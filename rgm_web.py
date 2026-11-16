"""
rgm_web.py
API usada por la página "Consulta" (vehiculos.py) para hablar con la cola
RGM/Judicial. NEXORA-CONTROL (Python) nunca consulta el portal real por
sí mismo -- solo la pestaña de Chrome con NEXORA-CONNECTOR cargado, abierta
por un Admin en /consulta, puede hacerlo; su JavaScript (no Python) es
quien de verdad llama a chrome.runtime.sendMessage/connect.

Mismo mecanismo de sesión que el resto del panel (cookie + requerir_admin,
sin CSRF): no existe ningún token CSRF en todo este proyecto, así que no
se introduce uno nuevo solo para estas rutas.

FASE 6/7: ya NO existe una página "Trabajador RGM" separada (plan
docs/PLAN-NEXCAM-FASE6-7.md) -- estas 4 rutas son solo API, consumidas
por el JavaScript de /consulta mientras esa pestaña sigue abierta.
"""

from fastapi import APIRouter, Depends, Form, Request

import database as db
import rgm_cola
import usuarios

router = APIRouter()


@router.post("/rgm/trabajador/latido")
def rgm_latido(usuario_actual: dict = Depends(usuarios.requerir_admin)):
    rgm_cola.registrar_latido_trabajador()
    return {"ok": True}


@router.post("/rgm/trabajador/siguiente")
def rgm_siguiente(usuario_actual: dict = Depends(usuarios.requerir_admin)):
    rgm_cola.registrar_latido_trabajador()
    item = rgm_cola.reservar_siguiente()
    return {"item": item}


@router.post("/rgm/trabajador/resultado")
async def rgm_resultado(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    b = await request.json()
    item_id = b.get("item_id")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "item_id inválido"}
    resultado = rgm_cola.completar_item(item_id, b.get("resultado") or {})
    return {"ok": True, **resultado}


@router.post("/rgm/trabajador/error")
async def rgm_error(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    b = await request.json()
    item_id = b.get("item_id")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "item_id inválido"}
    rgm_cola.marcar_error(item_id, str(b.get("mensaje") or "Error desconocido de la página Consulta"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Botón "Consultar RGM" / "Procesos judiciales" / "RGM + Judicial", desde
# Consulta o desde la ficha de Vehículos, para cualquier placa.
# ---------------------------------------------------------------------------

@router.post("/rgm/solicitar")
def rgm_solicitar(placa: str = Form(...), alcance: str = Form("RGM"), usuario_actual: dict = Depends(usuarios.requerir_admin)):
    placa_normalizada = placa.strip().upper()
    if alcance not in db.ALCANCES_CONSULTA_RGM:
        return usuarios.redirigir_con_mensaje("/consulta", "Alcance de consulta inválido.", "error")

    resultado = rgm_cola.solicitar_consulta(placa_normalizada, alcance, "panel", usuario_actual["id"])
    etiqueta = {"RGM": "RGM", "JUDICIAL": "procesos judiciales", "COMPLETO": "RGM + procesos judiciales"}[alcance]

    if resultado["accion"] == "SNAPSHOT_VIGENTE":
        mensaje = f"Ya hay una consulta de {etiqueta} de hace menos de 24 h para {placa_normalizada}; se usa esa."
    elif resultado["accion"] == "YA_EN_COLA":
        mensaje = f"Ya hay una consulta de {etiqueta} en curso para {placa_normalizada}."
    else:
        conectado = "sí" if rgm_cola.trabajador_conectado() else "no -- abre la pestaña Consulta en la PC"
        extra = f" {resultado['mensaje']}" if resultado.get("mensaje") else ""
        mensaje = f"Consulta de {etiqueta} encolada para {placa_normalizada}.{extra} ¿Hay alguien atendiendo la cola? {conectado}."

    return usuarios.redirigir_con_mensaje(f"/consulta?q={placa_normalizada}", mensaje, "info")
