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

FASE 6 (RGM/Judicial reales, vía rgm_cola.py) + Prompt 5 punto 1: el RGM
AUTOMÁTICO (cámara/estudio) queda SILENCIOSO -- ya guardó el snapshot y
actualizó la ficha, no manda nada a Telegram (antes mandaba un mensaje
por cada detección de cámara). Solo una consulta PEDIDA explícitamente
(botón "🔎 RGM"/"⚖️ Procesos judiciales", o "Consultar" en la página
Consulta) manda un mensaje al terminar: a quien la pidió por Telegram
-- Prompt 7 bloque A: rastreado en la tabla rgm_avisos (database.py),
no en memoria, para sobrevivir a un reinicio de NEXORA entre el pedido
y el resultado --, o a los chats de Admin si fue desde la web. REGLA DE
SEGURIDAD (obligatoria): los datos del
deudor/garante de RGM y CUALQUIER dato de Judicial (radicado, procesos)
SOLO se muestran a chats vinculados a un usuario Admin activo; a un chat
de Operador o sin usuario asignado se le muestra únicamente el dato del
vehículo y "consulta hecha", nunca el resto. El botón "🔎 Consultar RGM"
(sobre cualquier ficha/alerta) sí está disponible para cualquier chat con
usuario asignado -- pedir la consulta no es sensible, el RESULTADO sí.
"""

import json
import mimetypes
import os
import queue
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import database as db
import nexcam_db
import telegram_http


def _pruebas_activas() -> bool:
    """True si estamos en la suite de pruebas o se pidió explícitamente no
    arrancar el bot real (NEXORA_PRUEBAS=1). Ver docstring de arrancar()."""
    return (
        os.environ.get("NEXORA_PRUEBAS") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or "pytest" in sys.modules
    )

CONFIG_PATH = Path(__file__).parent / "telegram.json"
EDIT_SEGUNDOS = 3
VENTANA_SIN_SENAL_SEGUNDOS = int(os.environ.get("TG_SIN_SENAL_MIN", "10")) * 60

# Prompt 2 (NEXCAM-PROMPTS-Y-PRUEBAS.md): una sola alerta por placa por
# sesión de cámara del teléfono, o por este tiempo si no hay sesión abierta
# (operador con "En vivo" apagado, o primera lectura antes de camera.started).
TG_REPETIR_MIN = int(os.environ.get("TG_REPETIR_MIN", "30"))

# Colombia no tiene horario de verano (fijo en UTC-5 desde 1993): se usa un
# offset fijo en vez de zoneinfo porque Windows no trae la base de datos de
# zonas horarias instalada por defecto (zoneinfo.ZoneInfo("America/Bogota")
# falla ahí) y evita sumar tzdata como dependencia nueva solo para esto.
BOGOTA = timezone(timedelta(hours=-5))
VENTANA_EVENTOS_SEGUNDOS = 60

ICONO = {"exacta": "🚨", "probable": "⚠️"}
# Prompt 5 punto 5: icono DISTINTO (y más chico) para la lista en vivo y
# "Últimas" -- las alertas siguen usando ICONO de arriba, sin cambios.
ICONO_VIVO = {"exacta": "🔴", "probable": "🟡"}

# Prompt 5 punto 5: solo las últimas N placas en "📷 Cámara encendida"
# (antes mostraba hasta 30, una lista larga es difícil de leer de un
# vistazo en la calle); la cabecera sigue contando el total de la sesión.
TG_VIVO_MAX = int(os.environ.get("TG_VIVO_MAX", "10"))
# Prompt 6 punto 3: "🕒 Últimas" EN VIVO -- un mensaje que se va editando
# solo con las últimas N placas (reemplaza la versión paginada de 10 del
# Prompt 5 punto 6); [◀ Anteriores] pagina el historial viejo de a
# TG_ULTIMAS_VIVO_PAGINA también. Se congela ("⏸ Ya no se actualiza") a
# las TG_ULTIMAS_VIVO_HORAS h, y nunca edita más de una vez cada
# TG_ULTIMAS_VIVO_EDIT_SEGUNDOS por mensaje (ver _tocar_ultimas_vivo).
TG_ULTIMAS_VIVO_PAGINA = 5
TG_ULTIMAS_VIVO_HORAS = 12
TG_ULTIMAS_VIVO_EDIT_SEGUNDOS = int(os.environ.get("TG_ULTIMAS_EDIT_SEGUNDOS", "3"))

B = {
    "estado": "📊 Estado", "ultimas": "🕒 Últimas", "buscar": "🔍 Buscar placa",
    "cargar": "📥 Cargar lista", "agregar": "➕ Agregar placa", "quitar": "➖ Quitar placa",
    "vivo": "📷 En vivo", "avisos": "🔔 Avisos", "silencio": "🔕 Silencio", "ayuda": "❓ Ayuda",
    "app": "📲 Instalar app",
}

COMANDOS = [
    ("menu", "Mostrar los botones"), ("estado", "Teléfonos, cámaras activas y lecturas de hoy"),
    ("ultimas", "Las últimas placas leídas"), ("buscar", "Ficha de una placa: datos, fotos y dónde se vio"),
    ("cargar", "Cargar la lista del día"), ("agregar", "Agregar una placa a la lista"),
    ("quitar", "Quitar una placa de la lista"), ("envivo", "Ver o no las cámaras en vivo"),
    ("modo", "Avisar solo alertas o todo"), ("silencio", "Pausar o reanudar los avisos"),
    ("app", "Recibir la app NEXCAM para instalar"),
]

# Porte del APK (ver mock-api/telegram.js, releído para esta sección):
# enlace fijo en telegram.json/"apkUrl" (re-leído del archivo en cada pedido,
# nunca requiere reiniciar) > NEXCAM_APK_URL > archivo local de la PC si pesa
# <= TELEGRAM_LIMITE_MB (50 por defecto, Telegram no deja enviar más) > enlace
# temporal de 24 h servido por NEXORA-CONTROL mismo (/app/nexcam.apk?k=...).
RUTA_APK_PREDETERMINADA = Path(r"C:\Users\Rus\Documents\NEXORA\NEXCAM\app\android\app\build\outputs\apk\release\app-release.apk")
LIMITE_TELEGRAM_BYTES = int(os.environ.get("TELEGRAM_LIMITE_MB", "50")) * 1024 * 1024
RUTA_PACKAGE_JSON_APP = Path(r"C:\Users\Rus\Documents\NEXORA\NEXCAM\app\package.json")

# token -> expira (epoch segundos); mismo mecanismo que enlacesApk en server.js.
_enlaces_apk_temporales: dict = {}


def ruta_apk() -> Path:
    return Path(os.environ.get("NEXCAM_APK", str(RUTA_APK_PREDETERMINADA)))


def crear_enlace_apk_temporal(base_url: str) -> str:
    token = secrets.token_hex(16)
    _enlaces_apk_temporales[token] = time.time() + 24 * 3600
    return f"{base_url.rstrip('/')}/app/nexcam.apk?k={token}"


def enlace_apk_temporal_vigente(token: str) -> bool:
    vence = _enlaces_apk_temporales.get(token)
    return bool(vence) and vence >= time.time()


def version_app() -> Optional[str]:
    try:
        with open(RUTA_PACKAGE_JSON_APP, "r", encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


def _esc(t) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_placa(p: str = "") -> str:
    return f"{p[:3]} {p[3:]}" if len(p or "") >= 6 else (p or "")


_BASE_SANS_BOLD_LETRA = 0x1D5D4   # MATHEMATICAL SANS-SERIF BOLD CAPITAL A
_BASE_SANS_BOLD_DIGITO = 0x1D7EC  # MATHEMATICAL SANS-SERIF BOLD DIGIT ZERO


def placa_destacada(texto: str) -> str:
    """
    Prompt 5 punto 5: la placa en letras Unicode "negrita sans"
    (𝗔-𝗭, 𝟬-𝟵) -- en Telegram se ven más gruesas y de otra fuente, la
    única forma de "agrandar" texto ahí sin HTML (Telegram no deja
    cambiar el tamaño de letra). Función ÚNICA, usada SOLO en la lista en
    vivo y en "Últimas" (listas rápidas de leer desde lejos/en la calle):
    en alertas y fichas la placa se queda en texto normal en negrilla
    HTML (<b>), porque estos caracteres especiales no son "letras" de
    verdad para copiar/pegar ni para que alguien la busque a mano.
    """
    letras = []
    for c in texto or "":
        if "A" <= c <= "Z":
            letras.append(chr(_BASE_SANS_BOLD_LETRA + (ord(c) - ord("A"))))
        elif "0" <= c <= "9":
            letras.append(chr(_BASE_SANS_BOLD_DIGITO + (ord(c) - ord("0"))))
        else:
            letras.append(c)
    return "".join(letras)


def _dt_bogota(ts_ms: Optional[int] = None) -> datetime:
    """`ts_ms` es epoch-ms (UTC, como lo manda la app): se convierte
    explícitamente a la hora de Colombia, sin depender de la zona horaria
    del sistema operativo (ver BOGOTA)."""
    return datetime.fromtimestamp((ts_ms or int(time.time() * 1000)) / 1000, tz=BOGOTA)


def _ms_de_texto_local(s: Optional[str]) -> Optional[int]:
    """Convierte una fecha guardada en la base (texto naive "AAAA-MM-DD
    HH:MM:SS", ya en hora de Colombia -- igual convención que el resto de
    NEXORA) a epoch-ms, para poder formatearla con _hora()/_reloj()."""
    if not s:
        return None
    try:
        momento = datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BOGOTA)
        return int(momento.timestamp() * 1000)
    except Exception:
        return None


def _meridiano(hora24: int) -> str:
    return "a. m." if hora24 < 12 else "p. m."


def _hm(momento: datetime) -> str:
    return f"{momento.hour % 12 or 12}:{momento.minute:02d}"


def _hora(ts_ms: Optional[int] = None) -> str:
    """Fecha y hora completas, formato colombiano: "17/11/2026, 9:21 p. m.\""""
    m = _dt_bogota(ts_ms)
    return f"{m:%d/%m/%Y}, {_hm(m)} {_meridiano(m.hour)}"


def _reloj(ts_ms: Optional[int] = None) -> str:
    """Solo la hora, con a. m./p. m.: "9:04 p. m.\""""
    m = _dt_bogota(ts_ms)
    return f"{_hm(m)} {_meridiano(m.hour)}"


def _reloj_corto(ts_ms: Optional[int] = None) -> str:
    """Solo la hora, sin a. m./p. m. (filas de la lista en vivo): "9:03\""""
    return _hm(_dt_bogota(ts_ms))


def _duracion(ms: float) -> str:
    minutos = round(ms / 60000)
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60} min"


def _url_mapa(lat, lng) -> str:
    return f"https://maps.google.com/?q={lat},{lng}"


def _tiene_gps(v: dict) -> bool:
    return bool(v) and v.get("lat") is not None and v.get("lng") is not None


def _titulo(texto: Optional[str]) -> Optional[str]:
    """Mayúscula inicial de cada palabra ("CHEVROLET TRACKER" ->
    "Chevrolet Tracker"): el RGM entrega marca/línea/tipo_servicio en
    mayúsculas y el Prompt 6 punto 1 pide no mostrarlas así en Telegram."""
    return " ".join(p.capitalize() for p in texto.split()) if texto else texto


def _resumen_vehiculo_ficha(vehiculo: Optional[dict]) -> str:
    """"Chevrolet Tracker · 2021 · Particular · gris" -- Prompt 6 punto 1:
    marca+línea juntas, después modelo/servicio/color separados por "·",
    omitiendo lo que no haya. Lee directo de `vehiculos` (la misma tabla
    que el RGM sobrescribe), no de un snapshot."""
    if not vehiculo:
        return ""
    marca_linea = " ".join(x for x in (_titulo(vehiculo.get("marca")), _titulo(vehiculo.get("linea"))) if x)
    partes = [marca_linea, vehiculo.get("modelo"), _titulo(vehiculo.get("tipo_servicio")), vehiculo.get("color")]
    return " · ".join(x for x in partes if x)


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
        self._alertas_sin_sesion = {}  # (device_uuid, placa) -> estado de alerta (Prompt 2, sin sesión de cámara)
        # chat_id -> {"message_id","creado_en","timer","viendo_pagina","pendiente",
        # "ultima_edicion","ultimo_texto","ultimo_teclado"} del mensaje de
        # "🕒 Últimas" EN VIVO de ese chat (Prompt 6 punto 3).
        self._ultimas_vivo = {}
        # Prompt 6 punto 4: updates de getUpdates (mensajes/botones) en
        # espera de ser atendidos por _trabajador_updates -- getUpdates
        # (_bucle) nunca espera a que se vacíe.
        self._cola_updates = queue.Queue()
        self._lock = threading.Lock()
        self._hilo_bucle = None
        self._hilo_eventos = None

    # ------------------------------------------------------------ config

    def _guardar(self):
        try:
            # No perder un "apkUrl" que alguien agregó a mano al archivo
            # mientras el bot seguía corriendo (igual que guardar() en
            # mock-api/telegram.js).
            url_actual = self._url_apk_fija()
            if url_actual:
                self.cfg["apkUrl"] = url_actual
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Telegram: no se pudo guardar telegram.json: {e}")

    def _url_apk_fija(self) -> Optional[str]:
        """
        Enlace fijo (siempre la última versión publicada) para instalar la
        app, re-leído del archivo en cada pedido -- cambiarlo no exige
        reiniciar el servidor. Solo enlaces https (igual que el mock).
        """
        en_archivo = None
        try:
            with open(self.config_path, "r", encoding="utf-8-sig") as f:
                en_archivo = json.load(f).get("apkUrl")
        except Exception:
            pass
        url = os.environ.get("NEXCAM_APK_URL") or en_archivo or self.cfg.get("apkUrl")
        return url if url and str(url).startswith("https://") else None

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
        # Prompt 6c punto 1: timeout de lectura largo SOLO para getUpdates
        # (long poll real, hasta 30 s pedidos + margen); todo lo demás usa
        # el timeout corto de telegram_http, para que un botón nunca quede
        # esperando detrás de un getUpdates lento.
        timeout_lectura = (
            telegram_http.TIMEOUT_LECTURA_GETUPDATES_SEGUNDOS if metodo == "getUpdates"
            else telegram_http.TIMEOUT_LECTURA_NORMAL_SEGUNDOS
        )
        status, cuerpo_resp = telegram_http.peticion(
            f"{self.api}/bot{self.cfg['token']}/{metodo}", metodo="POST", cuerpo=datos,
            headers={"Content-Type": "application/json"}, timeout_lectura=timeout_lectura,
            log=self.log, etiqueta=metodo,
        )
        j = json.loads(cuerpo_resp.decode("utf-8"))
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

        _, cuerpo_resp = telegram_http.peticion(
            f"{self.api}/bot{self.cfg['token']}/sendPhoto", metodo="POST", cuerpo=cuerpo,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout_lectura=35, log=self.log, etiqueta="sendPhoto",
        )
        j = json.loads(cuerpo_resp.decode("utf-8"))
        if not j.get("ok"):
            raise RuntimeError(f"sendPhoto: {j.get('description', 'error')}")
        return j.get("result")

    def _enviar_documento(self, chat_id, ruta_archivo: Path, caption: str, nombre_archivo: str,
                           tipo_mime: str = "application/octet-stream", timeout: int = 180):
        """Igual mecanismo que _enviar_foto pero para sendDocument (usado
        para la app NEXCAM, que puede pesar decenas de MB)."""
        boundary = f"----nexora{secrets.token_hex(8)}"
        partes = []

        def campo(nombre, valor):
            partes.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{nombre}\"\r\n\r\n{valor}\r\n".encode("utf-8")
            )

        campo("chat_id", chat_id)
        campo("caption", caption[:1024])
        campo("parse_mode", "HTML")
        partes.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{nombre_archivo}\"\r\n"
             f"Content-Type: {tipo_mime}\r\n\r\n").encode("utf-8")
        )
        partes.append(ruta_archivo.read_bytes())
        partes.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        cuerpo = b"".join(partes)

        _, cuerpo_resp = telegram_http.peticion(
            f"{self.api}/bot{self.cfg['token']}/sendDocument", metodo="POST", cuerpo=cuerpo,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout_lectura=timeout, log=self.log, etiqueta="sendDocument",
        )
        j = json.loads(cuerpo_resp.decode("utf-8"))
        if not j.get("ok"):
            raise RuntimeError(f"sendDocument: {j.get('description', 'error')}")
        return j.get("result")

    def enviar_apk(self, chat_id):
        """
        Envía la forma de instalar NEXCAM: enlace fijo (apkUrl) si existe,
        si no el archivo local (<= LIMITE_TELEGRAM_BYTES) o, si pesa más,
        un enlace temporal de 24 h servido por este mismo NEXORA-CONTROL
        (ver /app/nexcam.apk en nexcam_api.py). Puerto de enviarApk() en
        mock-api/telegram.js, releído para esta sección.
        """
        fija = self._url_apk_fija()
        if fija:
            return self._texto(
                chat_id,
                "\n".join([
                    "📲 <b>App NEXCAM</b>: este enlace siempre descarga la última versión.", "",
                    "Para instalarla: toca el botón, abre el archivo descargado y, si Android lo pide, "
                    "permite instalar apps de esta fuente.",
                    "Si ya tienes NEXCAM, elige <b>Actualizar</b> (no la desinstales: tendrías que "
                    "autorizar el teléfono otra vez).",
                    "La primera vez, el administrador debe autorizar el teléfono.",
                ]),
                {"reply_markup": {"inline_keyboard": [[{"text": "📲 Descargar NEXCAM", "url": fija}]]}},
            )

        ruta = ruta_apk()
        if not ruta.is_file():
            return self._texto(
                chat_id,
                "Todavía no hay una app compilada en la PC. Compílala (gradlew assembleRelease) y "
                "vuelve a pedirla con el botón 📲 Instalar app.",
                self._con_menu(),
            )
        tamano = ruta.stat().st_size
        mb = tamano / 1024 / 1024
        if tamano > LIMITE_TELEGRAM_BYTES:
            self.log(f"Telegram: el APK pesa {mb:.1f} MB y Telegram solo deja enviar 50 MB.")
            try:
                import main

                base_url = f"http://{main._detectar_ip_red_local()}:8000"
            except Exception:
                base_url = "http://127.0.0.1:8000"
            url = crear_enlace_apk_temporal(base_url)
            return self._texto(
                chat_id,
                "\n".join([
                    f"📲 La app pesa {mb:.1f} MB, más de lo que Telegram deja enviar (50 MB).",
                    "Descárgala desde este enlace con el teléfono <b>conectado a la misma Wi-Fi que la PC</b> "
                    "(sirve 24 horas):",
                    f'<a href="{url}">Descargar NEXCAM</a>', "",
                    "Al abrir el archivo: si Android lo pide, permite instalar apps de esta fuente. "
                    "Si ya tienes NEXCAM, elige <b>Actualizar</b>.",
                ]),
                self._con_menu(),
            )

        self._texto(chat_id, f"📲 Te envío la app NEXCAM ({mb:.1f} MB). Puede tardar un poco…")
        v = version_app()
        hora_compilado = _hora(int(ruta.stat().st_mtime * 1000))
        caption = "\n".join([
            f"<b>NEXCAM{f' v{v}' if v else ''}</b>, compilada el {hora_compilado}.", "",
            "Para instalarla en el teléfono:",
            "1. Toca el archivo para descargarlo y ábrelo.",
            "2. Si Android lo pide, permite \"instalar apps de esta fuente\" para Telegram.",
            "3. Si ya tienes NEXCAM, elige <b>Actualizar</b> (no la desinstales: tendrías que autorizar "
            "el teléfono otra vez).",
            "4. La primera vez, el administrador debe autorizar el teléfono.",
        ])
        try:
            self._enviar_documento(chat_id, ruta, caption, f"NEXCAM{f'-v{v}' if v else ''}.apk",
                                    "application/vnd.android.package-archive")
        except Exception as e:
            self.log(f"Telegram: no se pudo enviar la app - {e}")
            self._texto(chat_id, "No pude enviar la app ahora. Inténtalo otra vez en un rato con 📲 Instalar app.", self._con_menu())

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

    def _a_todos(self, text, foto: Optional[Path] = None, extra: dict = None,
                 aun_en_silencio: bool = False, chats: Optional[list] = None) -> list:
        # Prompt 2: nada de sendLocation -- la ubicación va como enlace
        # "📍 Ver ubicación" dentro del propio texto (ver _url_mapa).
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
                [{"text": "🔔 Reanudar avisos" if self.cfg["silencio"] else B["silencio"]}, {"text": B["app"]}],
                [{"text": B["ayuda"]}],
            ],
            "resize_keyboard": True, "is_persistent": True,
            "input_field_placeholder": "Elige una opción o escribe una placa",
        }

    def _con_menu(self, extra: dict = None) -> dict:
        return {**(extra or {}), "reply_markup": self._menu()}

    def _texto_boton_rgm(self, placa: str) -> str:
        """Prompt 6b punto 2: el botón RGM SIEMPRE está (antes se ocultaba
        si ya había un RGM de menos de 24 h -- Gabriel no lo encontraba
        cuando quería forzar una actualización); el texto dice si va a
        pedir uno nuevo o a refrescar el que ya hay."""
        return "🔄 Actualizar RGM" if db.obtener_ultimo_snapshot_por_fuente(placa, "RGM") else "🔎 Consultar RGM"

    def _botones_vista(self, v: dict, es_admin: bool) -> dict:
        # Prompt 2: se quita el botón Mapa -- la ubicación ya va como
        # enlace "📍 Ver ubicación" en el propio texto del mensaje.
        fila = [{"text": "📋 Ficha", "callback_data": f"ficha:{v['placa']}"}]
        if v.get("snapshot") and v.get("vehiculo"):
            fila.append({"text": "🔢 Foto de la placa", "callback_data": f"placa:{v['id']}"})
        filas = [fila]
        # Prompt 6b punto 2: el botón RGM es solo de Admin (un Operador no
        # ve deudor/acreedor en el resultado -- pedirlo tampoco le sirve).
        if es_admin:
            filas.append([{"text": self._texto_boton_rgm(v["placa"]), "callback_data": f"rgm:{v['placa']}"}])
        return {"inline_keyboard": filas}

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
            f"<b>{B['app']}</b>: te envío la última versión de la app para el teléfono",
        ])

    # ------------------------------------------------------------ ficha única de Telegram (Prompt 6 punto 1)

    def _texto_ficha_telegram(self, placa: str, *, es_admin: bool, leida: Optional[str] = None,
                               lectura_actual: Optional[dict] = None, limite: Optional[int] = None,
                               titulo: Optional[str] = None) -> str:
        """
        Formato ÚNICO compartido por la alerta en vivo, la respuesta del
        🔎 RGM (a pedido), el botón 📋 Ficha y /buscar -- antes cada uno
        armaba su propio texto (y el RGM a pedido se pegaba al final de
        la alerta, duplicando "Consulta hecha."/"En tu lista: …"/
        "Vista N veces. Última: …"/"Antes:", que la lista de Ubicaciones
        de abajo ya cubre).

        `leida` (solo para una alerta "posible"): lo que la cámara leyó,
        cuando difiere de la placa de la lista. `lectura_actual`: dict
        {"ts","confianza","operador"} de la lectura que disparó una
        alerta EN VIVO -- agrega la línea "🕘 fecha · confianza NN % ·
        👤 operador" antes de Ubicaciones (además de que esa misma visita
        también aparece dentro de la lista, con su fecha completa).
        `limite`: si el texto final no cabe (pie de foto, 1024
        caracteres), se recorta la lista de Ubicaciones primero, nunca
        las líneas fijas de arriba. `titulo`: reemplaza la primera línea
        ("🚗 <b>PLACA</b>") -- usado por "Ya la viste" (Prompt 6 punto 7),
        que necesita decir "🗂️ Ya la viste: PLACA" en vez de solo la placa.
        """
        vehiculo = db.buscar_placa(placa)
        lineas = [titulo or f"🚗 <b>{_esc(_fmt_placa(placa))}</b>"]
        if leida:
            lineas.append(f"⚠️ POSIBLE · leyó {_esc(leida)}")
        elif vehiculo and vehiculo["estado"] == "activo":
            motivo = (vehiculo.get("observaciones") or "").strip() or "UBICAR"
            lineas.append(f"🚨 En lista: {_esc(motivo)}")
        elif vehiculo:
            lineas.append("Estuvo en la lista (dada de baja).")
        else:
            lineas.append("No está en la lista.")

        fecha_ingreso_ms = _ms_de_texto_local(vehiculo.get("fecha_ingreso")) if vehiculo else None
        if fecha_ingreso_ms is not None:
            lineas.append(f"📅 Ingreso: {_hora(fecha_ingreso_ms)}")

        resumen_vehiculo = _resumen_vehiculo_ficha(vehiculo)
        if resumen_vehiculo:
            lineas.append(f"🚙 {_esc(resumen_vehiculo)}")

        serie_motor = []
        if vehiculo and vehiculo.get("numero_serie"):
            serie_motor.append(f"Serie/VIN: {_esc(vehiculo['numero_serie'])}")
        if vehiculo and vehiculo.get("motor"):
            serie_motor.append(f"Motor: {_esc(vehiculo['motor'])}")
        if serie_motor:
            lineas.append(f"🔩 {' · '.join(serie_motor)}")

        if es_admin:
            snap_rgm = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
            datos_rgm = None
            if snap_rgm:
                try:
                    datos_rgm = json.loads(snap_rgm["datos"])
                except Exception:
                    datos_rgm = None
            if datos_rgm:
                acreedor = (datos_rgm.get("acreedor") or {}).get("razon_social")
                if acreedor:
                    lineas.append(f"🏦 Acreedor: {_esc(acreedor)}")
                deudor = datos_rgm.get("deudor_garante") or {}
                if deudor.get("nombre"):
                    partes = [deudor["nombre"]]
                    documento = " ".join(x for x in (deudor.get("tipo_identificacion"), deudor.get("numero_identificacion")) if x)
                    if documento:
                        partes.append(documento)
                    if deudor.get("ciudad"):
                        partes.append(deudor["ciudad"])
                    lineas.append(f"👤 Deudor: {_esc(' · '.join(partes))}")

        if vehiculo and vehiculo.get("rgm_actualizado_en"):
            lineas.append(f"📄 RGM del {_hora(_ms_de_texto_local(vehiculo['rgm_actualizado_en']))}")

        if lectura_actual:
            conf = f"{round(lectura_actual['confianza'] * 100)} %" if lectura_actual.get("confianza") is not None else "—"
            lineas.append(f"🕘 {_hora(lectura_actual.get('ts'))} · confianza {conf} · 👤 {_esc(lectura_actual.get('operador') or '—')}")

        vistas = nexcam_db.vistas_de([placa]).get(placa)
        todas = vistas["vistas"] if vistas else []
        filas_ubicacion = []
        for v in todas[:8]:
            enlace = f' · <a href="{_url_mapa(v["lat"], v["lng"])}">Ver</a>' if _tiene_gps(v) else ""
            filas_ubicacion.append(f"• {_hora(v.get('ts'))} · {_esc(v.get('operador') or '—')}{enlace}")

        def _armar(filas_ub):
            bloque = []
            if todas:
                bloque.append(f"🗺️ Ubicaciones ({len(todas)})")
                bloque.extend(filas_ub)
                ocultas = len(todas) - len(filas_ub)
                if ocultas > 0:
                    bloque.append(f"… y {ocultas} más en la web.")
            return "\n".join(lineas + bloque)

        texto = _armar(filas_ubicacion)
        # Prompt 6 punto 1: si no cabe en el pie de foto (1024), se
        # recorta la lista de Ubicaciones primero, nunca las líneas fijas.
        while limite and len(texto) > limite and filas_ubicacion:
            filas_ubicacion.pop()
            texto = _armar(filas_ubicacion)
        return texto

    def _botones_alerta(self, estado: dict, es_admin: bool) -> dict:
        fila = [{"text": "📋 Ficha", "callback_data": f"ficha:{estado['placa']}"}]
        if estado.get("avistamiento_id") is not None:
            fila.append({"text": "🔢 Placa", "callback_data": f"placa:{estado['avistamiento_id']}"})
        filas = [fila]
        # Prompt 6b punto 2: el botón RGM SIEMPRE está en la alerta (antes
        # se ocultaba si ya había un RGM de menos de 24 h: Gabriel no lo
        # encontraba cuando quería forzar una actualización) y es solo de
        # Admin (un Operador no ve el resultado, pedirlo tampoco le sirve).
        if es_admin:
            filas.append([{"text": self._texto_boton_rgm(estado["placa"]), "callback_data": f"rgm:{estado['placa']}"}])
        return {"inline_keyboard": filas}

    def _enviar_ficha(self, chats: list, placa: str, *, foto: Optional[Path] = None, leida: Optional[str] = None,
                       lectura_actual: Optional[dict] = None, botones_fn: Optional[callable] = None,
                       aun_en_silencio: bool = False) -> list:
        """Manda la ficha única a varios chats -- a diferencia de
        _a_todos (mismo texto para todos), acá el texto SE ARMA POR CHAT
        porque acreedor/deudor solo se ven si ese chat es de un Admin
        activo. `botones_fn(es_admin)` (Prompt 6b punto 2: el botón RGM
        también es solo de Admin) arma el teclado por chat, igual que el
        texto; si no se da, no hay teclado. Devuelve la misma forma que
        _a_todos: [{"chat_id","message_id"}], para poder seguir editando
        después (ver _refrescar_texto_estado)."""
        if not self.activo or (self.cfg["silencio"] and not aun_en_silencio):
            return []
        if not chats:
            return []
        limite = 1024 if foto else None
        enviados = []
        for c in chats:
            usuario = self._usuario_de_chat(c["id"])
            es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")
            texto = self._texto_ficha_telegram(placa, es_admin=es_admin, leida=leida,
                                                lectura_actual=lectura_actual, limite=limite)
            extra = {"reply_markup": botones_fn(es_admin)} if botones_fn else None
            try:
                m = self._enviar(c["id"], texto, foto, extra)
                enviados.append({"chat_id": c["id"], "message_id": m.get("message_id") if m else None})
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar a {c.get('nombre') or c['id']} - {e}")
        return enviados

    def _refrescar_texto_estado(self, estado: dict):
        """Vuelve a armar el texto de una alerta EN VIVO con los datos
        ACTUALES de la placa (incluido el RGM si ya llegó) y edita sus
        mensajes -- Prompt 6 punto 1: cuando el RGM pedido desde el botón
        de una alerta termina, esto es lo que la actualiza (nunca se
        manda ni se pega texto aparte; una repetición de la misma lectura
        -- _editar_alerta -- también pasa por acá). El teclado (con o sin
        el botón RGM) se arma POR CHAT, Prompt 6b punto 2."""
        leida = estado.get("leida") if estado.get("tipo") == "probable" else None
        lectura_actual = {"ts": estado.get("ultima_ts"), "confianza": estado.get("confianza"),
                           "operador": estado.get("operador")}
        for m in estado.get("mensajes") or []:
            if not m.get("message_id"):
                continue
            usuario = self._usuario_de_chat(m["chat_id"])
            es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")
            limite = 1024 if estado.get("con_foto") else None
            texto = self._texto_ficha_telegram(estado["placa"], es_admin=es_admin, leida=leida,
                                                lectura_actual=lectura_actual, limite=limite)
            try:
                cuerpo = {"chat_id": m["chat_id"], "message_id": m["message_id"], "parse_mode": "HTML",
                          "reply_markup": self._botones_alerta(estado, es_admin)}
                if estado.get("con_foto"):
                    cuerpo["caption"] = texto
                    self._llamar("editMessageCaption", cuerpo)
                else:
                    cuerpo["text"] = texto
                    cuerpo["disable_web_page_preview"] = True
                    self._llamar("editMessageText", cuerpo)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    self.log(f"Telegram: no se pudo actualizar la alerta de {estado['placa']} - {e}")

    def _crear_alerta(self, p: dict) -> dict:
        placa = p["placa"]
        tipo = "probable" if p.get("match_type") == "probable" else "exacta"
        estado = {
            "placa": placa, "tipo": tipo, "leida": p.get("leida"),
            "ultima_ts": p.get("ts") or int(time.time() * 1000),
            "confianza": p.get("confidence"), "operador": p.get("operador"), "device": p.get("device"),
            "avistamiento_id": p.get("id"), "ultima_actividad": time.time(),
        }
        foto = self._foto_principal(p)
        estado["con_foto"] = bool(foto)
        leida = estado["leida"] if tipo == "probable" else None
        lectura_actual = {"ts": estado["ultima_ts"], "confianza": estado["confianza"], "operador": estado["operador"]}
        enviados = self._enviar_ficha(self.cfg["chats"], placa, foto=foto, leida=leida, lectura_actual=lectura_actual,
                                       botones_fn=lambda es_admin: self._botones_alerta(estado, es_admin))
        estado["mensajes"] = enviados
        return estado

    def _editar_alerta(self, estado: dict, p: dict):
        estado["tipo"] = "probable" if p.get("match_type") == "probable" else "exacta"
        estado["leida"] = p.get("leida") or estado.get("leida")
        estado["ultima_ts"] = p.get("ts") or int(time.time() * 1000)
        estado["confianza"] = p.get("confidence")
        estado["operador"] = p.get("operador") or estado.get("operador")
        estado["ultima_actividad"] = time.time()
        if p.get("id") is not None:
            estado["avistamiento_id"] = p["id"]  # el botón "Placa" siempre apunta a la foto más reciente
        self._refrescar_texto_estado(estado)

    def _alerta_tardia(self, p: dict):
        """Aviso independiente para una lectura que llegó de la cola del
        teléfono con un `ts` de antes del inicio de esta sesión de cámara
        -- nunca crea ni edita el estado de alerta "en vivo" de la sesión
        actual (eso se queda limpio de lecturas fuera de orden)."""
        placa = p["placa"]
        vehiculo = db.buscar_placa(placa)
        tipo = "probable" if p.get("match_type") == "probable" else "exacta"
        lineas = [f"🚗 <b>{_esc(_fmt_placa(placa))}</b>"]
        if tipo == "probable":
            lineas.append(f"⚠️ POSIBLE · leyó {_esc(p.get('leida') or '')}")
        else:
            motivo = p.get("motivo") or (vehiculo.get("observaciones") if vehiculo else None) or "en lista"
            lineas.append(f"🚨 EN LISTA · {_esc(motivo)}")
        lineas.append(f"🕘 Vista el {_hora(p.get('ts'))} (llegó con retraso)")
        estado_botones = {"placa": placa, "avistamiento_id": p.get("id")}
        texto = "\n".join(lineas)
        if not self.activo or self.cfg["silencio"]:
            return
        for c in self.cfg["chats"]:
            usuario = self._usuario_de_chat(c["id"])
            es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")
            try:
                self._enviar(c["id"], texto, extra={"reply_markup": self._botones_alerta(estado_botones, es_admin)})
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar la alerta tardía de {placa} a {c.get('nombre') or c['id']} - {e}")

    def _alerta_placa(self, p: dict):
        """
        Prompt 2: una sola alerta por placa (la de la lista, no la leída)
        por sesión de cámara del teléfono, o por TG_REPETIR_MIN si no hay
        sesión abierta. Las repeticiones EDITAN la alerta original en vez
        de mandar un mensaje nuevo; todas las lecturas se siguen guardando
        igual en la base (eso no cambia, viene de antes en registrar_avistamiento).
        """
        if not (p.get("en_lista") or p.get("match_type") in ("exacta", "probable")):
            if self.cfg["modo"] == "todo" and not self.cfg["en_vivo"]:
                self._a_todos(f"▫️ {_esc(_fmt_placa(p['placa']))} leída. No está en la lista.\n{_esc(p.get('operador') or '')}, {_hora(p.get('ts'))}")
            return
        placa = p["placa"]
        sesion = self.sesiones.get(p.get("device_uuid"))

        # Lectura tardía (llegó de la cola del teléfono, de antes de que
        # esta sesión de cámara empezara): nunca edita la alerta de la
        # sesión actual como si fuera nueva. Si nunca se avisó esta placa
        # en esta sesión, se manda un aviso aparte marcado "con retraso",
        # con la fecha real de cuando se vio, no la de ahora.
        if sesion is not None and p.get("ts") and p["ts"] < sesion["inicio"]:
            if placa not in sesion["alertas"] and placa not in sesion["avisos_tardios"]:
                self._alerta_tardia(p)
                sesion["avisos_tardios"].add(placa)
            return

        clave = (p.get("device_uuid"), placa)
        if sesion is not None:
            estado = sesion["alertas"].get(placa)
        else:
            estado = self._alertas_sin_sesion.get(clave)
            if estado and (time.time() - estado["ultima_actividad"]) > TG_REPETIR_MIN * 60:
                estado = None  # venció la ventana sin sesión: cuenta como una alerta nueva
        if estado:
            self._editar_alerta(estado, p)
            return
        estado = self._crear_alerta(p)
        if sesion is not None:
            sesion["alertas"][placa] = estado
        else:
            self._alertas_sin_sesion[clave] = estado

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
        # Prompt 2: una fila por PLACA (con contador ×N y hora de la última
        # lectura), no una fila por lectura -- antes s["lecturas"] guardaba
        # cada lectura suelta; ahora s["placas"] agrega por placa.
        n_placas = len(s["placas"])
        en_lista = sum(1 for d in s["placas"].values() if d["tipo"] == "exacta")
        actor = f"{_esc(s.get('operador') or 'Operador')} ({_esc(s['device'])})"
        if final:
            # Prompt 6 punto 6: "medianoche" cierra el mensaje del día que
            # termina (la cámara NO se apagó, solo arranca un mensaje
            # nuevo que vuelve a contar desde 0) -- título propio para no
            # decir "Cámara apagada" cuando no lo está.
            if final == "medianoche":
                titulo, motivo = "🌙 Fin del día", ""
            else:
                titulo = "⏹️ Cámara apagada"
                motivo = " Se perdió la señal del teléfono." if final == "sin-senal" else ""
            cab = (f"{titulo} · {actor}\n"
                   f"{_reloj(s['inicio'])} a {_reloj(s['fin'])} ({_duracion(s['fin'] - s['inicio'])}).{motivo}")
        else:
            cab = f"📷 <b>Cámara encendida</b> · {actor}\nDesde {_reloj(s['inicio'])} · {n_placas} placas · {en_lista} en lista"
        if not s["placas"]:
            return f"{cab}\n\n{'No leyó ninguna placa.' if final else 'Esperando placas…'}"
        # Prompt 5 punto 5: solo las últimas TG_VIVO_MAX (la cabecera ya
        # cuenta el total de la sesión completa, arriba).
        filas_orden = sorted(s["placas"].values(), key=lambda d: -d["ultima_ts"])
        filas = []
        for d in filas_orden[:TG_VIVO_MAX]:
            icono = ICONO_VIVO.get(d["tipo"], "⚪")
            filas.append(f"{icono} 🚗 {placa_destacada(_fmt_placa(d['placa']))}   {_reloj_corto(d['ultima_ts'])}")
        mas = f"\n…y {len(filas_orden) - TG_VIVO_MAX} placa(s) más." if len(filas_orden) > TG_VIVO_MAX else ""
        return f"{cab}\n\n{chr(10).join(filas)}{mas}"

    def _abrir_sesion(self, device_uuid, device, operador, ts) -> Optional[dict]:
        with self._lock:
            if device_uuid in self.sesiones or not self.cfg["en_vivo"]:
                return self.sesiones.get(device_uuid)
            inicio = ts or int(time.time() * 1000)
            s = {"device_uuid": device_uuid, "device": device or "Teléfono", "operador": operador,
                 "inicio": inicio, "ultima": time.time(), "placas": {},
                 "alertas": {}, "avisos_tardios": set(), "mensajes": [], "timer": None,
                 # Prompt 6 punto 6: fecha LOCAL (Bogotá) de esta sesión --
                 # si sigue abierta cuando cambie, _vigilar_sesiones_inactivas
                 # la cierra con el resumen del día y abre una nueva en 0.
                 "dia": _dt_bogota(inicio).strftime("%Y-%m-%d")}
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

    def _cerrar_por_cambio_de_dia(self, uuid):
        """Prompt 6 punto 6: una sesión que sigue abierta al cruzar la
        medianoche (hora de Bogotá) cierra su mensaje con el resumen del
        día y abre uno NUEVO que arranca en 0 -- nada se borra (las
        lecturas ya guardadas siguen intactas en la base), solo el
        contador EN VIVO de ese mensaje de Telegram."""
        with self._lock:
            s = self.sesiones.pop(uuid, None)
        if not s:
            return
        s["fin"] = int(time.time() * 1000)
        self._refrescar_sesion(s, "medianoche")
        self._abrir_sesion(uuid, s["device"], s["operador"], s["fin"])

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
        # Prueba real: lecturas que llegaban tarde (cola acumulada del
        # teléfono mientras no había conexión) con un `ts` de ANTES de
        # que esta sesión de cámara empezara no deben aparecer como si
        # fueran lo que la cámara está viendo "ahora" en "Cámara encendida".
        if p.get("ts") and p["ts"] < s["inicio"]:
            return
        s["ultima"] = time.time()
        placa = p["placa"]
        tipo = p.get("match_type") or ("exacta" if p.get("en_lista") else None)
        ts = p.get("ts") or int(time.time() * 1000)
        existente = s["placas"].get(placa)
        if existente:
            existente["veces"] += 1
            existente["ultima_ts"] = ts
            if tipo:
                existente["tipo"] = tipo
        else:
            s["placas"][placa] = {"placa": placa, "tipo": tipo, "veces": 1, "ultima_ts": ts}
        if not s.get("timer"):
            s["timer"] = threading.Timer(EDIT_SEGUNDOS, lambda: self._refrescar_sesion(s))
            s["timer"].daemon = True
            s["timer"].start()

    def _revisar_sesiones_una_vez(self):
        """Un ciclo de _vigilar_sesiones_inactivas, extraído aparte para
        poder probarlo sin depender del `while` + `time.sleep(60)` real."""
        dia_actual = _dt_bogota().strftime("%Y-%m-%d")
        for uuid, s in list(self.sesiones.items()):
            if time.time() - s["ultima"] > VENTANA_SIN_SENAL_SEGUNDOS:
                self._cerrar_sesion(uuid, "sin-senal")
            elif s.get("dia") and s["dia"] != dia_actual:
                self._cerrar_por_cambio_de_dia(uuid)
        # Poda las alertas por placa fuera de sesión (sin cámara en
        # vivo) que ya vencieron su ventana de TG_REPETIR_MIN: así la
        # próxima lectura de esa placa manda una alerta nueva, no edita
        # una vieja de hace rato.
        vencidas = [
            clave for clave, estado in self._alertas_sin_sesion.items()
            if (time.time() - estado["ultima_actividad"]) > TG_REPETIR_MIN * 60
        ]
        for clave in vencidas:
            self._alertas_sin_sesion.pop(clave, None)
        # Prompt 6 punto 3: "Últimas en vivo" se fija a las 12 h aunque
        # no haya llegado ninguna lectura nueva que dispare el chequeo
        # de _tocar_ultimas_vivo.
        vencidas_ultimas = [
            chat_id for chat_id, info in self._ultimas_vivo.items()
            if (time.time() - info["creado_en"]) > TG_ULTIMAS_VIVO_HORAS * 3600
        ]
        for chat_id in vencidas_ultimas:
            self._congelar_ultimas_vivo(chat_id)

    def _vigilar_sesiones_inactivas(self):
        while not self.detenido:
            time.sleep(60)
            self._revisar_sesiones_una_vez()

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
            # Prompt 6 punto 7: "Ya la viste" completo -- formato único
            # (punto 1, con 🗺️ Ubicaciones y foto de la última vez) y SOLO
            # a los chats de Admin (antes iba a todos con _a_todos). Un
            # mensaje por placa, máximo 10 por carga.
            hits = p.get("hits") or []
            chats_admin = self._chats_admin()
            for h in hits[:10]:
                placa = h["placa"]
                titulo = (f"🗂️ <b>Ya la viste: {_esc(_fmt_placa(placa))}</b>" if h["tipo"] == "exacta"
                          else f"⚠️ <b>Posiblemente vista: {_esc(_fmt_placa(placa))}</b>")
                v0 = {**h["vistas"][0], "placa": placa}
                foto = self._foto_principal(v0)
                limite = 1024 if foto else None
                texto = self._texto_ficha_telegram(placa, es_admin=True, titulo=titulo, limite=limite)
                for c in chats_admin:
                    try:
                        self._enviar(c["id"], texto, foto, extra={"reply_markup": self._botones_vista(v0, True)})
                    except Exception as e:
                        self.log(f"Telegram: no se pudo avisar 'ya la viste' de {placa} a {c['id']} - {e}")
            if len(hits) > 10:
                resto = ", ".join(_esc(_fmt_placa(h["placa"])) for h in hits[10:])
                texto_resto = f"… y {len(hits) - 10} más, ver en la web: {resto}"
                for c in chats_admin:
                    try:
                        self._texto(c["id"], texto_resto)
                    except Exception as e:
                        self.log(f"Telegram: no se pudo avisar el resto de 'ya la viste' a {c['id']} - {e}")
            return
        if evento == "detection.hit":
            self._lectura_en_vivo(p)
            self._alerta_placa(p)
            self._tocar_ultimas_vivo()
            return
        if evento == "rgm.resultado":
            return self._on_rgm_resultado(p)
        if evento == "rgm.error":
            # Prompt 7 bloque A, punto 4: igual que rgm.resultado -- el
            # silencio de un ítem automático aplica SOLO si nadie lo pidió
            # (p["avisos"], de la base); "panel" sigue avisando a Admin.
            # Incluye [🔁 Reintentar] (mismo callback que pedir de nuevo).
            origen = p.get("origen")
            placa = p.get("placa")
            avisos = p.get("avisos") or []
            if origen == "panel":
                chats = [c["id"] for c in self._chats_admin()]
            elif avisos:
                chats = [a["chat_id"] for a in avisos]
            else:
                return
            accion_retry = "rgm" if p.get("alcance") != "JUDICIAL" else "rgm_judicial"
            boton_reintentar = {"reply_markup": {"inline_keyboard": [[
                {"text": "🔁 Reintentar", "callback_data": f"{accion_retry}:{placa}"}]]}}
            for chat_id in chats:
                try:
                    self._texto(chat_id, f"⚠️ No se pudo consultar RGM de <b>{_esc(_fmt_placa(placa or ''))}</b>: {_esc(p.get('error'))}",
                                boton_reintentar)
                except Exception as e:
                    self.log(f"Telegram: no se pudo avisar rgm.error a {chat_id} - {e}")
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

    def _on_rgm_resultado(self, p: dict):
        """
        FASE 6 + Prompt 5 punto 1: RGM AUTOMÁTICO (cámara/estudio) queda
        SILENCIOSO -- ya se guardó el snapshot y rgm_cola actualizó la
        ficha, no hace falta avisar nada. Solo una consulta PEDIDA
        explícitamente (botón 🔎 RGM en Telegram, o "Consultar" en la
        página Consulta) avisa al terminar.

        Prompt 7 bloque A, punto 3: el silencio se decide con
        `p["avisos"]` (de rgm_avisos, persistido en la base por
        rgm_cola.solicitar_consulta), NO con `origen` -- ese era el bug
        real: un ítem que la cámara encoló automáticamente sigue teniendo
        origen="camara" para siempre, aunque alguien lo haya pedido
        DESPUÉS por Telegram; antes, _on_rgm_resultado se quedaba callado
        solo por mirar `origen`, sin fijarse si alguien esperaba el
        resultado.

        Prompt 6 punto 1: si quien pidió tenía el botón sobre una alerta
        EN VIVO (`aviso["alerta_message_id"]`), el resultado EDITA esa
        misma alerta -- nunca se manda ni se pega texto aparte. En
        cualquier otro caso se manda un mensaje nuevo. REGLA DE SEGURIDAD
        de siempre: el texto se arma DISTINTO por chat según si está
        vinculado a un usuario Admin activo.
        """
        origen = p.get("origen")
        placa = p.get("placa")
        item_id = p.get("item_id")
        resultado = p.get("resultado") or {}
        rgm_resultado = resultado.get("rgm")
        judicial_resultado = resultado.get("judicial")
        if rgm_resultado is None and judicial_resultado is None:
            return

        avisos = p.get("avisos") or []
        if origen == "panel":
            destinos = [{"chat_id": c["id"], "estado_alerta": None} for c in self._chats_admin()]
        elif avisos:
            destinos = [
                {"chat_id": a["chat_id"],
                 "estado_alerta": self._estado_alerta_por_mensaje(a["alerta_chat_id"], a["alerta_message_id"])
                 if a.get("alerta_message_id") else None}
                for a in avisos
            ]
        else:
            return  # automático (cámara/estudio) y nadie lo pidió: silencio

        if not self.activo or self.cfg["silencio"]:
            return

        for destino in destinos:
            chat_id = destino["chat_id"]
            estado_alerta = destino["estado_alerta"]
            if rgm_resultado is not None and judicial_resultado is None and estado_alerta is not None:
                self._refrescar_texto_estado(estado_alerta)
                db.registrar_diagnostico_rgm("aviso enviado (editó alerta)", item_id, placa, f"chat_id={chat_id}")
                continue

            usuario = self._usuario_de_chat(chat_id)
            es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")

            if rgm_resultado is not None:
                texto_ficha = self._texto_ficha_telegram(placa, es_admin=es_admin)
                botones = [{"text": "📋 Ficha", "callback_data": f"ficha:{placa}"}]
                if judicial_resultado is None and es_admin:
                    botones.append({"text": "⚖️ Procesos judiciales", "callback_data": f"rgm_judicial:{placa}"})
                try:
                    self._texto(chat_id, texto_ficha, {"reply_markup": {"inline_keyboard": [botones]}})
                    db.registrar_diagnostico_rgm("aviso enviado", item_id, placa, f"chat_id={chat_id}")
                except Exception as e:
                    self.log(f"Telegram: no se pudo avisar rgm.resultado a {chat_id} - {e}")
                    db.registrar_diagnostico_rgm("aviso fallido", item_id, placa, f"chat_id={chat_id}: {e}")

            if judicial_resultado is not None:
                try:
                    self._enviar_lista_procesos(chat_id, placa, judicial_resultado, es_admin)
                    db.registrar_diagnostico_rgm("aviso enviado (procesos)", item_id, placa, f"chat_id={chat_id}")
                except Exception as e:
                    self.log(f"Telegram: no se pudo avisar judicial.resultado a {chat_id} - {e}")
                    db.registrar_diagnostico_rgm("aviso fallido (procesos)", item_id, placa, f"chat_id={chat_id}: {e}")

    def _enviar_lista_procesos(self, chat_id, placa: str, resultado: dict, es_admin: bool):
        """
        Prompt 8d punto 2: lista legible de procesos judiciales (grupos
        financiera/otros, tarjetas cortas, fechas dd/mm/aaaa, nombres con
        mayúscula inicial) -- usa el shape de judicial_directo.py
        (consultaprocesos.ramajudicial.gov.co, API directa). Los de la
        financiera primero (judicial_directo.ordenar_y_marcar_procesos,
        comparando contra el acreedor del último RGM de esta placa),
        privados sin radicado ni botón. Partido en varios mensajes sin
        cortar una tarjeta a la mitad (judicial_formato.particionar_
        mensajes_telegram) si no cabe en 4096 caracteres. Botones
        [📄 N]... + [🔄 Actualizar][⬅️ Volver] solo en el último mensaje.
        """
        if not es_admin:
            self._texto(chat_id, f"📄 <b>Procesos judiciales consultados: {_esc(_fmt_placa(placa))}</b>\nConsulta hecha.")
            return

        import judicial_directo
        import judicial_formato as jf

        procesos = resultado.get("procesos") or []
        if not procesos:
            self._texto(chat_id, f"⚖️ <b>{_esc(_fmt_placa(placa))}</b>: no se encontraron procesos judiciales.",
                        {"reply_markup": {"inline_keyboard": [[{"text": "⬅️ Volver", "callback_data": "procvolver"}]]}})
            return

        acreedor = None
        snap_rgm = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
        if snap_rgm:
            try:
                acreedor = (json.loads(snap_rgm["datos"]).get("acreedor") or {}).get("razon_social")
            except Exception:
                acreedor = None
        ordenados = judicial_directo.ordenar_y_marcar_procesos(procesos, acreedor)
        armado = jf.construir_tarjetas_judicial(_fmt_placa(placa), ordenados)
        mensajes = jf.particionar_mensajes_telegram(armado["piezas"])

        botones_procesos = [
            {"text": f"📄 {n}", "callback_data": f"procver:{id_proceso}:{placa}"}
            for id_proceso, n in sorted(armado["numero_por_id_proceso"].items(), key=lambda kv: kv[1])
        ]
        filas_botones = [botones_procesos[i:i + 4] for i in range(0, len(botones_procesos), 4)]
        filas_botones.append([{"text": "🔄 Actualizar", "callback_data": f"rgm_judicial:{placa}"},
                               {"text": "⬅️ Volver", "callback_data": "procvolver"}])

        for i, texto in enumerate(mensajes):
            extra = {"reply_markup": {"inline_keyboard": filas_botones}} if i == len(mensajes) - 1 else None
            self._texto(chat_id, texto, extra)

    def _mostrar_detalle_proceso(self, chat_id, message_id, id_proceso: int, placa: str, pagina: int,
                                  nuevo_mensaje: bool, forzar: bool = False):
        """
        Prompt 8d punto 3: "Ver proceso" (📄 N) manda un panel NUEVO con el
        detalle + actuaciones (10 por página, más reciente arriba); cambiar
        de página EDITA ese mismo panel (nunca manda uno nuevo). Caché de
        6 h (judicial_directo.obtener_detalle_y_actuaciones_cacheado).
        """
        import judicial_directo
        import judicial_formato as jf

        try:
            datos = judicial_directo.obtener_detalle_y_actuaciones_cacheado(placa, id_proceso, forzar=forzar)
        except judicial_directo.ErrorJudicialDirecto as e:
            texto_error = f"No se pudo consultar el proceso: {_esc(e)}"
            extra = {"reply_markup": {"inline_keyboard": [[{"text": "⬅️ Volver", "callback_data": "procvolver"}]]}}
            if nuevo_mensaje:
                self._texto(chat_id, texto_error, extra)
            else:
                try:
                    self._llamar("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                                       "parse_mode": "HTML", "text": texto_error,
                                                       "reply_markup": extra["reply_markup"]})
                except Exception:
                    pass
            return

        detalle = datos.get("detalle") or {}
        actuaciones = datos.get("actuaciones") or []
        pag = jf.paginar(actuaciones, pagina)

        piezas = [jf.texto_detalle_proceso(detalle),
                  f"Actuaciones ({pag['total']}), página {pag['pagina']}/{pag['total_paginas']}:"]
        piezas.extend(jf.texto_actuacion(a) for a in pag["items"])
        texto = "\n\n".join(piezas) or "Sin actuaciones."

        nav = []
        if pag["pagina"] < pag["total_paginas"]:
            nav.append({"text": "◀ Más antiguas", "callback_data": f"procpag:{id_proceso}:{placa}:{pag['pagina'] + 1}"})
        if pag["pagina"] > 1:
            nav.append({"text": "Más recientes ▶", "callback_data": f"procpag:{id_proceso}:{placa}:{pag['pagina'] - 1}"})
        filas = [nav] if nav else []
        filas.append([{"text": "📑 Proceso completo (PDF)", "callback_data": f"procpdf:{id_proceso}:{placa}"}])
        filas.append([{"text": "⬅️ Volver a la lista", "callback_data": "procvolver"}])
        reply_markup = {"inline_keyboard": filas}

        if nuevo_mensaje:
            self._texto(chat_id, texto, {"reply_markup": reply_markup})
        else:
            try:
                self._llamar("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                                   "parse_mode": "HTML", "text": texto, "reply_markup": reply_markup})
            except Exception as e:
                self.log(f"Telegram: no se pudo cambiar de página el detalle del proceso - {e}")

    def _enviar_pdf_proceso(self, chat_id, id_proceso: int, placa: str):
        """Prompt 8d punto 3: "📑 Proceso completo (PDF)" -- detalle +
        TODAS las actuaciones (anotaciones completas, sin recortar), se
        guarda también para la ficha del vehículo (procesos_pdf)."""
        import judicial_pdf

        try:
            generado = judicial_pdf.generar_y_guardar_pdf_proceso(placa, id_proceso)
            ruta = generado["ruta"]
            self._enviar_documento(chat_id, ruta, f"📑 Proceso completo: {_fmt_placa(placa)}", ruta.name, "application/pdf")
            db.registrar_diagnostico_rgm("PDF de proceso enviado", None, placa, f"id_proceso={id_proceso}")
        except Exception as e:
            self.log(f"Telegram: no se pudo generar/enviar el PDF del proceso - {e}")
            self._texto(chat_id, "No pude generar el PDF ahora. Inténtalo otra vez con 📑 Proceso completo (PDF).")

    # ------------------------------------------------------------ acciones del usuario

    def _buscar(self, chat_id, placa_raw: str):
        """Botón 📋 Ficha y /buscar: Prompt 6 punto 1, mismo formato único
        que la alerta y la respuesta del 🔎 RGM (armado por
        _texto_ficha_telegram), sin "Consulta hecha"/"En tu lista"/
        "Vista N veces"/"Antes" (la lista de Ubicaciones ya lo cubre)."""
        placa = nexcam_db.normalizar_placa(placa_raw)
        if not nexcam_db.RE_PLACA_NEXCAM.match(placa):
            return self._texto(chat_id, "Escribe una placa de 3 letras y 3 números, como ABC123.", self._con_menu())
        usuario = self._usuario_de_chat(chat_id)
        es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")
        vistas = nexcam_db.vistas_de([placa]).get(placa)
        v0 = {**vistas["vistas"][0], "placa": placa} if vistas and vistas["vistas"] else {"placa": placa}
        foto = self._foto_principal(v0)
        texto = self._texto_ficha_telegram(placa, es_admin=es_admin, limite=1024 if foto else None)
        return self._enviar(chat_id, texto, foto=foto, extra={"reply_markup": self._botones_vista(v0, es_admin)})

    def _resumen_carga(self, resumen: dict) -> str:
        lineas = [f"📥 <b>Lista cargada</b>: {len(resumen['insertadas']) + len(resumen['duplicadas'])} placas "
                  f"({len(resumen['insertadas'])} nuevas, {len(resumen['duplicadas'])} ya existían)."]
        if resumen["invalidas"]:
            muestra = ", ".join(f"<code>{_esc(x[:30])}</code>" for x in resumen["invalidas"][:5])
            lineas.append(f"No entendí {len(resumen['invalidas'])} línea(s): {muestra}{'…' if len(resumen['invalidas']) > 5 else ''}")
        ya_vistas = resumen.get("ya_vistas") or {}
        lineas.append(f"🗂️ {len(ya_vistas)} ya se habían visto. Los chats de Admin reciben el detalle con foto y lugar." if ya_vistas
                      else "Ninguna se había visto antes. Te aviso apenas una cámara las vea.")
        return "\n".join(lineas)

    def _cargar(self, chat_id, usuario_id: int, cuerpo: str):
        import vehiculos

        # Prompt 6 punto 7: el aviso "ya la viste" por placa (foto + Ubicaciones)
        # ya lo manda, una sola vez, el evento retro.hits que
        # procesar_carga_masiva_texto dispara (_on_evento) -- a los chats de
        # Admin, con el formato único del punto 1. Mandarlo aparte aquí
        # también lo duplicaría (y con el formato viejo).
        resumen = vehiculos.procesar_carga_masiva_texto(cuerpo, usuario_id)
        self._texto(chat_id, self._resumen_carga(resumen), self._con_menu())

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
        self._texto(chat_id, f"Agregada <b>{_esc(_fmt_placa(placa))}</b>{f': {_esc(motivo)}' if motivo else ''}. Los teléfonos la reciben en segundos.", self._con_menu())
        # Prompt 6 punto 7: alta individual por Telegram también dispara
        # "ya la viste" (mismo evento retro.hits que usa la carga masiva y
        # la alta individual de la web -- un solo camino, formato único).
        vistas = nexcam_db.vistas_de([placa]).get(placa)
        if vistas and vistas["vistas"]:
            try:
                import nexcam_api

                nexcam_api.broadcast("retro.hits", {"origen": "telegram", "hits": [vistas]})
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar 'ya la viste' de {placa} - {e}")

    def _quitar(self, chat_id, usuario_id: int, placa_raw: str):
        placa = nexcam_db.normalizar_placa(placa_raw or "")
        try:
            db.cambiar_estado_a_baja(placa, usuario_id, "Dada de baja por Telegram")
        except db.RegistroNoEncontradoError:
            return self._texto(chat_id, "Esa placa no está en la lista.", self._con_menu())
        except db.ValorInvalidoError as e:
            return self._texto(chat_id, _esc(str(e)))
        return self._texto(chat_id, f"Quitada <b>{_esc(_fmt_placa(placa))}</b> de la lista.", self._con_menu())

    def _filas_ultimas(self, items: list) -> list:
        return [
            f"{ICONO_VIVO.get(d.get('match_type') or ('exacta' if d['en_lista'] else None), '⚪')} 🚗 "
            f"{placa_destacada(_fmt_placa(d['placa']))}   {_reloj_corto(d['ts'])} · {_esc(d.get('operador') or '—')}"
            for d in items
        ]

    def _botones_ultimas_vivo(self, items: list, viendo_pagina: Optional[int]) -> dict:
        # La vista en vivo (viendo_pagina=None) muestra lo mismo que la
        # página 1 (las TG_ULTIMAS_VIVO_PAGINA más recientes): "Anteriores"
        # desde ahí debe saltar a la página 2, no repetir la 1.
        pagina_actual = viendo_pagina or 1
        nav = [{"text": "◀ Anteriores", "callback_data": f"uvivo:atras:{pagina_actual + 1}"}]
        if viendo_pagina is not None:
            nav.append({"text": "🔴 En vivo", "callback_data": "uvivo:vivo"})
        filas_placas, fila = [], []
        for d in items:
            fila.append({"text": f"📋 {_fmt_placa(d['placa'])}", "callback_data": f"ficha:{d['placa']}"})
            if len(fila) == 2:
                filas_placas.append(fila)
                fila = []
        if fila:
            filas_placas.append(fila)
        return {"inline_keyboard": [nav] + filas_placas}

    def _texto_ultimas_vivo(self, items: list) -> str:
        cabecera = "🕒 <b>Últimas placas</b> · en vivo"
        if not items:
            return f"{cabecera}\n\nTodavía no hay placas leídas."
        return f"{cabecera}\n\n{chr(10).join(self._filas_ultimas(items))}"

    def _texto_ultimas_pagina(self, pagina: int, total_paginas: int, items: list) -> str:
        cabecera = f"🕒 <b>Últimas placas</b> · página {pagina} de {total_paginas}"
        if not items:
            return f"{cabecera}\n\nSin más lecturas."
        return f"{cabecera}\n\n{chr(10).join(self._filas_ultimas(items))}"

    def _ultimas(self, chat_id):
        """
        Prompt 6 punto 3: al pulsar 🕒 Últimas se fija (si había) el
        mensaje EN VIVO anterior de este chat ("⏸ Ya no se actualiza") y
        se abre uno nuevo con las últimas TG_ULTIMAS_VIVO_PAGINA placas,
        que se va editando solo mientras la cámara lee (ver
        _tocar_ultimas_vivo/_refrescar_ultimas_vivo) hasta que se pulse
        de nuevo o pasen TG_ULTIMAS_VIVO_HORAS h (_vigilar_sesiones_inactivas).
        """
        self._congelar_ultimas_vivo(chat_id)
        items = nexcam_db.avistamientos_recientes(TG_ULTIMAS_VIVO_PAGINA, 0)
        texto = self._texto_ultimas_vivo(items)
        teclado = self._botones_ultimas_vivo(items, viendo_pagina=None)
        m = self._texto(chat_id, texto, {"reply_markup": teclado})
        if m and m.get("message_id"):
            self._ultimas_vivo[chat_id] = {
                "message_id": m["message_id"], "creado_en": time.time(), "timer": None,
                "viendo_pagina": None, "pendiente": False, "ultima_edicion": time.time(),
                "ultimo_texto": texto, "ultimo_teclado": teclado,
            }
        return m

    def _congelar_ultimas_vivo(self, chat_id):
        """Fija el mensaje de "Últimas en vivo" anterior de este chat (si
        lo hay) con "⏸ Ya no se actualiza" -- al pulsar 🕒 Últimas otra vez
        o a las TG_ULTIMAS_VIVO_HORAS h. Reusa el último texto/teclado ya
        armado (no vuelve a consultar la base): es el estado final tal
        como quedó, no uno recalculado en el momento de fijarlo."""
        info = self._ultimas_vivo.pop(chat_id, None)
        if not info:
            return
        if info.get("timer"):
            info["timer"].cancel()
        try:
            texto = f"{info.get('ultimo_texto') or ''}\n\n⏸ Ya no se actualiza."
            self._llamar("editMessageText", {
                "chat_id": chat_id, "message_id": info["message_id"], "text": texto,
                "parse_mode": "HTML", "disable_web_page_preview": True,
                "reply_markup": info.get("ultimo_teclado") or {"inline_keyboard": []},
            })
        except Exception as e:
            if "not modified" not in str(e).lower():
                self.log(f"Telegram: no se pudo fijar Últimas en vivo - {e}")

    def _tocar_ultimas_vivo(self):
        """Cada detection.hit llega aquí (Prompt 6 punto 3): a cada chat
        con un mensaje de "Últimas" EN VIVO (viendo_pagina is None) se le
        programa una actualización, coalescida a máximo una edición cada
        TG_ULTIMAS_VIVO_EDIT_SEGUNDOS si llegan varias lecturas juntas --
        un token-bucket simple: si ya pasó el tiempo mínimo desde la
        última edición, se programa para YA (espera 0); si no, se
        completa la espera que falte, nunca menos."""
        ahora = time.time()
        for chat_id, info in list(self._ultimas_vivo.items()):
            if info.get("viendo_pagina") is not None:
                continue  # mirando una página vieja: no se toca (Prompt 6 punto 3)
            if ahora - info["creado_en"] > TG_ULTIMAS_VIVO_HORAS * 3600:
                self._congelar_ultimas_vivo(chat_id)
                continue
            info["pendiente"] = True
            if info.get("timer"):
                continue  # ya hay una edición programada: esta lectura se le junta
            espera = max(0.0, TG_ULTIMAS_VIVO_EDIT_SEGUNDOS - (ahora - info.get("ultima_edicion", 0)))
            info["timer"] = threading.Timer(espera, lambda cid=chat_id: self._refrescar_ultimas_vivo(cid))
            info["timer"].daemon = True
            info["timer"].start()

    def _refrescar_ultimas_vivo(self, chat_id):
        info = self._ultimas_vivo.get(chat_id)
        if not info:
            return
        info["timer"] = None
        if not info.get("pendiente") or info.get("viendo_pagina") is not None:
            return
        info["pendiente"] = False
        info["ultima_edicion"] = time.time()
        items = nexcam_db.avistamientos_recientes(TG_ULTIMAS_VIVO_PAGINA, 0)
        texto = self._texto_ultimas_vivo(items)
        teclado = self._botones_ultimas_vivo(items, viendo_pagina=None)
        info["ultimo_texto"], info["ultimo_teclado"] = texto, teclado
        try:
            self._llamar("editMessageText", {
                "chat_id": chat_id, "message_id": info["message_id"], "text": texto,
                "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": teclado,
            })
        except Exception as e:
            if "not modified" not in str(e).lower():
                self.log(f"Telegram: no se pudo actualizar Últimas en vivo - {e}")

    def _mostrar_ultimas_vivo_pagina(self, chat_id, message_id, pagina: int):
        """[◀ Anteriores]: páginas viejas de TG_ULTIMAS_VIVO_PAGINA en
        TG_ULTIMAS_VIVO_PAGINA, sin tocar el mensaje mientras se mira una
        de ellas (Prompt 6 punto 3)."""
        total = nexcam_db.contar_avistamientos()
        total_paginas = max(1, -(-total // TG_ULTIMAS_VIVO_PAGINA))
        pagina = min(max(1, pagina), total_paginas)
        items = nexcam_db.avistamientos_recientes(TG_ULTIMAS_VIVO_PAGINA, (pagina - 1) * TG_ULTIMAS_VIVO_PAGINA)
        texto = self._texto_ultimas_pagina(pagina, total_paginas, items)
        teclado = self._botones_ultimas_vivo(items, viendo_pagina=pagina)
        info = self._ultimas_vivo.get(chat_id)
        if info and info["message_id"] == message_id:
            info["viendo_pagina"] = pagina
            info["ultimo_texto"], info["ultimo_teclado"] = texto, teclado
        try:
            self._llamar("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": texto,
                "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": teclado,
            })
        except Exception as e:
            if "not modified" not in str(e).lower():
                self.log(f"Telegram: no se pudo paginar Últimas - {e}")

    def _volver_a_ultimas_en_vivo(self, chat_id, message_id):
        info = self._ultimas_vivo.get(chat_id)
        if not info or info["message_id"] != message_id:
            return
        info["viendo_pagina"] = None
        info["pendiente"] = True
        info["timer"] = None
        self._refrescar_ultimas_vivo(chat_id)

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
                                       "usuario_id": usuario["id"] if usuario else None,
                                       # Ya le avisamos abajo en el mensaje de bienvenida (mismo propósito que
                                       # _avisar_chats_sin_usuario_una_vez): que ese aviso de arranque no se repita.
                                       "aviso_sin_usuario_enviado": usuario is None})
            self._guardar()
            self.codigo = str(secrets.randbelow(900000) + 100000)
            self.log(f"Telegram: chat vinculado ({nombre}). Nuevo código: {self.codigo}")
            pendiente = "" if usuario else "\n\nTodavía no tienes un usuario de NEXORA asignado a este chat: pídele a un Admin que te lo asigne desde el panel (NEXCAM → Telegram) para poder cargar o agregar placas."
            self._texto(chat_id, f"Listo, {_esc(nombre)}. Este chat recibirá los avisos de NEXCAM.{pendiente}\n\n{self._ayuda()}", self._con_menu())
            # Primera vez: le llega la app para instalarla en el teléfono.
            try:
                self.enviar_apk(chat_id)
            except Exception as e:
                self.log(f"Telegram: error enviando la app - {e}")
            return None
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
        if txt == B["app"] or cmd == "/app":
            return self.enviar_apk(chat_id)
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
        _, crudo = telegram_http.peticion(
            f"{self.api}/file/bot{self.cfg['token']}/{f['file_path']}", metodo="GET",
            timeout_lectura=35, log=self.log, etiqueta="getFile(descarga)",
        )
        try:
            texto = crudo.decode("utf-8")
            if "�" in texto:
                texto = crudo.decode("latin-1")
        except UnicodeDecodeError:
            texto = crudo.decode("latin-1")
        return self._cargar(chat_id, usuario["id"], texto)

    def _estado_alerta_por_mensaje(self, chat_id, message_id) -> Optional[dict]:
        """Encuentra, si existe, el `estado` de una alerta EN VIVO que
        tenga ese (chat_id, message_id) entre sus mensajes enviados --
        para saber si el botón 🔎 RGM se pulsó sobre una alerta (Prompt 6
        punto 1: se EDITA) o sobre otra cosa (ficha/buscar: se manda un
        mensaje nuevo)."""
        if message_id is None:
            return None
        estados = list(self._alertas_sin_sesion.values())
        for s in self.sesiones.values():
            estados.extend(s["alertas"].values())
        for estado in estados:
            for m in estado.get("mensajes") or []:
                if m.get("chat_id") == chat_id and m.get("message_id") == message_id:
                    return estado
        return None

    def _boton_pulsado(self, q: dict):
        """
        Prompt 6 punto 4 ("mucho delay al pasar de páginas"): el
        answerCallbackQuery se manda INMEDIATO, antes de calcular nada --
        antes, cada rama lo mandaba en un punto distinto (a veces después
        de una consulta SQL, a veces al final de todo), y el "cargando…"
        del botón se quedaba ahí mientras tanto. A cambio, el texto
        informativo que antes iba en ese aviso ("Ya hay una consulta en
        curso…") se pierde -- el mensaje final (editado o nuevo) ya avisa
        lo que haga falta, y lo que de verdad medía Gabriel (Fase 16.13:
        "cambia en menos de 1 s") es cuándo deja de girar el botón y
        cuándo cambia el mensaje, no el texto del aviso.
        """
        t0 = time.monotonic()
        chat_id = (q.get("message") or {}).get("chat", {}).get("id")
        if not self._autorizado(chat_id):
            return self._llamar("answerCallbackQuery", {"callback_query_id": q["id"], "text": "Chat no vinculado"})
        try:
            self._llamar("answerCallbackQuery", {"callback_query_id": q["id"]})
        except Exception:
            pass
        t_ack = time.monotonic()

        datos = q.get("data") or ""
        i = datos.find(":")
        accion, valor = (datos[:i], datos[i + 1:]) if i >= 0 else (datos, "")

        try:
            if accion == "auth":
                dev = nexcam_db.autorizar_dispositivo(valor)
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
                return self._buscar(chat_id, valor)
            if accion == "placa":
                v = nexcam_db.obtener_avistamiento(int(valor)) if valor.isdigit() else None
                foto = nexcam_db.ruta_foto_segura(v["snapshot"]) if v else None
                if foto:
                    return self._enviar_foto(chat_id, foto, f"🔢 Placa leída: <b>{_esc(_fmt_placa(v.get('leida') or v['placa']))}</b>, {_hora(v['ts'])}")
                return
            if accion in ("rgm", "rgm_judicial"):
                usuario = self._usuario_de_chat(chat_id)
                if usuario is None:
                    return
                import rgm_cola

                alcance = "RGM" if accion == "rgm" else "JUDICIAL"
                placa = nexcam_db.normalizar_placa(valor)
                es_admin = usuario["rol"] == "Admin" and usuario["estado"] == "activo"
                # Prompt 6 punto 1: si el botón vive en una alerta EN VIVO, el
                # resultado la EDITA (nunca manda un mensaje aparte). Solo se
                # rastrea para "rgm" puro -- "rgm_judicial" siempre manda un
                # mensaje nuevo (el botón ⚖️ no vive en la alerta original).
                message_id = (q.get("message") or {}).get("message_id")
                estado_alerta = self._estado_alerta_por_mensaje(chat_id, message_id) if accion == "rgm" else None
                # Prompt 7 bloque A: quién pidió esto se guarda EN LA BASE
                # (rgm_avisos), no en un diccionario en memoria -- sobrevive
                # a un reinicio de NEXORA entre el pedido y el resultado.
                resultado = rgm_cola.solicitar_consulta(
                    placa, alcance, "telegram", usuario["id"], chat_id=chat_id,
                    alerta_chat_id=chat_id if estado_alerta is not None else None,
                    alerta_message_id=message_id if estado_alerta is not None else None,
                )
                if resultado["accion"] == "SNAPSHOT_VIGENTE":
                    # Prompt 5 punto 1: ya hay dato vigente -- rgm.resultado NUNCA
                    # va a llegar para este caso (no se encoló nada), así que el
                    # mensaje con los datos se manda aquí mismo, de una vez.
                    try:
                        if alcance == "RGM" and estado_alerta is not None:
                            self._refrescar_texto_estado(estado_alerta)
                        elif alcance == "RGM":
                            self._texto(chat_id, self._texto_ficha_telegram(placa, es_admin=es_admin))
                        else:
                            self._enviar_lista_procesos(chat_id, placa, json.loads(resultado["snapshot"]["datos"]), es_admin)
                    except Exception as e:
                        self.log(f"Telegram: no se pudo mostrar el snapshot vigente de {placa} - {e}")
                else:
                    # YA_EN_COLA o ENCOLADA: Prompt 7 bloque A, punto 2 --
                    # respuesta inmediata con el puesto en la cola; su
                    # message_id se guarda para poder editarlo a los 3 min
                    # (ver _vigilar_esperas_rgm) si sigue sin resultado.
                    puesto = resultado.get("puesto") or 0
                    puesto_txt = f" (puesto {puesto} en la cola)" if puesto else ""
                    try:
                        m = self._texto(chat_id, f"🔎 Consultando {_esc(_fmt_placa(placa))}…{puesto_txt}")
                        if resultado.get("aviso_id") and m and m.get("message_id"):
                            db.actualizar_mensaje_espera_rgm(resultado["aviso_id"], m["message_id"])
                    except Exception as e:
                        self.log(f"Telegram: no se pudo responder el pedido de RGM de {placa} - {e}")
                return

            if accion == "noop":
                return  # el botón "1/4" (indicador de página): solo hacía falta el ack de arriba

            if accion == "procvolver":
                # Prompt 8d punto 3 (criterio del Prompt 9): el panel de
                # detalle/lista de procesos se BORRA al volver, nunca manda
                # nada nuevo.
                message_id = (q.get("message") or {}).get("message_id")
                try:
                    self._llamar("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
                except Exception:
                    pass
                return

            if accion in ("procver", "procpag", "procpdf"):
                usuario = self._usuario_de_chat(chat_id)
                es_admin = bool(usuario and usuario["rol"] == "Admin" and usuario["estado"] == "activo")
                if not es_admin:
                    return
                partes = valor.split(":")
                try:
                    id_proceso = int(partes[0])
                except (ValueError, IndexError):
                    return
                placa = partes[1] if len(partes) > 1 else ""
                message_id = (q.get("message") or {}).get("message_id")
                if accion == "procpdf":
                    self._enviar_pdf_proceso(chat_id, id_proceso, placa)
                else:
                    pagina = int(partes[2]) if accion == "procpag" and len(partes) > 2 and partes[2].isdigit() else 1
                    self._mostrar_detalle_proceso(chat_id, message_id, id_proceso, placa, pagina,
                                                   nuevo_mensaje=(accion == "procver"))
                return

            if accion == "uvivo":
                # Prompt 6 punto 3: [◀ Anteriores]/[🔴 En vivo] EDITAN este
                # mismo mensaje, nunca mandan uno nuevo.
                message_id = (q.get("message") or {}).get("message_id")
                i_sub = valor.find(":")
                sub, resto = (valor[:i_sub], valor[i_sub + 1:]) if i_sub >= 0 else (valor, "")
                if sub == "vivo":
                    self._volver_a_ultimas_en_vivo(chat_id, message_id)
                elif sub == "atras":
                    pagina = int(resto) if resto.isdigit() else 1
                    self._mostrar_ultimas_vivo_pagina(chat_id, message_id, pagina)
                return
        finally:
            # Prompt 6b punto 3: log de tiempos SIEMPRE (no solo si se pasa
            # de 1 s) -- Gabriel mide de verdad contando 10 toques reales
            # en su PC; "TIEMPO_BOTON" para poder filtrarlo fácil en la
            # consola (findstr/grep). ack = answerCallbackQuery (debe ser
            # ~0); total = hasta que termina de editar/mandar el mensaje.
            duracion_ack = t_ack - t0
            duracion_total = time.monotonic() - t0
            self.log(f"TIEMPO_BOTON accion={accion!r} ack={duracion_ack:.3f}s total={duracion_total:.3f}s")

    # ------------------------------------------------------------ bucle de mensajes

    def _bucle(self):
        """
        Prompt 6 punto 4: getUpdates en SU PROPIO hilo, que nunca espera a
        que termine de procesarse nada -- antes, este mismo hilo hacía
        getUpdates Y procesaba cada update (incluidos los botones, con sus
        llamadas de red), así que un mensaje lento de procesar (p.ej.
        _cargar con miles de placas) retrasaba la siguiente ronda de
        getUpdates, y con ella cualquier botón que llegara mientras tanto.
        Ahora este hilo SOLO pide actualizaciones y las deja en
        self._cola_updates; _trabajador_updates (hilo aparte) es quien
        realmente las atiende.
        """
        while not self.detenido:
            try:
                updates = self._llamar("getUpdates", {"offset": self.offset, "timeout": 30,
                                                        "allowed_updates": ["message", "callback_query"]})
                for u in updates:
                    self.offset = u["update_id"] + 1
                    self._cola_updates.put(u)
            except Exception as e:
                if self.detenido:
                    return
                self.log(f"Telegram: sin conexión con Telegram, reintento en 5 s: {e}")
                time.sleep(5)

    def _trabajador_updates(self):
        while not self.detenido:
            try:
                u = self._cola_updates.get(timeout=1)
            except queue.Empty:
                continue
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

    def _avisar_chats_sin_usuario_una_vez(self):
        """Prompt 6c punto 3: a cada chat vinculado SIN usuario de NEXORA
        se le avisa UNA sola vez (no en cada reinicio de NEXORA) que le
        falta un Admin que lo asigne -- cubre tanto chats vinculados antes
        de este cambio (nunca recibieron ningún aviso) como los nuevos,
        marcados ya al vincularse (ver _vincular, que manda su propio
        mensaje de bienvenida con el mismo aviso incluido)."""
        cambio = False
        for c in self.cfg["chats"]:
            if c.get("usuario_id") or c.get("aviso_sin_usuario_enviado"):
                continue
            try:
                self._texto(c["id"], "⚠️ Este chat no tiene usuario de NEXORA asignado: pídele al Admin que lo asigne en el panel (NEXCAM → Telegram).")
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar 'sin usuario' a {c.get('nombre') or c['id']} - {e}")
                continue
            c["aviso_sin_usuario_enviado"] = True
            cambio = True
        if cambio:
            self._guardar()

    def arrancar(self):
        if _pruebas_activas():
            # Salvaguarda explícita (incidente FASE 5): nunca un bot real
            # mientras corren pruebas, sin depender solo de que
            # tests/conftest.py redirija CONFIG_PATH -- esto cubre
            # cualquier otro punto de entrada (script suelto, notebook, etc.).
            self.log("  Telegram: NEXORA_PRUEBAS=1 o ejecución bajo pytest detectada -- el bot NO arranca.")
            return
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
            try:
                self._avisar_chats_sin_usuario_una_vez()
            except Exception as e:
                self.log(f"Telegram: no se pudo avisar a los chats sin usuario - {e}")
        except Exception as e:
            self.log(f"  Telegram: el token no funciona o no hay internet: {e}")

        self.log(f"  Telegram: para vincular un chat, escríbele al bot  /vincular {self.codigo}")
        self._hilo_bucle = threading.Thread(target=self._bucle, daemon=True, name="nexcam-telegram-bucle")
        self._hilo_bucle.start()
        self._hilo_trabajador = threading.Thread(target=self._trabajador_updates, daemon=True, name="nexcam-telegram-trabajador")
        self._hilo_trabajador.start()
        self._hilo_eventos = threading.Thread(target=self._vigilar_eventos_notificacion, daemon=True, name="nexcam-telegram-eventos")
        self._hilo_eventos.start()
        self._hilo_inactivas = threading.Thread(target=self._vigilar_sesiones_inactivas, daemon=True, name="nexcam-telegram-inactivas")
        self._hilo_inactivas.start()
        self._hilo_esperas_rgm = threading.Thread(target=self._vigilar_esperas_rgm, daemon=True, name="nexcam-telegram-esperas-rgm")
        self._hilo_esperas_rgm.start()

    def _revisar_esperas_rgm_una_vez(self):
        """Un ciclo de _vigilar_esperas_rgm, aparte para poder probarlo
        sin depender del `while` + `time.sleep(30)` real."""
        import rgm_cola

        for aviso in db.avisos_con_espera_vencida():
            if not aviso.get("mensaje_espera_id"):
                db.marcar_aviso_espera_notificado(aviso["id"])
                continue
            puesto = db.posicion_en_cola_rgm(aviso["item_id"])
            conectado_txt = "" if rgm_cola.trabajador_conectado() else " ¿Está abierta Consulta en la PC?"
            texto = f"⏳ Sigue en cola{f' (puesto {puesto})' if puesto else ''}.{conectado_txt}"
            try:
                self._llamar("editMessageText", {
                    "chat_id": aviso["chat_id"], "message_id": aviso["mensaje_espera_id"], "text": texto,
                })
            except Exception as e:
                if "not modified" not in str(e).lower():
                    self.log(f"Telegram: no se pudo avisar la espera del aviso {aviso['id']} - {e}")
            db.marcar_aviso_espera_notificado(aviso["id"])

    def _vigilar_esperas_rgm(self):
        """
        Prompt 7 bloque A, punto 5: a los MINUTOS_AVISO_ESPERA_RGM de
        pedir un RGM/Judicial sin resultado, edita UNA vez el "🔎
        Consultando…" a "⏳ Sigue en cola (puesto N). ¿Está abierta
        Consulta en la PC?" -- nunca manda un mensaje nuevo, y nunca se
        repite (avisado_espera en rgm_avisos).
        """
        while not self.detenido:
            time.sleep(30)
            try:
                self._revisar_esperas_rgm_una_vez()
            except Exception as e:
                self.log(f"Telegram: error en el vigilante de esperas RGM: {e}")

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
