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

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, StreamingResponse

import database as db
import nexcam_api
import rgm_cola
import usuarios
from templates import templates

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
    # Prompt 8b punto 2: nunca un 500 aquí -- si completar_item falla por
    # lo que sea (antes: IntegrityError de la FK de snapshots_placa en una
    # placa que no está en vehiculos; cualquier otro fallo futuro queda
    # igual cubierto), el ítem se marca ERROR con el motivo real en vez de
    # quedarse EN_CURSO para siempre (eso era lo que la cola de Consulta
    # mostraba como "se quedó en curso" aunque el portal ya había
    # respondido: la excepción pasaba por encima de marcar_item_rgm_completado
    # y de _emitir, así que ni el estado ni el aviso por SSE llegaban).
    # Prompt 8b/8c: el Connector manda "plan" ("A" sin pestaña o "B" con
    # pestaña/ventana) y, si fue "B", "plan_motivo" (por qué no pudo usar
    # el plan A) -- se deja constancia en /consulta/diagnostico ("plan A
    # OK" / "plan B porque: <motivo exacto>", texto exacto del Prompt 8c
    # punto 2) sin importar si la consulta terminó bien o en error.
    plan = b.get("plan")
    if plan in ("A", "B"):
        item_previo = db.obtener_item_rgm(item_id)
        placa_previa = item_previo["placa"] if item_previo else None
        if plan == "A":
            db.registrar_diagnostico_rgm("plan A OK", item_id, placa_previa)
        else:
            db.registrar_diagnostico_rgm(
                f"plan B porque: {b.get('plan_motivo') or 'motivo no informado'}", item_id, placa_previa
            )

    try:
        resultado = rgm_cola.completar_item(item_id, b.get("resultado") or {})
    except Exception as e:
        rgm_cola.marcar_error(item_id, f"Error guardando el resultado: {e}")
        return {"ok": False, "motivo": str(e)}
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
def rgm_solicitar(placa: str = Form(...), alcance: str = Form("RGM"), volver: str = Form(""),
                   usuario_actual: dict = Depends(usuarios.requerir_admin)):
    placa_normalizada = placa.strip().upper()
    destino = volver if usuarios._es_ruta_segura(volver) else f"/consulta?q={placa_normalizada}"
    if alcance not in db.ALCANCES_CONSULTA_RGM:
        return usuarios.redirigir_con_mensaje(destino, "Alcance de consulta inválido.", "error")

    resultado = rgm_cola.solicitar_consulta(placa_normalizada, alcance, "panel", usuario_actual["id"])
    etiqueta = {"RGM": "RGM", "JUDICIAL": "procesos judiciales", "COMPLETO": "RGM + procesos judiciales"}[alcance]

    if resultado["accion"] == "SNAPSHOT_VIGENTE":
        mensaje = f"Ya hay una consulta de {etiqueta} de hace menos de 24 h para {placa_normalizada}; se usa esa."
    elif resultado["accion"] == "YA_EN_COLA":
        mensaje = f"Ya hay una consulta de {etiqueta} en curso para {placa_normalizada}."
    else:
        # Prompt 8: Judicial ya no depende de la pestaña Consulta (lo
        # atiende un hilo propio de NEXORA) -- solo RGM sigue necesitando
        # que el Connector esté conectado. Se mira el alcance REAL del
        # ítem encolado (una "JUDICIAL" pedida sin nombre reciente
        # encadena una "RGM" primero, ver rgm_cola.solicitar_consulta).
        extra = f" {resultado['mensaje']}" if resultado.get("mensaje") else ""
        if resultado["item"]["alcance"] == "JUDICIAL":
            mensaje = f"Consulta de {etiqueta} encolada para {placa_normalizada}.{extra} NEXORA la atiende sola en segundo plano."
        else:
            conectado = "sí" if rgm_cola.trabajador_conectado() else "no -- abre la pestaña Consulta en la PC"
            mensaje = f"Consulta de {etiqueta} encolada para {placa_normalizada}.{extra} ¿Hay alguien atendiendo la cola de RGM? {conectado}."

    return usuarios.redirigir_con_mensaje(destino, mensaje, "info")


# ---------------------------------------------------------------------------
# Diagnóstico (Prompt 7 bloque A, punto 6): página pequeña, solo Admin, con
# los últimos pasos de cada consulta (pedido -> reservado -> resultado ->
# aviso) -- nunca se manda nada de esto por Telegram.
# ---------------------------------------------------------------------------

@router.get("/consulta/diagnostico")
def consulta_diagnostico(request: Request, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    return templates.TemplateResponse(
        request,
        "consulta_diagnostico.html",
        {
            "request": request,
            "usuario_actual": usuario_actual,
            "ruta_activa": "consulta",
            "pasos": db.listar_diagnostico_rgm(50),
        },
    )


# ---------------------------------------------------------------------------
# Prompt 8 punto 5: tiempo real en Consulta/ficha -- stream SSE PROPIO,
# detrás de sesión de Admin (a diferencia de /app/stream, que no exige
# sesión porque lo usa la app con Bearer; no se reutiliza esa ruta para
# no mandar eventos sensibles -- RGM/Judicial -- a quien no tenga sesión
# web). Mismo broadcast() de siempre (rgm.solicitud/rgm.resultado/rgm.error),
# un consumidor más.
# ---------------------------------------------------------------------------

@router.get("/rgm/stream")
async def rgm_stream(usuario_actual: dict = Depends(usuarios.requerir_admin)):
    return nexcam_api.respuesta_sse()


# ---------------------------------------------------------------------------
# Prompt 8 punto 3: "Ver proceso" trae detalle + actuaciones de un
# radicado EN CUALQUIER MOMENTO (la cola puede seguir con otras placas),
# directo de la API real -- nunca abre el portal al frente. Se llama por
# AJAX desde las tarjetas de judicial_tarjetas() (_macros.html).
# ---------------------------------------------------------------------------

@router.post("/rgm/judicial/ver-proceso")
def rgm_ver_proceso(placa: str = Form(...), id_proceso: int = Form(...), forzar: bool = Form(False),
                     usuario_actual: dict = Depends(usuarios.requerir_admin)):
    import judicial_directo
    import judicial_formato as jf

    placa_normalizada = placa.strip().upper()
    try:
        # Prompt 8d punto 3: cacheado 6 h por id_proceso (antes se guardaba
        # bajo la fuente fija "JUDICIAL_DETALLE", sin el id -- dos procesos
        # distintos de la misma placa se pisaban el caché uno al otro).
        # "forzar" (botón "🔄 Actualizar") lo vuelve a pedir de verdad.
        resultado = judicial_directo.obtener_detalle_y_actuaciones_cacheado(placa_normalizada, id_proceso, forzar=forzar)
    except judicial_directo.ErrorJudicialDirecto as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    db.registrar_diagnostico_rgm("ver proceso", None, placa_normalizada, f"id_proceso={id_proceso}")

    # Prompt 8d punto 4: la web usa las MISMAS fechas dd/mm/aaaa y nombres
    # con mayúscula inicial que Telegram -- se formatean aquí (Python, un
    # solo lugar) en vez de duplicar esa lógica en JavaScript. Los campos
    # crudos ("detalle"/"actuaciones") se mantienen para no romper nada
    # que ya los lea.
    detalle = resultado.get("detalle") or {}
    actuaciones = resultado.get("actuaciones") or []
    detalle_formateado = {
        "radicado": detalle.get("radicado"),
        "despacho": jf.normalizar_nombre_visible(detalle.get("despacho")),
        "ponente": jf.normalizar_nombre_visible(detalle.get("ponente")) if detalle.get("ponente") else None,
        "tipo_proceso": detalle.get("tipo_proceso"),
        "clase_proceso": detalle.get("clase_proceso"),
        "ubicacion": jf.normalizar_nombre_visible(detalle.get("ubicacion")) if detalle.get("ubicacion") else None,
        "fecha_proceso": jf.formatear_fecha_corta(detalle.get("fecha_proceso")),
    }
    actuaciones_formateadas = [
        {
            "fecha_actuacion": jf.formatear_fecha_corta(a.get("fecha_actuacion")),
            "actuacion": a.get("actuacion"),
            "anotacion": (a.get("anotacion") or "").strip(),
            "con_documentos": bool(a.get("con_documentos")),
        }
        for a in actuaciones
    ]
    return {"ok": True, **resultado, "detalle_formateado": detalle_formateado,
            "actuaciones_formateadas": actuaciones_formateadas}


@router.get("/rgm/judicial/pdf-proceso")
def rgm_pdf_proceso(placa: str, id_proceso: int, usuario_actual: dict = Depends(usuarios.requerir_admin)):
    """Prompt 8d punto 4: botón "Descargar PDF" en la web -- mismo PDF
    (detalle + TODAS las actuaciones) que el "📑 Proceso completo (PDF)"
    de Telegram, un solo generador (judicial_pdf.py)."""
    import judicial_directo
    import judicial_pdf
    from fastapi import Response

    placa_normalizada = placa.strip().upper()
    try:
        generado = judicial_pdf.generar_y_guardar_pdf_proceso(placa_normalizada, id_proceso)
    except judicial_directo.ErrorJudicialDirecto as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    db.registrar_diagnostico_rgm("PDF de proceso descargado", None, placa_normalizada, f"id_proceso={id_proceso}")
    return Response(
        content=generado["contenido"], media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{generado["ruta"].name}"'},
    )
