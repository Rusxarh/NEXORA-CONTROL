"""
nexcam_telegram.py
Bot de Telegram de NEXCAM -- FASE 3.

Puerto a Python puro (sin dependencias nuevas: solo urllib/json de la
librería estándar) de NEXCAM/mock-api/telegram.js, leído completo antes de
escribir este archivo. Mismo comportamiento: teclado de botones, avisos al
instante (placa en lista, posible coincidencia, "ya la viste", teléfono
nuevo/revocado), cámara en vivo con mensaje que se edita solo, vinculación
por código de 6 dígitos, carga de lista por texto o archivo .csv/.txt.

Diferencias deliberadas frente al mock (decisiones de esta integración,
para revisar):
- La vinculación de un chat intenta resolver un usuario real de NEXORA
  (usuarios.telegram_id == chat_id). Si no hay uno, el chat queda
  vinculado pero "sin usuario": /agregar, /quitar y /cargar piden que un
  Admin le asigne un usuario desde el panel (pestaña Telegram) antes de
  permitir escribir en la base -- en NEXORA toda alta/baja exige un
  usuario_id real de auditoría, no uno inventado. Buscar/Estado/Últimas
  siguen funcionando sin usuario asignado.
- "/agregar" sobre una placa que ya existe NO la reactiva en silencio
  (el mock sí lo hace vía upsert): aquí se informa que ya existe. Reactivar
  una placa dada de baja sigue siendo una acción deliberada del panel, no
  algo que un comando de Telegram deba poder hacer solo.
- Los eventos de `eventos_notificacion` (CAMERA_WARNING/CAMERA_SUSPENDED/
  CAMERA_REACTIVATED/USER_DEACTIVATED/USER_REACTIVATED) se avisan SOLO a
  chats vinculados a un usuario Admin activo (nunca a Operador ni a chats
  sin usuario), consumidos por un hilo propio cada 60 s.
"""

import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import database as db
import nexcam_db

CONFIG_PATH = Path(__file__).parent / "telegram.json"
EDIT_SEGUNDOS = 3
VENTANA_SIN_SENAL_SEGUNDOS = 10 * 60
VENTANA_EVENTOS_SEGUNDOS = 60

ICONO = {"exacta": "🚨", "probable": "⚠️"}

B = {
    "estado": "📊 Estado", "ultimas": "🕒 Últimas", "buscar": "🔍 Buscar placa",
    "cargar": "📥 Cargar lista", "agregar": "➕ Agregar placa", "quitar": "➖ Quitar placa",
    "vivo": "📷 En vivo", "avisos": "🔔 Avisos", "silencio": "🔕 Silencio", "ayuda": "❓ Ayuda",
}

COMANDOS = [
    ("menu", "Mostrar los botones"), ("estado", "Teléfonos, cámaras activas y lecturas de hoy"),
    ("ultimas", "Las últimas placas leídas"), ("buscar", "Ficha de una placa: datos, fotos y dónde se vio"),
    ("cargar", "Cargar la lista del día"), ("agregar", "Agregar una placa a la lista"),
    ("quitar", "Quitar una placa de la lista"), ("envivo", "Ver o no las cámaras en vivo"),
    ("modo", "Avisar solo alertas o todo"), ("silencio", "Pausar o reanudar los avisos"),
]


def _esc(t) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_placa(p: str = "") -> str:
    return f"{p[:3]} {p[3:]}" if len(p or "") >= 6 else (p or "")


def _hora(ts_ms: Optional[int] = None) -> str:
    momento = datetime.fromtimestamp((ts_ms or int(time.time() * 1000)) / 1000)
    return momento.strftime("%I:%M %p, %d %b").lstrip("0").replace(" 0", " ")


def _reloj(ts_ms: Optional[int] = None) -> str:
    momento = datetime.fromtimestamp((ts_ms or int(time.time() * 1000)) / 1000)
    return momento.strftime("%I:%M %p").lstrip("0")


def _duracion(ms: float) -> str:
    minutos = round(ms / 60000)
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60} min"


def _url_mapa(lat, lng) -> str:
    return f"https://maps.google.com/?q={lat},{lng}"


def _tiene_gps(v: dict) -> bool:
    return bool(v) and v.get("lat") is not None and v.get("lng") is not None


def _vehiculo_txt(v: Optional[dict]) -> str:
    if not v:
        return ""
    return ", ".join(x for x in (v.get("marca"), v.get("linea"), v.get("color"), v.get("modelo")) if x)


PATRON_AGREGAR = re.compile(r"^\s*([A-Za-z]{3})[\s-]?(\d{3})\b\s*(.*)$")
PATRON_PLACA_SOLA = re.compile(r"^[A-Za-z]{3}[\s-]?\d{3}$")


class BotTelegram:
    def __init__(self, config_path: Optional[Path] = None, log=print):
        self.config_path = config_path or CONFIG_PATH
        self.log = log
        self.cfg = {"token": None, "chats": [], "modo": "alertas", "silencio": False, "en_vivo": True}
        try:
            with open(self.config_path, "r", encoding="utf-8-sig") as f:
                self.cfg.update(json.load(f))
        except Exception:
            pass

        if os.environ.get("TELEGRAM_TOKEN"):
            self.cfg["token"] = os.environ["TELEGRAM_TOKEN"]
        self.api = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
        token = self.cfg.get("token") or ""
        self.activo = bool(re.match(r"^\d+:[\w-]{20,}$", token))

        self.codigo = str(secrets.randbelow(900000) + 100000)
        self.offset = 0
        self.detenido = False
        self.bot_nombre = None
        self.esperando = {}   # chat_id -> {"que": ..., "hasta": datetime}
        self.sesiones = {}    # device_uuid -> sesión de cámara en vivo
        self._lock = threading.Lock()
        self._hilo_bucle = None
        self._hilo_eventos = None

    # ------------------------------------------------------------ config

    def _guardar(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Telegram: no se pudo guardar telegram.json: {e}")

    def _chat(self, chat_id) -> Optional[dict]:
        return next((c for c in self.cfg["chats"] if c["id"] == chat_id), None)

    def _autorizado(self, chat_id) -> bool:
        return self._chat(chat_id) is not None

    def _usuario_de_chat(self, chat_id) -> Optional[dict]:
        c = self._chat(chat_id)
        if not c or not c.get("usuario_id"):
            return None
        return db.obtener_usuario_por_id(c["usuario_id"])

    def _chats_admin(self) -> list:
        """Chats vinculados a un usuario Admin activo (para eventos sensibles)."""
        resultado = []
        for c in self.cfg["chats"]:
            u = db.obtener_usuario_por_id(c["usuario_id"]) if c.get("usuario_id") else None
            if u and u["rol"] == "Admin" and u["estado"] == "activo":
                resultado.append(c)
        return resultado

    # ------------------------------------------------------------ llamadas a Telegram

    def _llamar(self, metodo: str, cuerpo: dict) -> dict:
        datos = json.dumps(cuerpo).encode("utf-8")
        peticion = urllib.request.Request(
            f"{self.api}/bot{self.cfg['token']}/{metodo}", data=datos,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(peticion, timeout=35) as r:
            j = json.loads(r.read().decode("utf-8"))
        if not j.get("ok"):
            raise RuntimeError(f"{metodo}: {j.get('description', 'error')}")
        return j.get("result")

    def _enviar_foto(self, chat_id, ruta_archivo: Path, caption: str, extra: dict = None):
        boundary = f"----nexora{secrets.token_hex(8)}"
        partes = []

        def campo(nombre, valor):
            partes.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{nombre}\"\r\n\r\n{valor}\r\n".encode("utf-8")
            )

        campo("chat_id", chat_id)
        campo("caption", caption[:1024])
        campo("parse_mode", "HTML")
        if extra and extra.get("reply_markup"):
            campo("reply_markup", json.dumps(extra["reply_markup"]))
        contenido = ruta_archivo.read_bytes()
        tipo = mimetypes.guess_type(str(ruta_archivo))[0] or "image/jpeg"
        partes.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{ruta_archivo.name}\"\r\n"
             f"Content-Type: {tipo}\r\n\r\n").encode("utf-8")
        )
        partes.append(contenido)
        partes.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        cuerpo = b"".join(partes)

        peticion = urllib.request.Request(
            f"{self.api}/bot{self.cfg['token']}/sendPhoto", data=cuerpo,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
        )
        with urllib.request.urlopen(peticion, timeout=35) as r:
            j = json.loads(r.read().decode("utf-8"))
        if not j.get("ok"):
            raise RuntimeError(f"sendPhoto: {j.get('description', 'error')}")
        return j.get("result")

    def _texto(self, chat_id, text, extra: dict = None):
        cuerpo = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        cuerpo.update(extra or {})
        return self._llamar("sendMessage", cuerpo)

    def _enviar(self, chat_id, text, foto: Optional[Path] = None, extra: dict = None):
        if foto:
            try:
                return self._enviar_foto(chat_id, foto, text, extra)
            except Exception as e:
                self.log(f"Telegram: no se pudo enviar la foto - {e}")
        return self._texto(chat_id, text, extra)

    def _a_todos(self, text, foto: Optional[Path] = None, extra: dict = None, ubicacion: dict = None,
                 aun_en_silencio: bool = False, chats: Optional[list] = None) -> list:
        if not self.activo or (self.cfg["silencio"] and not aun_en_silencio):
            return []
        destino = chats if chats is not None else self.cfg["chats"]
        if not destino:
            return []
        enviados = []
        for c in destino:
            try:
                m = self._enviar(c["id"], text, foto, extra)
                enviados.append({"chat_id": c["id"], "message_id": m.get("message_id") if m else None})
                if ubicacion:
                    self._llamar("sendLocation", {"chat_id": c["id"], "latitude": ubicacion["lat"], "longitude": ubicacion["lng"]})
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar a {c.get('nombre') or c['id']} - {e}")
        return enviados

    # ------------------------------------------------------------ teclados

    def _menu(self) -> dict:
        return {
            "keyboard": [
                [{"text": B["estado"]}, {"text": B["ultimas"]}],
                [{"text": B["buscar"]}, {"text": B["cargar"]}],
                [{"text": B["agregar"]}, {"text": B["quitar"]}],
                [{"text": f"{B['vivo']}: {'sí' if self.cfg['en_vivo'] else 'no'}"},
                 {"text": f"{B['avisos']}: {'todo' if self.cfg['modo'] == 'todo' else 'alertas'}"}],
                [{"text": "🔔 Reanudar avisos" if self.cfg["silencio"] else B["silencio"]}, {"text": B["ayuda"]}],
            ],
            "resize_keyboard": True, "is_persistent": True,
            "input_field_placeholder": "Elige una opción o escribe una placa",
        }

    def _con_menu(self, extra: dict = None) -> dict:
        return {**(extra or {}), "reply_markup": self._menu()}

    def _botones_vista(self, v: dict) -> dict:
        fila = [{"text": "📋 Ficha", "callback_data": f"ficha:{v['placa']}"}]
        if v.get("snapshot") and v.get("vehiculo"):
            fila.append({"text": "🔢 Foto de la placa", "callback_data": f"placa:{v['id']}"})
        if _tiene_gps(v):
            fila.append({"text": "🗺️ Mapa", "url": _url_mapa(v["lat"], v["lng"])})
        return {"inline_keyboard": [fila]}

    def _foto_principal(self, v: dict) -> Optional[Path]:
        if not v:
            return None
        return nexcam_db.ruta_foto_segura(v.get("vehiculo") or "") or nexcam_db.ruta_foto_segura(v.get("snapshot") or "")

    # ------------------------------------------------------------ ayuda

    def _ayuda(self) -> str:
        return "\n".join([
            "<b>NEXCAM</b>: te aviso lo que ven las cámaras. Usa los botones de abajo o escribe:", "",
            f"<b>{B['estado']}</b>: teléfonos, cámaras activas y lecturas de hoy",
            f"<b>{B['ultimas']}</b>: las últimas placas leídas",
            f"<b>{B['buscar']}</b>: datos, fotos y lugares donde se vio una placa",
            f"<b>{B['cargar']}</b>: pega tu lista del día (una placa por línea) o envíame un .csv",
            f"<b>{B['agregar']}</b> / <b>{B['quitar']}</b>: una placa a la vez",
            f"<b>{B['vivo']}</b>: ver lo que lee cada cámara mientras está encendida",
            f"<b>{B['avisos']}</b>: solo alertas, o también cada placa leída",
            f"<b>{B['silencio']}</b>: pausa o reanuda todos los avisos",
        ])

    # ------------------------------------------------------------ textos

    def _detalle_deteccion(self, d: dict) -> str:
        lineas = []
        if d.get("motivo"):
            lineas.append(f"Motivo: {_esc(d['motivo'])}")
        lineas.append(f"Operador: {_esc(d.get('operador') or '—')}")
        conf = f"{round((d.get('confidence') or 0) * 100)}%" if d.get("confidence") is not None else "—"
        lineas.append(f"Confianza: {conf}")
        lineas.append(f"Hora: {_hora(d.get('ts'))}")
        return "\n".join(lineas)

    def _texto_ya_vista(self, h: dict, titulo: str) -> str:
        ult = h["vistas"][0]
        lineas = [titulo]
        if h["tipo"] == "probable":
            lineas.append(f"La cámara leyó <b>{_esc(ult.get('leida') or ult.get('placa'))}</b>, casi igual. Compara la foto antes de actuar.")
        if h.get("motivo"):
            lineas.append(f"Motivo: {_esc(h['motivo'])}")
        veh = _vehiculo_txt(h)
        if veh:
            lineas.append(f"En tu lista: {_esc(veh)}")

        def donde(v):
            return f'<a href="{_url_mapa(v["lat"], v["lng"])}">mapa</a>' if _tiene_gps(v) else "sin ubicación"

        n = len(h["vistas"])
        lineas.append(f"Vista {'1 vez' if n == 1 else f'{n} veces'}. Última: {_hora(ult.get('ts'))}, {_esc(ult.get('operador') or '')}, {donde(ult)}")
        antes = [f"• {_hora(v.get('ts'))}, {donde(v)}" for v in h["vistas"][1:6]]
        if antes:
            lineas.append("Antes:")
            lineas.extend(antes)
        if n > 6:
            lineas.append(f"…y {n - 6} más (ver en el panel).")
        return "\n".join(lineas)

    def _texto_estado(self) -> str:
        dispositivos = nexcam_db.listar_dispositivos()
        autorizados = [d for d in dispositivos if d["authorized"]]
        en_linea = [d for d in autorizados if d["online"]]
        lineas = [
            f"<b>Estado de NEXCAM</b> ({_hora()})",
            f"Teléfonos en línea: {len(en_linea)} de {len(autorizados)}",
            f"Cámaras encendidas: {len(self.sesiones)}",
        ]
        for d in en_linea:
            cam = " 📷" if d["device_uuid"] in self.sesiones else ""
            modelo = f"{d.get('brand') or ''} {d.get('model') or ''}".strip()
            usuario_txt = f", {_esc(d['user_name'])}" if d.get("user_name") else ""
            lineas.append(f"• {_esc(modelo)}{usuario_txt}{cam}")
        lineas.append(f"Avisos: {'en silencio' if self.cfg['silencio'] else ('todo' if self.cfg['modo'] == 'todo' else 'solo alertas')}. En vivo: {'sí' if self.cfg['en_vivo'] else 'no'}.")
        return "\n".join(lineas)

    # ------------------------------------------------------------ cámara en vivo

    def _texto_sesion(self, s: dict, final: Optional[str] = None) -> str:
        en_lista = sum(1 for l in s["lecturas"] if l.get("tipo"))
        if final:
            motivo = "Se perdió la señal del teléfono." if final == "sin-senal" else ""
            cab = (f"⏹️ <b>Cámara apagada</b>: {_esc(s['device'])}\n"
                   f"{_reloj(s['inicio'])} a {_reloj(s['fin'])} ({_duracion(s['fin'] - s['inicio'])}). {motivo}")
        else:
            cab = f"📷 <b>Cámara encendida</b>: {_esc(s['device'])}\nDesde las {_reloj(s['inicio'])}. Se actualiza solo."
        cuenta = f"Placas leídas: <b>{s['total']}</b>" + (f", <b>{en_lista} en lista</b>" if en_lista else "")
        if not s["lecturas"]:
            return f"{cab}\n{cuenta}\n\n{'No leyó ninguna placa.' if final else 'Esperando placas…'}"
        filas = []
        for l in s["lecturas"][:20]:
            marca = ICONO.get(l.get("tipo"), "▫️")
            extra = f" (leyó {_esc(l['leida'])})" if l.get("tipo") == "probable" else (f" {_esc(l.get('motivo') or 'en lista')}" if l.get("tipo") else "")
            filas.append(f"{marca} {_reloj(l['ts'])}  <b>{_esc(_fmt_placa(l['placa']))}</b>{extra}")
        mas = f"\n…y {len(s['lecturas']) - 20} más antes." if len(s["lecturas"]) > 20 else ""
        return f"{cab}\n{cuenta}\n\n{chr(10).join(filas)}{mas}"

    def _abrir_sesion(self, device_uuid, device, operador, ts) -> Optional[dict]:
        with self._lock:
            if device_uuid in self.sesiones or not self.cfg["en_vivo"]:
                return self.sesiones.get(device_uuid)
            s = {"device_uuid": device_uuid, "device": device or "Teléfono", "operador": operador,
                 "inicio": ts or int(time.time() * 1000), "ultima": time.time(), "lecturas": [], "total": 0,
                 "mensajes": [], "timer": None}
            self.sesiones[device_uuid] = s
        s["mensajes"] = self._a_todos(self._texto_sesion(s))
        return s

    def _refrescar_sesion(self, s: dict, final: Optional[str] = None):
        if s.get("timer"):
            s["timer"].cancel()
            s["timer"] = None
        texto = self._texto_sesion(s, final)
        for m in s["mensajes"]:
            if not m.get("message_id"):
                continue
            try:
                self._llamar("editMessageText", {
                    "chat_id": m["chat_id"], "message_id": m["message_id"], "text": texto,
                    "parse_mode": "HTML", "disable_web_page_preview": True,
                })
            except Exception as e:
                if "not modified" not in str(e).lower():
                    self.log(f"Telegram: no se pudo actualizar la cámara en vivo - {e}")

    def _cerrar_sesion(self, uuid, motivo="apagada"):
        with self._lock:
            s = self.sesiones.pop(uuid, None)
        if not s:
            return
        s["fin"] = int(time.time() * 1000)
        self._refrescar_sesion(s, motivo)

    def _lectura_en_vivo(self, p: dict):
        if not self.cfg["en_vivo"]:
            return
        s = self.sesiones.get(p["device_uuid"])
        if not s and (time.time() * 1000 - (p.get("ts") or 0)) > VENTANA_SIN_SENAL_SEGUNDOS * 1000:
            return
        if not s:
            s = self._abrir_sesion(p["device_uuid"], p.get("device"), p.get("operador"), p.get("ts"))
        if not s:
            return
        s["ultima"] = time.time()
        s["total"] += 1
        s["lecturas"].insert(0, {"ts": p.get("ts"), "placa": p["placa"], "leida": p.get("leida"),
                                  "motivo": p.get("motivo"), "tipo": p.get("match_type") or ("exacta" if p.get("en_lista") else None)})
        s["lecturas"] = s["lecturas"][:200]
        if not s.get("timer"):
            s["timer"] = threading.Timer(EDIT_SEGUNDOS, lambda: self._refrescar_sesion(s))
            s["timer"].daemon = True
            s["timer"].start()

    def _vigilar_sesiones_inactivas(self):
        while not self.detenido:
            time.sleep(60)
            for uuid, s in list(self.sesiones.items()):
                if time.time() - s["ultima"] > VENTANA_SIN_SENAL_SEGUNDOS:
                    self._cerrar_sesion(uuid, "sin-senal")

    # ------------------------------------------------------------ eventos del servidor -> avisos

    def on_evento(self, evento: str, p: dict):
        try:
            self._on_evento(evento, p)
        except Exception as e:
            self.log(f"Telegram: error procesando evento {evento}: {e}")

    def _on_evento(self, evento: str, p: dict):
        if evento == "camera.started":
            self._abrir_sesion(p.get("device_uuid"), p.get("device"), p.get("operador"), int(time.time() * 1000))
            return
        if evento == "camera.stopped":
            self._cerrar_sesion(p.get("device_uuid"))
            return
        if evento == "connectivity":
            s = self.sesiones.get(p.get("device_uuid"))
            if s:
                s["ultima"] = time.time()
            return
        if evento == "retro.hits":
            hits = p.get("hits") or []
            for h in hits[:8]:
                titulo = (f"🗂️ <b>YA LA VISTE: {_esc(_fmt_placa(h['placa']))}</b> (recién cargada)" if h["tipo"] == "exacta"
                          else f"⚠️ <b>POSIBLEMENTE VISTA: {_esc(_fmt_placa(h['placa']))}</b> (recién cargada)")
                v0 = {**h["vistas"][0], "placa": h["placa"]}
                self._a_todos(self._texto_ya_vista(h, titulo), foto=self._foto_principal(v0), extra={"reply_markup": self._botones_vista(v0)})
            if len(hits) > 8:
                resto = ", ".join(_esc(_fmt_placa(h["placa"])) for h in hits[8:])
                self._a_todos(f"Y otras {len(hits) - 8} placas de la carga ya se habían visto: {resto}. Míralas con {B['buscar']} o en el panel.")
            return
        if evento == "detection.hit":
            self._lectura_en_vivo(p)
            ubicacion = {"lat": p["lat"], "lng": p["lng"]} if _tiene_gps(p) else None
            extra = {"reply_markup": self._botones_vista(p)}
            if p.get("match_type") == "probable":
                t = (f"⚠️ <b>Posible coincidencia: {_esc(_fmt_placa(p['placa']))}</b>\n"
                     f"La cámara leyó {_esc(p.get('leida'))}. Verifica la placa antes de actuar.\n{self._detalle_deteccion(p)}")
                self._a_todos(t, foto=self._foto_principal(p), extra=extra, ubicacion=ubicacion)
                return
            if p.get("en_lista") or p.get("match_type") == "exacta":
                t = f"🚨 <b>PLACA EN LISTA: {_esc(_fmt_placa(p['placa']))}</b>\n{self._detalle_deteccion(p)}"
                self._a_todos(t, foto=self._foto_principal(p), extra=extra, ubicacion=ubicacion)
                return
            if self.cfg["modo"] == "todo" and not self.cfg["en_vivo"]:
                self._a_todos(f"▫️ {_esc(_fmt_placa(p['placa']))} leída. No está en la lista.\n{_esc(p.get('operador') or '')}, {_hora(p.get('ts'))}")
            return
        if evento == "device.new":
            modelo = f"{p.get('brand') or ''} {p.get('model') or ''}".strip() or "Sin modelo"
            t = (f"📱 <b>Teléfono nuevo esperando autorización</b>\n"
                 f"{_esc(modelo)}\n"
                 f"ID: <code>{_esc(p['device_uuid'])}</code>")
            self._a_todos(t, aun_en_silencio=True, chats=self._chats_admin(),
                           extra={"reply_markup": {"inline_keyboard": [[{"text": "✅ Autorizar", "callback_data": f"auth:{p['device_uuid']}"}]]}})
            return
        if evento == "device.revoked":
            self._cerrar_sesion(p.get("device_uuid"))
            self._a_todos(f"⛔ Se revocó el teléfono <code>{_esc(p['device_uuid'])}</code>. Su sesión se cerró y sus datos locales se borran.")
            return

    # ------------------------------------------------------------ acciones del usuario

    def _buscar(self, chat_id, placa_raw: str):
        placa = nexcam_db.normalizar_placa(placa_raw)
        if not nexcam_db.RE_PLACA_NEXCAM.match(placa):
            return self._texto(chat_id, "Escribe una placa de 3 letras y 3 números, como ABC123.", self._con_menu())
        lista = db.buscar_placa(placa)
        vistas = nexcam_db.vistas_de([placa]).get(placa)
        en_lista_txt = (
            f"En la lista: {_esc(lista['observaciones'] or 'sin motivo')}" if lista and lista["estado"] == "activo"
            else "Estuvo en la lista (dada de baja)." if lista else "No está en la lista."
        )
        cab = [f"<b>{_esc(_fmt_placa(placa))}</b>", en_lista_txt]
        veh = _vehiculo_txt(lista)
        if veh:
            cab.append(f"Vehículo: {_esc(veh)}")
        if not vistas or not vistas["vistas"]:
            return self._texto(chat_id, "\n".join(cab + ["Ninguna cámara la ha visto."]))
        v0 = {**vistas["vistas"][0], "placa": placa}
        h = {**(lista or {}), "placa": placa, "tipo": vistas["tipo"], "vistas": vistas["vistas"], "motivo": lista["observaciones"] if lista else None}
        return self._enviar(chat_id, self._texto_ya_vista(h, "\n".join(cab)), foto=self._foto_principal(v0), extra={"reply_markup": self._botones_vista(v0)})

    def _resumen_carga(self, resumen: dict) -> str:
        lineas = [f"📥 <b>Lista cargada</b>: {len(resumen['insertadas']) + len(resumen['duplicadas'])} placas "
                  f"({len(resumen['insertadas'])} nuevas, {len(resumen['duplicadas'])} ya existían)."]
        if resumen["invalidas"]:
            muestra = ", ".join(f"<code>{_esc(x[:30])}</code>" for x in resumen["invalidas"][:5])
            lineas.append(f"No entendí {len(resumen['invalidas'])} línea(s): {muestra}{'…' if len(resumen['invalidas']) > 5 else ''}")
        ya_vistas = resumen.get("ya_vistas") or {}
        lineas.append(f"🗂️ {len(ya_vistas)} ya se habían visto. Te las mando ahora con foto y lugar." if ya_vistas
                      else "Ninguna se había visto antes. Te aviso apenas una cámara las vea.")
        return "\n".join(lineas)

    def _cargar(self, chat_id, usuario_id: int, cuerpo: str):
        import vehiculos

        resumen = vehiculos.procesar_carga_masiva_texto(cuerpo, usuario_id)
        self._texto(chat_id, self._resumen_carga(resumen), self._con_menu())
        for h in list((resumen.get("ya_vistas") or {}).values())[:8]:
            v0 = {**h["vistas"][0], "placa": h["placa"]}
            titulo = f"🗂️ <b>YA LA VISTE: {_esc(_fmt_placa(h['placa']))}</b> (recién cargada)" if h["tipo"] == "exacta" else f"⚠️ <b>POSIBLEMENTE VISTA: {_esc(_fmt_placa(h['placa']))}</b>"
            self._enviar(chat_id, self._texto_ya_vista(h, titulo), foto=self._foto_principal(v0), extra={"reply_markup": self._botones_vista(v0)})

    def _agregar(self, chat_id, usuario_id: int, linea: str):
        m = PATRON_AGREGAR.match(linea or "")
        if not m:
            return self._texto(chat_id, "Formato no válido. Ejemplo: <code>ABC123 Aprehensión judicial</code> (3 letras y 3 números).")
        placa = (m.group(1) + m.group(2)).upper()
        motivo = m.group(3).strip() or None
        try:
            db.insertar_placa(placa, "carro", usuario_id, motivo)
        except db.PlacaDuplicadaError:
            return self._texto(chat_id, f"<b>{_esc(_fmt_placa(placa))}</b> ya está en el inventario (usa {B['buscar']} para verla).", self._con_menu())
        except db.ValorInvalidoError as e:
            return self._texto(chat_id, _esc(str(e)))
        vistas = nexcam_db.vistas_de([placa]).get(placa)
        extra_vista = " 🗂️ Ya la habían visto antes: usa 🔍 Buscar placa para ver dónde." if vistas and vistas["vistas"] else ""
        self._texto(chat_id, f"Agregada <b>{_esc(_fmt_placa(placa))}</b>{f': {_esc(motivo)}' if motivo else ''}. Los teléfonos la reciben en segundos.{extra_vista}", self._con_menu())

    def _quitar(self, chat_id, usuario_id: int, placa_raw: str):
        placa = nexcam_db.normalizar_placa(placa_raw or "")
        try:
            db.cambiar_estado_a_baja(placa, usuario_id, "Dada de baja por Telegram")
        except db.RegistroNoEncontradoError:
            return self._texto(chat_id, "Esa placa no está en la lista.", self._con_menu())
        except db.ValorInvalidoError as e:
            return self._texto(chat_id, _esc(str(e)))
        return self._texto(chat_id, f"Quitada <b>{_esc(_fmt_placa(placa))}</b> de la lista.", self._con_menu())

    def _ultimas(self, chat_id):
        ds = nexcam_db.avistamientos_recientes(10)
        if not ds:
            return self._texto(chat_id, "Todavía no hay placas leídas.")
        texto = "\n".join(
            f"{ICONO.get(d.get('match_type')) or ('🚨' if d['en_lista'] else '▫️')} {_hora(d['ts'])}  <b>{_esc(_fmt_placa(d['placa']))}</b>, {_esc(d.get('operador') or '')}"
            for d in ds
        )
        botones = [[{"text": f"📋 {_fmt_placa(d['placa'])}", "callback_data": f"ficha:{d['placa']}"}] for d in ds[:5]]
        return self._texto(chat_id, texto, {"reply_markup": {"inline_keyboard": botones}})

    PREGUNTA = {
        "buscar": "🔍 Escribe la placa que quieres buscar (por ejemplo ABC123).",
        "cargar": "📥 Pega la lista del día: una placa por línea, puede llevar el motivo al lado. También puedes enviarme un archivo .csv.",
        "agregar": "➕ Escribe la placa y el motivo, por ejemplo <code>ABC123 Aprehensión judicial</code>.",
        "quitar": "➖ Escribe la placa que quieres quitar de la lista.",
    }

    def _preguntar(self, chat_id, que):
        self.esperando[chat_id] = {"que": que, "hasta": datetime.now() + timedelta(minutes=5)}
        return self._texto(chat_id, self.PREGUNTA[que], {"reply_markup": {"force_reply": True}})

    def _requiere_usuario(self, chat_id) -> Optional[dict]:
        """Para /agregar, /quitar y /cargar: exige un usuario real de NEXORA
        vinculado a este chat (ver docstring del módulo)."""
        usuario = self._usuario_de_chat(chat_id)
        if usuario is None:
            self._texto(chat_id, "Este chat todavía no tiene un usuario de NEXORA asignado. Pídele a un Admin que te asigne uno desde el panel (NEXCAM → Telegram).", self._con_menu())
            return None
        return usuario

    def _vincular(self, chat_id, args: list, nombre: str):
        if args and args[0] == self.codigo:
            usuario = db.obtener_usuario_por_telegram_id(chat_id)
            self.cfg["chats"].append({"id": chat_id, "nombre": nombre, "desde": int(time.time() * 1000),
                                       "usuario_id": usuario["id"] if usuario else None})
            self._guardar()
            self.codigo = str(secrets.randbelow(900000) + 100000)
            self.log(f"Telegram: chat vinculado ({nombre}). Nuevo código: {self.codigo}")
            pendiente = "" if usuario else "\n\nTodavía no tienes un usuario de NEXORA asignado a este chat: pídele a un Admin que te lo asigne desde el panel (NEXCAM → Telegram) para poder cargar o agregar placas."
            return self._texto(chat_id, f"Listo, {_esc(nombre)}. Este chat recibirá los avisos de NEXCAM.{pendiente}\n\n{self._ayuda()}", self._con_menu())
        return self._texto(chat_id, "Este chat no está vinculado a NEXCAM. Escribe <code>/vincular</code> seguido del código que muestra el servidor al arrancar (o en el panel).")

    def _mensaje(self, chat_id, raw: str, nombre: str):
        txt = raw.strip()
        partes = txt.split()
        cmd_raw, args = (partes[0], partes[1:]) if partes else ("", [])
        cmd = re.sub(r"@.*$", "", cmd_raw.lower())
        if not self._autorizado(chat_id):
            return self._vincular(chat_id, args if cmd == "/vincular" else [], nombre)

        espera = self.esperando.get(chat_id)
        es_boton = any(txt.startswith(b) for b in B.values()) or txt.startswith("🔔 Reanudar")
        if espera and not txt.startswith("/") and not es_boton:
            del self.esperando[chat_id]
            if datetime.now() < espera["hasta"]:
                if espera["que"] == "buscar":
                    return self._buscar(chat_id, txt)
                usuario = self._requiere_usuario(chat_id)
                if not usuario:
                    return None
                if espera["que"] == "cargar":
                    return self._cargar(chat_id, usuario["id"], txt)
                if espera["que"] == "agregar":
                    return self._agregar(chat_id, usuario["id"], txt)
                if espera["que"] == "quitar":
                    return self._quitar(chat_id, usuario["id"], txt)
        elif espera and (txt.startswith("/") or es_boton):
            del self.esperando[chat_id]

        if txt == B["estado"] or cmd == "/estado":
            return self._texto(chat_id, self._texto_estado(), self._con_menu())
        if txt == B["ultimas"] or cmd == "/ultimas":
            return self._ultimas(chat_id)
        if txt == B["buscar"] or (cmd == "/buscar" and not args):
            return self._preguntar(chat_id, "buscar")
        if cmd == "/buscar":
            return self._buscar(chat_id, "".join(args))
        if txt == B["cargar"] or (cmd == "/cargar" and not re.sub(r"^/cargar(@\S+)?", "", txt).strip()):
            return self._preguntar(chat_id, "cargar")
        if cmd == "/cargar":
            usuario = self._requiere_usuario(chat_id)
            return self._cargar(chat_id, usuario["id"], re.sub(r"^/cargar(@\S+)?", "", txt).strip()) if usuario else None
        if txt == B["agregar"] or (cmd == "/agregar" and not args):
            return self._preguntar(chat_id, "agregar")
        if cmd == "/agregar":
            usuario = self._requiere_usuario(chat_id)
            return self._agregar(chat_id, usuario["id"], " ".join(args)) if usuario else None
        if txt == B["quitar"] or (cmd == "/quitar" and not args):
            return self._preguntar(chat_id, "quitar")
        if cmd == "/quitar":
            usuario = self._requiere_usuario(chat_id)
            return self._quitar(chat_id, usuario["id"], args[0] if args else "") if usuario else None
        if txt.startswith(B["vivo"]) or cmd == "/envivo":
            self.cfg["en_vivo"] = not self.cfg["en_vivo"]
            self._guardar()
            if not self.cfg["en_vivo"]:
                for uuid in list(self.sesiones.keys()):
                    self._cerrar_sesion(uuid)
            return self._texto(chat_id, "📷 En vivo activado." if self.cfg["en_vivo"] else "📷 En vivo desactivado. Sigues recibiendo las alertas.", self._con_menu())
        if txt.startswith(B["avisos"]) or cmd == "/modo":
            pedido = (args[0].lower() if args else "")
            self.cfg["modo"] = pedido if pedido in ("todo", "alertas") else ("alertas" if self.cfg["modo"] == "todo" else "todo")
            self._guardar()
            return self._texto(chat_id, "🔔 Avisos: todo." if self.cfg["modo"] == "todo" else "🔔 Avisos: solo alertas.", self._con_menu())
        if txt == B["silencio"] or txt.startswith("🔔 Reanudar") or cmd == "/silencio":
            self.cfg["silencio"] = not self.cfg["silencio"]
            self._guardar()
            return self._texto(chat_id, "🔕 Avisos en pausa." if self.cfg["silencio"] else "🔔 Avisos reanudados.", self._con_menu())
        if txt == B["ayuda"] or cmd in ("/start", "/menu", "/ayuda", "/help"):
            return self._texto(chat_id, self._ayuda(), self._con_menu())
        if cmd == "/vincular":
            return self._texto(chat_id, "Este chat ya está vinculado.", self._con_menu())
        if PATRON_PLACA_SOLA.match(txt):
            return self._buscar(chat_id, txt)
        return self._texto(chat_id, "No entendí. Usa los botones de abajo, o escribe una placa para buscarla.", self._con_menu())

    def _documento(self, chat_id, doc: dict):
        self.esperando.pop(chat_id, None)
        nombre = doc.get("file_name") or ""
        if not re.search(r"\.(csv|txt)$", nombre, re.I):
            return self._texto(chat_id, "Envíame la lista como .csv o .txt. Desde Excel: Archivo → Guardar como → CSV.")
        if (doc.get("file_size") or 0) > 5 * 1024 * 1024:
            return self._texto(chat_id, "El archivo es muy grande (máximo 5 MB).")
        usuario = self._requiere_usuario(chat_id)
        if not usuario:
            return None
        f = self._llamar("getFile", {"file_id": doc["file_id"]})
        with urllib.request.urlopen(f"{self.api}/file/bot{self.cfg['token']}/{f['file_path']}", timeout=35) as r:
            crudo = r.read()
        try:
            texto = crudo.decode("utf-8")
            if "�" in texto:
                texto = crudo.decode("latin-1")
        except UnicodeDecodeError:
            texto = crudo.decode("latin-1")
        return self._cargar(chat_id, usuario["id"], texto)

    def _boton_pulsado(self, q: dict):
        chat_id = (q.get("message") or {}).get("chat", {}).get("id")
        if not self._autorizado(chat_id):
            return self._llamar("answerCallbackQuery", {"callback_query_id": q["id"], "text": "Chat no vinculado"})
        datos = q.get("data") or ""
        i = datos.find(":")
        accion, valor = (datos[:i], datos[i + 1:]) if i >= 0 else (datos, "")

        def responder(texto=None):
            try:
                self._llamar("answerCallbackQuery", {"callback_query_id": q["id"], **({"text": texto} if texto else {})})
            except Exception:
                pass

        if accion == "auth":
            dev = nexcam_db.autorizar_dispositivo(valor)
            responder("Teléfono autorizado" if dev else "Ese teléfono ya no existe")
            if dev:
                modelo = f"{dev.get('brand') or ''} {dev.get('model') or ''}".strip()
                try:
                    self._llamar("editMessageText", {
                        "chat_id": chat_id, "message_id": q["message"]["message_id"], "parse_mode": "HTML",
                        "text": f"✅ <b>Teléfono autorizado</b>\n{_esc(modelo)}\nID: <code>{_esc(valor)}</code>",
                    })
                except Exception:
                    pass
            return
        if accion == "ficha":
            responder()
            return self._buscar(chat_id, valor)
        if accion == "placa":
            v = nexcam_db.obtener_avistamiento(int(valor)) if valor.isdigit() else None
            foto = nexcam_db.ruta_foto_segura(v["snapshot"]) if v else None
            responder("" if foto else "Esa foto ya no está disponible")
            if foto:
                return self._enviar_foto(chat_id, foto, f"🔢 Placa leída: <b>{_esc(_fmt_placa(v.get('leida') or v['placa']))}</b>, {_hora(v['ts'])}")

    # ------------------------------------------------------------ bucle de mensajes

    def _bucle(self):
        while not self.detenido:
            try:
                updates = self._llamar("getUpdates", {"offset": self.offset, "timeout": 30,
                                                        "allowed_updates": ["message", "callback_query"]})
                for u in updates:
                    self.offset = u["update_id"] + 1
                    try:
                        m = u.get("message")
                        if m and m.get("text"):
                            nombre = " ".join(x for x in ((m.get("from") or {}).get("first_name"), (m.get("from") or {}).get("last_name")) if x) or str(m["chat"]["id"])
                            self._mensaje(m["chat"]["id"], m["text"], nombre)
                        elif m and m.get("document"):
                            if not self._autorizado(m["chat"]["id"]):
                                self._vincular(m["chat"]["id"], [], "")
                            else:
                                self._documento(m["chat"]["id"], m["document"])
                        elif u.get("callback_query"):
                            self._boton_pulsado(u["callback_query"])
                    except Exception as e:
                        self.log(f"Telegram: error atendiendo un mensaje: {e}")
            except Exception as e:
                if self.detenido:
                    return
                self.log(f"Telegram: sin conexión con Telegram, reintento en 5 s: {e}")
                time.sleep(5)

    # ------------------------------------------------------------ eventos de NEXORA (CAMARA...)

    def _vigilar_eventos_notificacion(self):
        """Consumidor de `eventos_notificacion` (CAMERA_WARNING/SUSPENDED/
        REACTIVATED, USER_DEACTIVATED/REACTIVATED): solo a chats de Admin."""
        TEXTOS = {
            "CAMERA_WARNING": "⚠️ El servicio CAMARA de {nombre} entra en alerta: si no hay actividad antes del corte, quedará suspendido.",
            "CAMERA_SUSPENDED": "⛔ El servicio CAMARA de {nombre} quedó SUSPENDIDO. Solo un Admin puede reactivarlo desde el panel.",
            "CAMERA_REACTIVATED": "✅ El servicio CAMARA de {nombre} volvió a estar ACTIVA.",
            "USER_DEACTIVATED": "🚫 El usuario {nombre} fue desactivado.",
            "USER_REACTIVATED": "✅ El usuario {nombre} fue reactivado.",
        }
        while not self.detenido:
            time.sleep(VENTANA_EVENTOS_SEGUNDOS)
            try:
                chats_admin = self._chats_admin()
                for evento in db.listar_eventos_pendientes():
                    plantilla = TEXTOS.get(evento["tipo_evento"])
                    if plantilla:
                        usuario = db.obtener_usuario_por_id(evento["usuario_id"])
                        nombre = usuario["nombre"] if usuario else f"id={evento['usuario_id']}"
                        self._a_todos(plantilla.format(nombre=_esc(nombre)), aun_en_silencio=True, chats=chats_admin)
                    db.marcar_evento_entregado(evento["id"])
            except Exception as e:
                self.log(f"Telegram: error en el vigilante de eventos_notificacion: {e}")

    # ------------------------------------------------------------ ciclo de vida

    def info(self) -> dict:
        chats = []
        for c in self.cfg["chats"]:
            usuario = db.obtener_usuario_por_id(c["usuario_id"]) if c.get("usuario_id") else None
            chats.append({
                "id": c["id"], "nombre": c["nombre"], "usuario_id": c.get("usuario_id"),
                "usuario_nombre": usuario["nombre"] if usuario else None,
                "usuario_rol": usuario["rol"] if usuario else None,
            })
        return {
            "activo": self.activo, "bot": self.bot_nombre, "codigo": self.codigo if self.activo else None,
            "modo": self.cfg["modo"], "silencio": self.cfg["silencio"], "en_vivo": self.cfg["en_vivo"],
            "chats": chats, "camaras": len(self.sesiones),
        }

    def asignar_usuario_a_chat(self, chat_id: int, usuario_id: Optional[int]) -> bool:
        c = self._chat(chat_id)
        if not c:
            return False
        c["usuario_id"] = usuario_id
        self._guardar()
        return True

    def arrancar(self):
        if not self.activo:
            motivo = "el token de telegram.json no tiene el formato de @BotFather (123456789:ABC...)." if self.cfg.get("token") else "desactivado (crea telegram.json con tu token, o la variable TELEGRAM_TOKEN, para activarlo)."
            self.log(f"  Telegram: {motivo}")
            return

        import nexcam_api

        nexcam_api.suscribir(self.on_evento)
        try:
            yo = self._llamar("getMe", {})
            self.bot_nombre = yo.get("username")
            self.log(f"  Telegram: bot @{self.bot_nombre} activo, {len(self.cfg['chats'])} chat(s) vinculado(s).")
            try:
                self._llamar("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMANDOS]})
                self._llamar("setMyShortDescription", {"short_description": "Avisos de las cámaras NEXCAM: placas en lista, fotos y ubicación."})
                self._llamar("setMyDescription", {"description": "NEXCAM te avisa cuando una cámara lee una placa de tu lista, con foto del vehículo y ubicación. Muestra en vivo lo que lee cada cámara y te deja cargar la lista del día. Solo responde a chats vinculados."})
            except Exception:
                pass
        except Exception as e:
            self.log(f"  Telegram: el token no funciona o no hay internet: {e}")

        self.log(f"  Telegram: para vincular un chat, escríbele al bot  /vincular {self.codigo}")
        self._hilo_bucle = threading.Thread(target=self._bucle, daemon=True, name="nexcam-telegram-bucle")
        self._hilo_bucle.start()
        self._hilo_eventos = threading.Thread(target=self._vigilar_eventos_notificacion, daemon=True, name="nexcam-telegram-eventos")
        self._hilo_eventos.start()
        self._hilo_inactivas = threading.Thread(target=self._vigilar_sesiones_inactivas, daemon=True, name="nexcam-telegram-inactivas")
        self._hilo_inactivas.start()

    def detener(self):
        self.detenido = True


_instancia: Optional[BotTelegram] = None


def iniciar(config_path: Optional[Path] = None, log=print) -> BotTelegram:
    # `config_path` se resuelve aquí (no como valor por defecto del
    # parámetro) para que main.py recoja en cada llamada el valor ACTUAL
    # de CONFIG_PATH, no el que tenía cuando este módulo se importó por
    # primera vez -- necesario para que las pruebas puedan redirigirlo
    # (ver tests/conftest.py) y para pruebas unitarias reales en general.
    global _instancia
    _instancia = BotTelegram(config_path or CONFIG_PATH, log)
    _instancia.arrancar()
    return _instancia


def instancia() -> Optional[BotTelegram]:
    return _instancia
