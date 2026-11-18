"""
nexcam_api.py
Router de la API que consume la app NEXCAM (Android) — FASE 1.

Contrato HTTP reproducido EXACTAMENTE desde NEXCAM/mock-api/server.js
(rutas, nombres de campos JSON, códigos HTTP y códigos de error), para que
el APK ya instalado funcione sin recompilar contra este servidor. La
lógica de negocio real vive en nexcam_db.py; este archivo solo traduce
HTTP <-> nexcam_db, igual que vehiculos.py hace con database.py.

Autenticación propia por Bearer token + X-Device-UUID, INDEPENDIENTE de
usuarios.requerir_autenticacion (sesión por cookie, exclusiva del dashboard
web para Admin): un Operador puede autenticarse aquí aunque
requerir_autenticacion lo bloquee en la web (Etapa 4.6).
"""

import asyncio
import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

import camara
import database as db
import nexcam_db
import usuarios

router = APIRouter()

# ---------------------------------------------------------------------------
# SSE: suscriptores en memoria (un solo proceso Uvicorn; igual límite que el
# resto de NEXORA, que tampoco tiene workers múltiples).
# ---------------------------------------------------------------------------

_suscriptores_sse: list = []

# Otros suscriptores de eventos, además de SSE (el bot de Telegram, FASE 3):
# funciones síncronas fn(evento, payload). Igual patrón que `oyentes` en
# mock-api/server.js. Una excepción de un oyente nunca debe romper la
# petición HTTP que disparó el evento.
_oyentes: list = []

# Prueba real con el teléfono: /api/detections/upload (y operator/status,
# camera/state) son rutas async, pero Telegram se llamaba con urllib
# (bloqueante) EN EL MISMO HILO del event loop -- cada sendPhoto/
# editMessage (segundos, red externa) congelaba TODO el servidor: la app
# esperaba los 12 s de su propio timeout, los latidos de otros teléfonos
# no entraban ("Sin conexión") y sus reintentos duplicaban avistamientos.
#
# broadcast() ya NO llama a los oyentes en el hilo de la petición: los
# encola en _cola_oyentes y un único hilo trabajador (FIFO, por eso
# "en orden") los reparte aparte. Las puestas en las colas SSE (asyncio.Queue)
# SÍ se quedan aquí mismo, síncronas: son locales, nunca bloquean, y
# asyncio.Queue no es thread-safe para llamarla desde otro hilo.
_cola_oyentes: "queue.Queue" = queue.Queue()
_hilo_oyentes: Optional[threading.Thread] = None
_hilo_oyentes_lock = threading.Lock()


def _repartir_a_oyentes():
    while True:
        evento, payload = _cola_oyentes.get()
        for fn in list(_oyentes):
            try:
                fn(evento, payload)
            except Exception:
                pass


def _asegurar_hilo_oyentes():
    global _hilo_oyentes
    if _hilo_oyentes is not None and _hilo_oyentes.is_alive():
        return
    with _hilo_oyentes_lock:
        if _hilo_oyentes is not None and _hilo_oyentes.is_alive():
            return
        _hilo_oyentes = threading.Thread(target=_repartir_a_oyentes, daemon=True, name="nexcam-oyentes")
        _hilo_oyentes.start()


def suscribir(fn):
    _oyentes.append(fn)
    _asegurar_hilo_oyentes()


def broadcast(evento: str, payload: dict):
    mensaje = f"event: {evento}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    for cola in list(_suscriptores_sse):
        try:
            cola.put_nowait(mensaje)
        except asyncio.QueueFull:
            pass
    if _oyentes:
        _asegurar_hilo_oyentes()
        _cola_oyentes.put((evento, payload))


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

async def _cuerpo(request: Request) -> dict:
    """Igual que readBody en server.js: JSON inválido o vacío -> {}."""
    try:
        datos = await request.json()
        return datos if isinstance(datos, dict) else {}
    except Exception:
        return {}


def _error_json(exc: nexcam_db.ErrorNexcamAPI):
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


def _autenticar(authorization: Optional[str], x_device_uuid: Optional[str]) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    try:
        return nexcam_db.autenticar(token, x_device_uuid)
    except nexcam_db.ErrorNexcamAPI as e:
        _error_json(e)


def nexcam_auth(
    authorization: Optional[str] = Header(None),
    x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID"),
) -> dict:
    """Dependency de FastAPI para las rutas que exigen sesión de la app."""
    return _autenticar(authorization, x_device_uuid)


# ---------------------------------------------------------------------------
# Dispositivo
# ---------------------------------------------------------------------------

@router.post("/api/device/register")
async def device_register(request: Request):
    b = await _cuerpo(request)
    uuid = str(b.get("device_uuid") or "")[:64]
    if not uuid:
        raise HTTPException(status_code=422, detail={"message": "device_uuid requerido"})
    dispositivo, es_nuevo = nexcam_db.registrar_dispositivo(
        uuid,
        android_id=b.get("androidId"),
        brand=b.get("brand"),
        model=b.get("model"),
        os_version=b.get("osVersion"),
        app_version=b.get("appVersion"),
    )
    if es_nuevo:
        broadcast("device.new", dispositivo)
    return {"device_uuid": uuid, "authorized": bool(dispositivo["authorized"])}


@router.get("/api/device/status")
def device_status(device_uuid: str = ""):
    dispositivo = nexcam_db.obtener_dispositivo(device_uuid)
    return {
        "authorized": bool(dispositivo and dispositivo["authorized"]),
        "revoked": bool(dispositivo and dispositivo["revoked"]),
    }


# ---------------------------------------------------------------------------
# Autenticación de la app
# ---------------------------------------------------------------------------

@router.post("/api/auth/login")
async def auth_login(request: Request, x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    b = await _cuerpo(request)
    device_uuid = b.get("device_uuid") or x_device_uuid
    try:
        resultado = nexcam_db.iniciar_sesion(b.get("username") or "", b.get("password") or "", device_uuid)
    except nexcam_db.ErrorNexcamAPI as e:
        _error_json(e)
    return {
        "token": resultado["token"],
        "expiresAt": resultado["expires_at"],
        "user": resultado["user"],
    }


@router.post("/api/auth/logout")
def auth_logout(authorization: Optional[str] = Header(None),
                 x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    nexcam_db.cerrar_sesion(sesion["token_hash"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Placas (lectura para la app)
# ---------------------------------------------------------------------------

@router.get("/api/plates")
def api_plates(since: int = 0, authorization: Optional[str] = Header(None),
                x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    usuario = db.obtener_usuario_por_id(sesion["usuario_id"])
    if since:
        plates = nexcam_db.placas_cambiadas_desde(since, usuario)
    else:
        plates = nexcam_db.placas_activas_para_app(usuario)
    return {"plates": plates, "serverTime": _ahora_ms(), "full": not since}


def _ahora_ms() -> int:
    import time

    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Avistamientos
# ---------------------------------------------------------------------------

@router.post("/api/detections/upload")
async def detections_upload(request: Request, authorization: Optional[str] = Header(None),
                             x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    b = await _cuerpo(request)
    try:
        det = nexcam_db.registrar_avistamiento(
            sesion, b.get("placa"), b.get("leida"), b.get("matchType"), b.get("confidence"),
            b.get("sharpness"), b.get("frames"), b.get("source"), b.get("lat"), b.get("lng"),
            b.get("ts"), b.get("snapshot_b64"), b.get("vehicle_b64"),
        )
    except nexcam_db.ErrorNexcamAPI as e:
        _error_json(e)

    ya_existia = det.pop("_ya_existia", False)
    if not ya_existia:
        # Actividad CAMARA: máximo una vez cada 10 min por usuario (regla
        # pedida para esta integración; camara.registrar_actividad_camara no
        # tiene ese límite por sí sola, así que se aplica aquí, sin
        # modificar camara.py).
        _registrar_actividad_camara_throttled(sesion["usuario_id"])

        # mock-api/server.js agrega `device` ("marca modelo") a cada
        # avistamiento transmitido; nexcam_avistamientos no guarda ese
        # texto, así que se arma aquí solo para el evento (el bot de
        # Telegram lo usa en la cámara en vivo -- sin esto, siempre
        # mostraba el "Teléfono" genérico).
        broadcast("detection.hit", {**det, "device": nexcam_db.descripcion_dispositivo(sesion["device_uuid"])})

        # RGM automático (FASE 6): SOLO coincidencia exacta de una placa que
        # está en lista en este momento. Una "posible coincidencia" (gemelo
        # O/Q/D) o una lectura sin coincidencia nunca encolan nada -- evita
        # cientos de consultas al portal real por lecturas erróneas del OCR.
        # Va DESPUÉS de "detection.hit" a propósito: el bot de Telegram debe
        # guardar a qué mensaje responder (_mensajes_por_avistamiento) antes
        # de que exista la más mínima posibilidad de un rgm.resultado.
        if det["en_lista"] and det.get("match_type") != "probable":
            try:
                import rgm_cola

                rgm_cola.solicitar_consulta(det["placa"], "RGM", "camara", sesion["usuario_id"], avistamiento_id=det["id"])
            except Exception:
                pass

    return {"ok": True, "id": det["id"], "enLista": bool(det["en_lista"]), "motivo": det["motivo"]}


_VENTANA_ACTIVIDAD_CAMARA_SEGUNDOS = 10 * 60


def _registrar_actividad_camara_throttled(usuario_id: int):
    from datetime import datetime

    usuario = db.obtener_usuario_por_id(usuario_id)
    if usuario is None or usuario["rol"] != "Operador":
        return
    ultima = usuario.get("ultima_actividad_camara")
    if ultima:
        transcurrido = (datetime.now() - datetime.strptime(str(ultima)[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
        if transcurrido < _VENTANA_ACTIVIDAD_CAMARA_SEGUNDOS:
            return
    camara.registrar_actividad_camara(usuario_id)


@router.post("/api/operator/status")
async def operator_status(request: Request, authorization: Optional[str] = Header(None),
                           x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    b = await _cuerpo(request)
    pending = b.get("pending") or 0
    nexcam_db.marcar_pendientes(sesion["device_uuid"], pending)
    broadcast("connectivity", {"device_uuid": sesion["device_uuid"], "online": True, "pending": pending})
    return {"ok": True, "serverTime": _ahora_ms()}


@router.post("/api/camera/state")
async def camera_state(request: Request, authorization: Optional[str] = Header(None),
                        x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    b = await _cuerpo(request)
    payload = {
        "device_uuid": sesion["device_uuid"],
        "operador": sesion["nombre"],
        "ts": _ahora_ms(),
        "device": nexcam_db.descripcion_dispositivo(sesion["device_uuid"]),
    }
    broadcast("camera.started" if b.get("activa") else "camera.stopped", payload)
    if b.get("activa"):
        _registrar_actividad_camara_throttled(sesion["usuario_id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Aprendizaje del OCR
# ---------------------------------------------------------------------------

@router.post("/api/ocr/feedback")
async def ocr_feedback(request: Request, authorization: Optional[str] = Header(None),
                        x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    sesion = _autenticar(authorization, x_device_uuid)
    b = await _cuerpo(request)
    items = b.get("items") if isinstance(b.get("items"), list) else []
    resultado = nexcam_db.registrar_feedback_ocr(items[:200], sesion["nombre"], sesion["device_uuid"])
    if resultado["aprendidas"]:
        broadcast("ocr.learned", {"aprendidas": resultado["aprendidas"]})
    return {"ok": True, **resultado}


@router.get("/api/ocr/confusions")
def ocr_confusions(authorization: Optional[str] = Header(None),
                    x_device_uuid: Optional[str] = Header(None, alias="X-Device-UUID")):
    _autenticar(authorization, x_device_uuid)
    return {"confusions": nexcam_db.listar_confusiones()}


# ---------------------------------------------------------------------------
# Stream en vivo (SSE)
# ---------------------------------------------------------------------------

@router.get("/app/stream")
async def app_stream():
    cola: asyncio.Queue = asyncio.Queue(maxsize=200)
    _suscriptores_sse.append(cola)

    async def generador():
        yield 'event: connected\ndata: {"ok": true}\n\n'
        try:
            while True:
                mensaje = await cola.get()
                yield mensaje
        except asyncio.CancelledError:
            pass
        finally:
            if cola in _suscriptores_sse:
                _suscriptores_sse.remove(cola)

    return StreamingResponse(
        generador(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# Fotos: solo Admin con sesión WEB (cookie), nunca con el Bearer de la app.
# ---------------------------------------------------------------------------

@router.get("/fotos/{fecha}/{archivo}")
def servir_foto(fecha: str, archivo: str, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    ruta = nexcam_db.ruta_foto_segura(f"/fotos/{fecha}/{archivo}")
    if ruta is None:
        raise HTTPException(status_code=404, detail={"message": "La foto ya no existe (se borran pasados los días configurados)."})
    return FileResponse(ruta, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Descarga de la app (enlace temporal de 24 h cuando el APK pesa más de lo
# que Telegram deja enviar). Token-gated, sin Bearer ni cookie: el teléfono
# lo abre directo desde el navegador, antes incluso de tener la app.
# ---------------------------------------------------------------------------

@router.get("/app/nexcam.apk")
def descargar_apk(k: str = ""):
    import nexcam_telegram

    if not nexcam_telegram.enlace_apk_temporal_vigente(k):
        raise HTTPException(status_code=403, detail={"message": "Enlace vencido. Pide la app otra vez al bot de Telegram."})
    ruta = nexcam_telegram.ruta_apk()
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail={"message": "No hay app compilada en la PC."})
    return FileResponse(ruta, media_type="application/vnd.android.package-archive", filename="NEXCAM.apk")
