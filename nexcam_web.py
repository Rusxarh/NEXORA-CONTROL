"""
nexcam_web.py
Interfaz web del panel NEXCAM (FASE 2) — exclusiva Admin, por sesión de
cookie (usuarios.requerir_admin), independiente del Bearer/X-Device-UUID
que usa la app (nexcam_api.py). Una sola página con pestañas por
querystring (?tab=...), mismo patrón que _tabs_gestion_masiva.html.

Pestañas (FASE 7: "Buscar placa" se retiró -- la cumple la ficha de
Vehículos, /vehiculos/{placa}): teléfonos, en vivo, ya vistas,
aprendizaje OCR, telegram.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER

import database as db
import nexcam_db
import nexcam_telegram
import usuarios
from templates import templates

router = APIRouter()

TABS = ("telefonos", "en-vivo", "ya-vistas", "ocr", "telegram")


TAMANO_PAGINA_EN_VIVO = 30


@router.get("/nexcam")
def nexcam_panel(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_admin),
    tab: str = "telefonos",
    q: str = "",
    mensaje: Optional[str] = None,
    tipo: str = "info",
    pagina: int = 1,
):
    # Compatibilidad: un enlace viejo a "?tab=buscar&q=PLACA" lleva ahora
    # directo a la ficha de esa placa en Vehículos.
    if tab == "buscar":
        destino = f"/vehiculos/{nexcam_db.normalizar_placa(q)}" if q.strip() else "/"
        return RedirectResponse(url=destino, status_code=HTTP_303_SEE_OTHER)

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
        total = nexcam_db.contar_avistamientos()
        total_paginas = max(1, -(-total // TAMANO_PAGINA_EN_VIVO))  # división hacia arriba
        pagina_actual = min(max(1, pagina), total_paginas)
        contexto["avistamientos"] = nexcam_db.avistamientos_recientes(
            TAMANO_PAGINA_EN_VIVO, (pagina_actual - 1) * TAMANO_PAGINA_EN_VIVO
        )
        contexto["pagina_actual"] = pagina_actual
        contexto["total_paginas"] = total_paginas
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
