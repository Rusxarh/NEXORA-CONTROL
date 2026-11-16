"""
nexcam_web.py
Interfaz web del panel NEXCAM (FASE 2) — exclusiva Admin, por sesión de
cookie (usuarios.requerir_admin), independiente del Bearer/X-Device-UUID
que usa la app (nexcam_api.py). Una sola página con pestañas por
querystring (?tab=...), mismo patrón que _tabs_gestion_masiva.html.

Pestañas: teléfonos, en vivo, buscar placa, ya vistas, aprendizaje OCR,
telegram (placeholder: el bot es FASE 3, todavía no existe).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request

import database as db
import nexcam_db
import nexcam_telegram
import usuarios
from templates import templates

router = APIRouter()

TABS = ("telefonos", "en-vivo", "buscar", "ya-vistas", "ocr", "telegram")


@router.get("/nexcam")
def nexcam_panel(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_admin),
    tab: str = "telefonos",
    q: str = "",
    mensaje: Optional[str] = None,
    tipo: str = "info",
):
    tab = tab if tab in TABS else "telefonos"
    contexto = {
        "request": request,
        "usuario_actual": usuario_actual,
        "ruta_activa": "nexcam",
        "tab": tab,
        "q": q,
        "mensaje": mensaje,
        "tipo_mensaje": tipo,
    }

    if tab == "telefonos":
        contexto["dispositivos"] = nexcam_db.listar_dispositivos()
    elif tab == "en-vivo":
        contexto["avistamientos"] = nexcam_db.avistamientos_recientes(30)
    elif tab == "buscar":
        placa_normalizada = nexcam_db.normalizar_placa(q) if q else ""
        contexto["buscado"] = bool(placa_normalizada)
        contexto["vehiculo"] = db.buscar_placa(placa_normalizada) if placa_normalizada else None
        vistas = nexcam_db.vistas_de([placa_normalizada]) if placa_normalizada else {}
        contexto["ficha"] = vistas.get(placa_normalizada)
    elif tab == "ya-vistas":
        contexto["ultimo_cruce"] = nexcam_db.obtener_ultimo_cruce()
    elif tab == "ocr":
        contexto["confusiones"] = nexcam_db.listar_confusiones()
    elif tab == "telegram":
        bot = nexcam_telegram.instancia()
        contexto["bot_info"] = bot.info() if bot else None
        contexto["usuarios_disponibles"] = [u for u in db.listar_usuarios() if u["estado"] == "activo"]

    return templates.TemplateResponse(request, "nexcam.html", contexto)


@router.post("/nexcam/telegram/{chat_id}/asignar-usuario")
def nexcam_asignar_usuario_telegram(chat_id: int, usuario_id: str = "", usuario_actual: dict = Depends(usuarios.requerir_admin)):
    bot = nexcam_telegram.instancia()
    if bot is None or not bot.activo:
        return usuarios.redirigir_con_mensaje("/nexcam?tab=telegram", "El bot de Telegram no está activo.", "error")

    nuevo_id = int(usuario_id) if usuario_id.strip().isdigit() else None
    if not bot.asignar_usuario_a_chat(chat_id, nuevo_id):
        return usuarios.redirigir_con_mensaje("/nexcam?tab=telegram", "Ese chat no existe.", "error")

    db.registrar_log(
        usuario_actual["id"], "NEXCAM_TELEGRAM_USUARIO_ASIGNADO",
        observaciones=f"chat_id={chat_id} -> usuario_id={nuevo_id}.",
    )
    return usuarios.redirigir_con_mensaje("/nexcam?tab=telegram", "Chat de Telegram actualizado.", "exito")


@router.post("/nexcam/telefonos/{device_uuid}/autorizar")
def nexcam_autorizar_telefono(device_uuid: str, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    dispositivo = nexcam_db.autorizar_dispositivo(device_uuid)
    if dispositivo is None:
        return usuarios.redirigir_con_mensaje("/nexcam?tab=telefonos", "Ese dispositivo no existe.", "error")
    db.registrar_log(
        usuario_actual["id"], "NEXCAM_TELEFONO_AUTORIZADO",
        observaciones=f"device_uuid={device_uuid} ({dispositivo.get('brand') or ''} {dispositivo.get('model') or ''}).".strip(),
    )
    return usuarios.redirigir_con_mensaje("/nexcam?tab=telefonos", "Teléfono autorizado.", "exito")


@router.post("/nexcam/telefonos/{device_uuid}/revocar")
def nexcam_revocar_telefono(device_uuid: str, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    dispositivo = nexcam_db.revocar_dispositivo(device_uuid)
    if dispositivo is None:
        return usuarios.redirigir_con_mensaje("/nexcam?tab=telefonos", "Ese dispositivo no existe.", "error")
    db.registrar_log(
        usuario_actual["id"], "NEXCAM_TELEFONO_REVOCADO",
        observaciones=f"device_uuid={device_uuid}. Sesión(es) activa(s) cerrada(s) de inmediato.",
    )
    return usuarios.redirigir_con_mensaje("/nexcam?tab=telefonos", "Teléfono revocado. Su sesión se cerró de inmediato.", "exito")
