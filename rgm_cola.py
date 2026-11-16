"""
rgm_cola.py
Cola de consultas RGM/Judicial reales a través de NEXORA-CONNECTOR
(FASE 6 NEXCAM). database.py guarda la fila (rgm_cola); aquí vive la
regla de negocio: sin duplicados, un snapshot vigente (24 h) evita una
consulta innecesaria, y la página "Trabajador RGM" (abierta y atendida en
Chrome, único lugar que puede hablar con el Connector) es la que reserva
y completa los ítems -- el servidor nunca consulta el portal por sí mismo.

Especificación de referencia leída COMPLETA antes de escribir este
archivo (y de rgm_web.py): nexora-connector-rgm-v0.1/background.js,
judicial.js, content.js. No se modificó ninguno de los tres.

HALLAZGO IMPORTANTE (decisión ya discutida y aprobada por Gabriel):
- El protocolo externo CONSULTAR_PLACA del Connector NO distingue "solo
  RGM" de "RGM+Judicial": si RGM entrega un nombre de deudor/garante
  utilizable, el Connector encadena Judicial automáticamente, SIN
  esperar confirmación de la Web. Para un ítem de tipo 'RGM', la
  Trabajador RGM cancela (acción CANCELAR_CONSULTA) apenas recibe
  RGM_COMPLETADO/RGM_INCOMPLETO -- pero como el Connector sigue de
  inmediato tras emitir ese evento, existe una condición de carrera real:
  ocasionalmente Judicial puede alcanzar a arrancar de todas formas antes
  de que la cancelación llegue. Queda documentado como limitación
  conocida (no resuelta, no silenciada), no se modificó el Connector.
- El extractor real de RGM (content.js) solo entrega marca y
  "Año Correspondiente al Modelo" del vehículo -- NUNCA línea ni color
  (esos campos no existen en su resultado). _completar_datos_vehiculo
  por eso solo completa marca/modelo, nunca línea/color.
"""

import json
from datetime import datetime
from typing import Optional

import database as db

VENTANA_LATIDO_SEGUNDOS = 120
_ultimo_latido: Optional[datetime] = None


def registrar_latido_trabajador():
    """Llamado por la página Trabajador RGM cada ~20 s mientras sigue abierta."""
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


def solicitar_consulta(placa: str, tipo: str, origen: str, usuario_id: int, avistamiento_id: int = None) -> dict:
    """
    Punto de entrada único para pedir una consulta RGM/Judicial real.
    Devuelve {"accion": "SNAPSHOT_VIGENTE" | "YA_EN_COLA" | "ENCOLADA", ...}.
    Nunca duplica: si ya hay un snapshot de menos de 24 h para esa placa y
    tipo, lo usa; si ya hay un ítem PENDIENTE/EN_CURSO, no crea otro.
    """
    placa = placa.strip().upper()

    snapshot = db.obtener_ultimo_snapshot_por_fuente(placa, tipo)
    if snapshot and _snapshot_vigente(snapshot):
        return {"accion": "SNAPSHOT_VIGENTE", "snapshot": snapshot}

    existente = db.item_rgm_pendiente_o_en_curso(placa, tipo)
    if existente:
        return {"accion": "YA_EN_COLA", "item": existente}

    item_id = db.crear_item_rgm_cola(placa, tipo, origen, usuario_id, avistamiento_id)
    item = db.obtener_item_rgm(item_id)
    _emitir("rgm.solicitud", {"id": item_id, "placa": placa, "tipo": tipo})
    return {"accion": "ENCOLADA", "item": item}


def reservar_siguiente() -> Optional[dict]:
    """Llamado SOLO por la Trabajador RGM: toma una consulta a la vez."""
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
    Guarda `resultado` (el objeto crudo que entregó el Connector) como
    snapshot permanente, marca el ítem OK, completa marca/modelo si
    corresponde (solo RGM) y avisa por SSE/Telegram.
    """
    item = db.obtener_item_rgm(item_id)
    if item is None:
        raise db.RegistroNoEncontradoError(f"Ítem de cola RGM id={item_id} no existe")

    db.crear_snapshot(item["placa"], item["tipo"], json.dumps(resultado or {}, ensure_ascii=False))
    db.marcar_item_rgm_completado(item_id)

    diferencias = {}
    if item["tipo"] == "RGM":
        diferencias = _completar_datos_vehiculo_desde_rgm(item["placa"], (resultado or {}).get("vehiculo") or {})

    item_final = db.obtener_item_rgm(item_id)
    _emitir("rgm.resultado", {
        "item_id": item_id, "placa": item["placa"], "tipo": item["tipo"], "origen": item["origen"],
        "avistamiento_id": item["avistamiento_id"], "solicitado_por": item["solicitado_por"],
        "resultado": resultado, "diferencias": diferencias,
    })
    return {"item": item_final, "diferencias": diferencias}


def marcar_error(item_id: int, mensaje: str):
    db.marcar_item_rgm_error(item_id, mensaje)
    item = db.obtener_item_rgm(item_id)
    if item and item["estado"] == "ERROR":
        _emitir("rgm.error", {"item_id": item_id, "placa": item["placa"], "tipo": item["tipo"], "error": mensaje})
