"""
rgm_cola.py
Cola de consultas RGM/Judicial reales a través de NEXORA-CONNECTOR
(FASE 6/7 NEXCAM, plan docs/PLAN-NEXCAM-FASE6-7.md). database.py guarda la
fila (rgm_cola); aquí vive la regla de negocio: sin duplicados, un
snapshot vigente (24 h) evita una consulta innecesaria, "Judicial sola"
reutiliza el nombre de un snapshot RGM reciente (o encadena RGM primero si
no hay uno), y la página "Consulta" -- abierta y atendida en Chrome, único
lugar que puede hablar con el Connector -- es la que reserva y completa
los ítems. El servidor nunca consulta el portal por sí mismo.

Especificación de referencia leída COMPLETA antes de escribir este
archivo (y de rgm_web.py): nexora-connector-rgm-v0.1/background.js
(rama alcance-rgm), judicial.js, content.js. No se modificó judicial.js
ni content.js.

Decisiones de esta fase:
- La rama `alcance-rgm` del Connector agrega `alcance: "RGM"|"JUDICIAL"|
  "COMPLETO"` a CONSULTAR_PLACA: "RGM" ya NO encadena Judicial (el
  Connector mismo se detiene tras RGM_COMPLETADO/INCOMPLETO) -- resuelve
  de raíz la condición de carrera del intento anterior de esta
  integración (cancelar justo después de RGM_COMPLETADO ya no es
  necesario, ni existe). "JUDICIAL" arranca Judicial directo con un
  nombre, sin pasar por RGM.
- El resultado que la página "Consulta" manda a /rgm/trabajador/resultado
  es SIEMPRE la forma {"rgm": ... | null, "judicial": ... | null} --
  exactamente los campos `consulta.rgm`/`consulta.judicial` que ya
  entrega el Connector (consultaPublica), sin que el cliente tenga que
  distinguir el alcance. completar_item() guarda cada pieza presente como
  su propio snapshot (fuente 'RGM' o 'JUDICIAL' en snapshots_placa,
  NUNCA 'COMPLETO': ese valor no existe como fuente, solo como alcance
  pedido).
- El extractor real de RGM (content.js) solo entrega marca y "Año
  Correspondiente al Modelo" del vehículo -- nunca línea ni color (esos
  campos no existen en su resultado). _completar_datos_vehiculo por eso
  solo completa marca/modelo.
"""

import json
from datetime import datetime
from typing import Optional

import database as db

VENTANA_LATIDO_SEGUNDOS = 120
_ultimo_latido: Optional[datetime] = None


def registrar_latido_trabajador():
    """Llamado por la página Consulta cada ~20 s mientras sigue abierta
    atendiendo la cola."""
    global _ultimo_latido
    _ultimo_latido = datetime.now()


def trabajador_conectado() -> bool:
    return bool(_ultimo_latido) and (datetime.now() - _ultimo_latido).total_seconds() < VENTANA_LATIDO_SEGUNDOS


def _snapshot_vigente(snapshot: dict) -> bool:
    momento = datetime.strptime(str(snapshot["creado_en"])[:19], "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - momento).total_seconds() / 3600 < db.VIGENCIA_SNAPSHOT_RGM_HORAS


def _emitir(evento: str, payload: dict):
    try:
        import nexcam_api

        nexcam_api.broadcast(evento, payload)
    except Exception:
        pass


def _nombre_de_snapshot_rgm(snapshot: dict) -> Optional[str]:
    try:
        datos = json.loads(snapshot["datos"] or "{}")
    except Exception:
        return None
    nombre = ((datos.get("deudor_garante") or {}).get("nombre") or "").strip()
    return nombre or None


def solicitar_consulta(placa: str, alcance: str, origen: str, usuario_id: int, avistamiento_id: int = None,
                        _encadenado: bool = False) -> dict:
    """
    Punto de entrada único para pedir una consulta RGM/Judicial real.
    Devuelve {"accion": "SNAPSHOT_VIGENTE" | "YA_EN_COLA" | "ENCOLADA", ...}.

    alcance="JUDICIAL": usa el nombre de un snapshot RGM de menos de 24 h
    si existe; si no, encola "RGM" con encadenar_judicial=True (al
    completarse esa RGM, esta misma función se vuelve a llamar sola con
    "JUDICIAL" -- ver completar_item) y devuelve esa consulta RGM con un
    "mensaje" explicando que primero consulta RGM.
    """
    placa = placa.strip().upper()

    if alcance == "JUDICIAL":
        snapshot_rgm = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
        nombre = _nombre_de_snapshot_rgm(snapshot_rgm) if snapshot_rgm and _snapshot_vigente(snapshot_rgm) else None
        if not nombre:
            resultado = solicitar_consulta(placa, "RGM", origen, usuario_id, avistamiento_id, _encadenado=True)
            resultado["mensaje"] = "Primero consulto RGM para obtener el nombre."
            return resultado

        existente = db.item_rgm_pendiente_o_en_curso(placa, "JUDICIAL")
        if existente:
            return {"accion": "YA_EN_COLA", "item": existente}
        item_id = db.crear_item_rgm_cola(placa, "JUDICIAL", origen, usuario_id, avistamiento_id, nombre_judicial=nombre)
        item = db.obtener_item_rgm(item_id)
        _emitir("rgm.solicitud", {"id": item_id, "placa": placa, "alcance": "JUDICIAL"})
        return {"accion": "ENCOLADA", "item": item}

    if alcance == "COMPLETO":
        snap_rgm = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
        snap_jud = db.obtener_ultimo_snapshot_por_fuente(placa, "JUDICIAL")
        if snap_rgm and _snapshot_vigente(snap_rgm) and snap_jud and _snapshot_vigente(snap_jud):
            return {"accion": "SNAPSHOT_VIGENTE", "snapshot": {"rgm": snap_rgm, "judicial": snap_jud}}
    else:  # "RGM"
        snapshot = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
        if snapshot and _snapshot_vigente(snapshot) and not _encadenado:
            return {"accion": "SNAPSHOT_VIGENTE", "snapshot": snapshot}

    existente = db.item_rgm_pendiente_o_en_curso(placa, alcance)
    if existente:
        return {"accion": "YA_EN_COLA", "item": existente}

    item_id = db.crear_item_rgm_cola(placa, alcance, origen, usuario_id, avistamiento_id, encadenar_judicial=_encadenado)
    item = db.obtener_item_rgm(item_id)
    _emitir("rgm.solicitud", {"id": item_id, "placa": placa, "alcance": alcance})
    return {"accion": "ENCOLADA", "item": item}


def reservar_siguiente() -> Optional[dict]:
    """Llamado SOLO por la página 'Consulta' mientras atiende la cola."""
    return db.reservar_siguiente_item_rgm()


def _completar_datos_vehiculo_desde_rgm(placa: str, vehiculo_rgm: dict) -> dict:
    """
    Completa marca/modelo en `vehiculos` SOLO si la columna está vacía
    (nunca sobrescribe lo que ya vino de la lista del día o del alta
    manual). Si el valor de RGM difiere del que ya había, tampoco lo
    sobrescribe: se devuelve en `diferencias` para que la ficha lo
    muestre. línea/color nunca llegan de RGM (ver docstring del módulo),
    así que nunca se tocan aquí.
    """
    vehiculo = db.buscar_placa(placa)
    if vehiculo is None or not vehiculo_rgm:
        return {}

    diferencias = {}
    cambios = {}
    candidatos = {"marca": vehiculo_rgm.get("marca"), "modelo": vehiculo_rgm.get("anio_modelo")}
    for columna, valor_rgm in candidatos.items():
        valor_rgm = valor_rgm.strip() if isinstance(valor_rgm, str) else valor_rgm
        if not valor_rgm:
            continue
        valor_actual = (vehiculo.get(columna) or "").strip()
        if not valor_actual:
            cambios[columna] = valor_rgm
        elif valor_actual != str(valor_rgm).strip():
            diferencias[columna] = {"en_lista": valor_actual, "en_rgm": valor_rgm}

    if cambios:
        with db.get_connection() as conn:
            asignaciones = ", ".join(f"{columna} = ?" for columna in cambios)
            conn.execute(f"UPDATE vehiculos SET {asignaciones} WHERE placa = ?", (*cambios.values(), placa))

    return diferencias


def completar_item(item_id: int, resultado: dict) -> dict:
    """
    `resultado` es SIEMPRE {"rgm": ...|null, "judicial": ...|null} (ver
    docstring del módulo). Guarda cada pieza presente como su propio
    snapshot, marca el ítem OK, completa marca/modelo si corresponde
    (solo si vino `rgm`), encadena Judicial si esta RGM lo tenía pendiente,
    y avisa por SSE/Telegram.
    """
    item = db.obtener_item_rgm(item_id)
    if item is None:
        raise db.RegistroNoEncontradoError(f"Ítem de cola RGM id={item_id} no existe")

    resultado = resultado or {}
    rgm_resultado = resultado.get("rgm")
    judicial_resultado = resultado.get("judicial")

    diferencias = {}
    if rgm_resultado is not None:
        db.crear_snapshot(item["placa"], "RGM", json.dumps(rgm_resultado, ensure_ascii=False))
        diferencias = _completar_datos_vehiculo_desde_rgm(item["placa"], rgm_resultado.get("vehiculo") or {})
    if judicial_resultado is not None:
        db.crear_snapshot(item["placa"], "JUDICIAL", json.dumps(judicial_resultado, ensure_ascii=False))

    db.marcar_item_rgm_completado(item_id)

    encadenado = None
    if item["alcance"] == "RGM" and item["encadenar_judicial"] and rgm_resultado is not None:
        nombre = ((rgm_resultado.get("deudor_garante") or {}).get("nombre") or "").strip()
        if nombre:
            encadenado = solicitar_consulta(item["placa"], "JUDICIAL", item["origen"], item["solicitado_por"], item["avistamiento_id"])

    item_final = db.obtener_item_rgm(item_id)
    _emitir("rgm.resultado", {
        "item_id": item_id, "placa": item["placa"], "alcance": item["alcance"], "origen": item["origen"],
        "avistamiento_id": item["avistamiento_id"], "solicitado_por": item["solicitado_por"],
        "resultado": resultado, "diferencias": diferencias,
    })
    return {"item": item_final, "diferencias": diferencias, "encadenado": encadenado}


def marcar_error(item_id: int, mensaje: str):
    db.marcar_item_rgm_error(item_id, mensaje)
    item = db.obtener_item_rgm(item_id)
    if item and item["estado"] == "ERROR":
        _emitir("rgm.error", {"item_id": item_id, "placa": item["placa"], "alcance": item["alcance"], "error": mensaje})
