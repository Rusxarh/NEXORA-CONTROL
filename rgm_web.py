"""
rgm_web.py
Página "Trabajador RGM" (FASE 6 NEXCAM): la ÚNICA superficie por la que
NEXORA-CONTROL habla con nexora-connector-rgm-v0.1 (el Connector). El
servidor NUNCA puede consultar el portal de RGM/Judicial por sí mismo --
solo una pestaña de Chrome, con la extensión instalada, abierta por un
Admin, puede hacerlo. Esta página se deja abierta en la PC; su JavaScript
(no Python) es quien de verdad llama a chrome.runtime.sendMessage/connect.

Mismo mecanismo de sesión que el resto del panel (cookie + requerir_admin,
sin CSRF): no existe ningún token CSRF en todo este proyecto, así que no
se introduce uno nuevo solo para esta página.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request

import database as db
import rgm_cola
import usuarios
from templates import templates

router = APIRouter()


@router.get("/rgm/trabajador")
def rgm_trabajador_vista(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    return templates.TemplateResponse(
        request, "rgm_trabajador.html",
        {
            "request": request, "usuario_actual": usuario_actual, "ruta_activa": "rgm_trabajador",
            "ultimos": db.listar_ultimos_items_rgm(20),
        },
    )


@router.post("/rgm/trabajador/latido")
def rgm_trabajador_latido(usuario_actual: dict = Depends(usuarios.requerir_admin)):
    rgm_cola.registrar_latido_trabajador()
    return {"ok": True}


@router.post("/rgm/trabajador/siguiente")
def rgm_trabajador_siguiente(usuario_actual: dict = Depends(usuarios.requerir_admin)):
    rgm_cola.registrar_latido_trabajador()
    item = rgm_cola.reservar_siguiente()
    return {"item": item}


@router.post("/rgm/trabajador/resultado")
async def rgm_trabajador_resultado(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    b = await request.json()
    item_id = b.get("item_id")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "item_id inválido"}
    resultado = rgm_cola.completar_item(item_id, b.get("resultado") or {})
    return {"ok": True, **resultado}


@router.post("/rgm/trabajador/error")
async def rgm_trabajador_error(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    b = await request.json()
    item_id = b.get("item_id")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "item_id inválido"}
    rgm_cola.marcar_error(item_id, str(b.get("mensaje") or "Error desconocido del Trabajador RGM"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Botón "Consultar RGM" / "Procesos judiciales" del panel (cualquier placa).
# ---------------------------------------------------------------------------

@router.post("/rgm/solicitar")
def rgm_solicitar(placa: str = Form(...), tipo: str = Form("RGM"), usuario_actual: dict = Depends(usuarios.requerir_admin)):
    placa_normalizada = placa.strip().upper()
    if tipo not in db.TIPOS_CONSULTA_RGM:
        return usuarios.redirigir_con_mensaje("/nexcam?tab=buscar", "Tipo de consulta inválido.", "error")

    resultado = rgm_cola.solicitar_consulta(placa_normalizada, tipo, "panel", usuario_actual["id"])
    etiqueta = "RGM" if tipo == "RGM" else "procesos judiciales"
    if resultado["accion"] == "SNAPSHOT_VIGENTE":
        mensaje = f"Ya hay una consulta de {etiqueta} de hace menos de 24 h para {placa_normalizada}; se usa esa."
    elif resultado["accion"] == "YA_EN_COLA":
        mensaje = f"Ya hay una consulta de {etiqueta} en curso para {placa_normalizada}."
    else:
        conectado = "Sí" if rgm_cola.trabajador_conectado() else "No hay Trabajador RGM conectado: abre /rgm/trabajador en Chrome, en la PC."
        mensaje = f"Consulta de {etiqueta} encolada para {placa_normalizada}. Trabajador RGM conectado: {conectado}"

    return usuarios.redirigir_con_mensaje(f"/nexcam?tab=buscar&q={placa_normalizada}", mensaje, "info")
