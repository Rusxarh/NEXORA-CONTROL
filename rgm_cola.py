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
- Prompt 2b/5 (NEXCAM-PROMPTS-Y-PRUEBAS.md): el extractor real de RGM
  (content.js) entrega marca, modelo ("Año Correspondiente al Modelo" --
  el campo se llama "modelo", nunca "anio_modelo"), tipo_servicio y
  numero_serial como campos directos de `vehiculo`, más una
  descripcion_bien de texto corrido con rótulos "Linea:"/"Chasis:"/
  "Vin:"/"Motor:"/"Serie:" (mismo formato que ya usa content.js para
  extraer "T. Servicio:"). El RGM MANDA sobre la lista: ahora
  _actualizar_vehiculo_desde_rgm SOBRESCRIBE estas columnas (antes solo
  completaba si estaban vacías); el color nunca llega del RGM, así que
  nunca se toca.
- Prompt 7 bloque A: un ítem puede tener VARIOS solicitantes (cámara lo
  encoló, alguien lo pidió después por Telegram) -- quién lo pidió por
  Telegram vive en la tabla rgm_avisos (database.py), nunca en memoria,
  para sobrevivir a un reinicio de NEXORA entre el pedido y el resultado.
  El bug real que esto corrige: antes, _on_rgm_resultado (nexcam_telegram.py)
  decidía el silencio automático mirando SOLO el `origen` con el que el
  ítem se creó -- un ítem de cámara seguía "siendo de cámara" para
  siempre, aunque alguien lo pidiera después por Telegram, y nunca le
  avisaba. Ahora se decide con los avisos reales (quién espera), no con
  el origen de creación. Pedir sobre un ítem YA encolado también sube su
  prioridad (`prioridad` en rgm_cola, antes un FIFO puro por `id`) a la
  de quien pide, para no esperar detrás de algo de menor prioridad.
"""

import json
import re
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


def _registrar_aviso_si_corresponde(item_id: int, origen: str, usuario_id: int, chat_id: Optional[int],
                                     alerta_chat_id: Optional[int], alerta_message_id: Optional[int]):
    """Prompt 7 bloque A, punto 1: guarda EN LA BASE quién pidió esta
    consulta (reemplaza el diccionario en memoria _chat_por_item_rgm de
    nexcam_telegram.py -- sobrevive a un reinicio de NEXORA) y sube la
    prioridad del ítem a la de quien pide, si era más baja (un ítem de
    cámara/estudio que alguien pide por Telegram ya no debe esperar
    detrás del resto). Solo aplica a origen="telegram" (el único con un
    chat_id real detrás); "panel" sigue avisando a todos los Admin al
    terminar, sin necesitar esto."""
    if chat_id is None:
        return None
    aviso_id = db.crear_aviso_rgm(item_id, chat_id, usuario_id, alerta_chat_id, alerta_message_id)
    db.subir_prioridad_rgm(item_id, db.PRIORIDAD_POR_ORIGEN.get(origen, db.PRIORIDAD_TELEGRAM))
    return aviso_id


def solicitar_consulta(placa: str, alcance: str, origen: str, usuario_id: int, avistamiento_id: int = None,
                        _encadenado: bool = False, chat_id: Optional[int] = None,
                        alerta_chat_id: Optional[int] = None, alerta_message_id: Optional[int] = None) -> dict:
    """
    Punto de entrada único para pedir una consulta RGM/Judicial real.
    Devuelve {"accion": "SNAPSHOT_VIGENTE" | "YA_EN_COLA" | "ENCOLADA", ...};
    en YA_EN_COLA/ENCOLADA, además "puesto" (posición en la cola, 1 = el
    próximo; 0 si ya está EN_CURSO).

    `chat_id` (solo origen="telegram"): quién pidió esta consulta puntual
    -- se guarda en rgm_avisos (ver _registrar_aviso_si_corresponde) para
    poder avisarle al terminar aunque el ítem ya estuviera encolado por
    otra razón (cámara, otro chat) o NEXORA se reinicie de por medio.
    `alerta_chat_id`/`alerta_message_id`: si el botón se pulsó sobre una
    alerta EN VIVO, identifican qué mensaje editar al terminar (Prompt 6
    punto 1); None si se pidió desde una ficha/buscar/web.

    alcance="JUDICIAL": usa el nombre de un snapshot RGM de menos de 24 h
    si existe; si no, encola "RGM" con encadenar_judicial=True (al
    completarse esa RGM, esta misma función se vuelve a llamar sola con
    "JUDICIAL" -- ver completar_item, que copia los avisos del ítem RGM
    al Judicial encadenado) y devuelve esa consulta RGM con un "mensaje"
    explicando que primero consulta RGM.
    """
    placa = placa.strip().upper()

    if alcance == "JUDICIAL":
        snapshot_rgm = db.obtener_ultimo_snapshot_por_fuente(placa, "RGM")
        nombre = _nombre_de_snapshot_rgm(snapshot_rgm) if snapshot_rgm and _snapshot_vigente(snapshot_rgm) else None
        if not nombre:
            resultado = solicitar_consulta(placa, "RGM", origen, usuario_id, avistamiento_id, _encadenado=True,
                                            chat_id=chat_id, alerta_chat_id=alerta_chat_id, alerta_message_id=alerta_message_id)
            resultado["mensaje"] = "Primero consulto RGM para obtener el nombre."
            return resultado

        existente = db.item_rgm_pendiente_o_en_curso(placa, "JUDICIAL")
        if existente:
            aviso_id = _registrar_aviso_si_corresponde(existente["id"], origen, usuario_id, chat_id, alerta_chat_id, alerta_message_id)
            db.registrar_diagnostico_rgm("pedido (ya en cola)", existente["id"], placa, f"alcance=JUDICIAL origen={origen}")
            return {"accion": "YA_EN_COLA", "item": existente, "puesto": db.posicion_en_cola_rgm(existente["id"]), "aviso_id": aviso_id}
        item_id = db.crear_item_rgm_cola(placa, "JUDICIAL", origen, usuario_id, avistamiento_id, nombre_judicial=nombre)
        aviso_id = _registrar_aviso_si_corresponde(item_id, origen, usuario_id, chat_id, alerta_chat_id, alerta_message_id)
        item = db.obtener_item_rgm(item_id)
        db.registrar_diagnostico_rgm("pedido", item_id, placa, f"alcance=JUDICIAL origen={origen}")
        _emitir("rgm.solicitud", {"id": item_id, "placa": placa, "alcance": "JUDICIAL"})
        return {"accion": "ENCOLADA", "item": item, "puesto": db.posicion_en_cola_rgm(item_id), "aviso_id": aviso_id}

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
        aviso_id = _registrar_aviso_si_corresponde(existente["id"], origen, usuario_id, chat_id, alerta_chat_id, alerta_message_id)
        db.registrar_diagnostico_rgm("pedido (ya en cola)", existente["id"], placa, f"alcance={alcance} origen={origen}")
        return {"accion": "YA_EN_COLA", "item": existente, "puesto": db.posicion_en_cola_rgm(existente["id"]), "aviso_id": aviso_id}

    item_id = db.crear_item_rgm_cola(placa, alcance, origen, usuario_id, avistamiento_id, encadenar_judicial=_encadenado)
    aviso_id = _registrar_aviso_si_corresponde(item_id, origen, usuario_id, chat_id, alerta_chat_id, alerta_message_id)
    item = db.obtener_item_rgm(item_id)
    db.registrar_diagnostico_rgm("pedido", item_id, placa, f"alcance={alcance} origen={origen}")
    _emitir("rgm.solicitud", {"id": item_id, "placa": placa, "alcance": alcance})
    return {"accion": "ENCOLADA", "item": item, "puesto": db.posicion_en_cola_rgm(item_id), "aviso_id": aviso_id}


def reservar_siguiente() -> Optional[dict]:
    """Llamado SOLO por la página 'Consulta' mientras atiende la cola."""
    item = db.reservar_siguiente_item_rgm()
    if item:
        db.registrar_diagnostico_rgm("reservado por Consulta", item["id"], item["placa"],
                                      f"intento {item['intentos']}")
    return item


def _extraer_campo_descripcion_bien(descripcion: Optional[str], etiqueta: str) -> Optional[str]:
    """
    `vehiculo.descripcion_bien` del RGM trae varios rótulos CORRIDOS sin
    separador propio (mismo formato que ya usa content.js para extraer
    "T. Servicio:", p.ej. "...Linea: LOGAN Chasis: ABC123 Motor: ..."):
    se busca "<etiqueta>:" y se toma el texto hasta el siguiente rótulo
    con mayúscula inicial, o el final de la cadena.
    """
    if not descripcion:
        return None
    patron = re.compile(rf"{etiqueta}\s*:\s*(.*?)(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ.]*:|$)", re.IGNORECASE)
    m = patron.search(descripcion)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def _datos_vehiculo_desde_rgm(vehiculo_rgm: dict) -> dict:
    """
    Arma {columna: valor} con TODO lo que el RGM trae para `vehiculos`
    (Prompt 2b/5): marca/modelo/tipo_servicio vienen del bloque
    `vehiculo` directo (content.js: modelo = "Año Correspondiente al
    Modelo" -- OJO, el campo real se llama "modelo", nunca
    "anio_modelo"); línea/chasis/motor se extraen de descripcion_bien,
    el único lugar donde el RGM los trae. numero_serie usa primero el
    campo directo `numero_serial`; si vino vacío, cae a "Vin:"/"Serie:"
    de descripcion_bien (el RGM no siempre usa el mismo rótulo). El
    color NUNCA sale de aquí: el RGM no lo trae (ni como campo directo
    ni en descripcion_bien).
    """
    if not vehiculo_rgm:
        return {}
    descripcion = vehiculo_rgm.get("descripcion_bien")
    numero_serie = (
        vehiculo_rgm.get("numero_serial")
        or _extraer_campo_descripcion_bien(descripcion, "Vin")
        or _extraer_campo_descripcion_bien(descripcion, "Serie")
    )
    datos = {
        "marca": vehiculo_rgm.get("marca"),
        "modelo": vehiculo_rgm.get("modelo"),
        "tipo_servicio": vehiculo_rgm.get("tipo_servicio"),
        "numero_serie": numero_serie,
        "linea": _extraer_campo_descripcion_bien(descripcion, "Linea"),
        "chasis": _extraer_campo_descripcion_bien(descripcion, "Chasis"),
        "motor": _extraer_campo_descripcion_bien(descripcion, "Motor"),
    }
    return {col: val.strip() for col, val in datos.items() if isinstance(val, str) and val.strip()}


def _actualizar_vehiculo_desde_rgm(placa: str, vehiculo_rgm: dict, usuario_id: int) -> dict:
    """
    Prompt 2b: el RGM MANDA sobre la lista -- a diferencia de la regla
    anterior (solo completar vacíos), esto SOBRESCRIBE marca/línea/
    modelo/tipo_servicio/numero_serie/chasis/motor con lo que traiga el
    RGM. Si el RGM no trae un campo, se conserva el que había. El color
    nunca se toca (el RGM no lo trae). Cada columna que de verdad cambió
    queda en logs_auditoria (placa_afectada=placa, para que el ?since=
    de /api/plates la recoja -- ver nexcam_db.placas_cambiadas_desde) y
    se devuelve en `cambios` para que la ficha/Telegram puedan mostrar
    "antes -> ahora". rgm_actualizado_en se actualiza siempre que hubo
    al menos un cambio, para "según RGM del <fecha>" en la ficha.
    """
    vehiculo = db.buscar_placa(placa)
    datos_rgm = _datos_vehiculo_desde_rgm(vehiculo_rgm)
    if vehiculo is None or not datos_rgm:
        return {}

    cambios = {}
    for columna, valor_nuevo in datos_rgm.items():
        valor_anterior = (vehiculo.get(columna) or "").strip()
        if valor_anterior != valor_nuevo:
            cambios[columna] = {"antes": valor_anterior or None, "despues": valor_nuevo}

    if not cambios:
        return {}

    with db.get_connection() as conn:
        asignaciones = ", ".join(f"{columna} = ?" for columna in cambios)
        conn.execute(
            f"UPDATE vehiculos SET {asignaciones}, rgm_actualizado_en = datetime('now', 'localtime') WHERE placa = ?",
            (*[c["despues"] for c in cambios.values()], placa),
        )

    resumen = ", ".join(f"{col} {c['antes'] or '(vacío)'} → {c['despues']}" for col, c in cambios.items())
    db.registrar_log(usuario_id, "RGM_ACTUALIZO_VEHICULO", placa_afectada=placa,
                      observaciones=f"Actualizado con RGM: {resumen}.")
    return cambios


def completar_item(item_id: int, resultado: dict) -> dict:
    """
    `resultado` es SIEMPRE {"rgm": ...|null, "judicial": ...|null} (ver
    docstring del módulo). Guarda cada pieza presente como su propio
    snapshot, marca el ítem OK, completa marca/modelo si corresponde
    (solo si vino `rgm`), encadena Judicial si esta RGM lo tenía pendiente,
    y avisa por SSE/Telegram.

    Prompt 7 bloque A, punto 3: el payload de "rgm.resultado" incluye
    "avisos" (quién lo pidió, de rgm_avisos) -- nexcam_telegram.py decide
    con eso a quién avisar, YA NO con `origen` (el bug real: un ítem
    encolado por la cámara sigue teniendo origen="camara" aunque alguien
    lo haya pedido después por Telegram, y _on_rgm_resultado se quedaba
    callado por eso). Si el Judicial queda encadenado, los avisos del
    ítem RGM se copian al nuevo ítem Judicial: quien pidió "Judicial" no
    debe perder su aviso solo porque primero hizo falta una RGM.
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
        diferencias = _actualizar_vehiculo_desde_rgm(item["placa"], rgm_resultado.get("vehiculo") or {}, item["solicitado_por"])
    if judicial_resultado is not None:
        db.crear_snapshot(item["placa"], "JUDICIAL", json.dumps(judicial_resultado, ensure_ascii=False))

    db.marcar_item_rgm_completado(item_id)
    db.registrar_diagnostico_rgm("resultado recibido", item_id, item["placa"],
                                  f"rgm={'si' if rgm_resultado is not None else 'no'} judicial={'si' if judicial_resultado is not None else 'no'}")

    encadenado = None
    if item["alcance"] == "RGM" and item["encadenar_judicial"] and rgm_resultado is not None:
        nombre = ((rgm_resultado.get("deudor_garante") or {}).get("nombre") or "").strip()
        if nombre:
            encadenado = solicitar_consulta(item["placa"], "JUDICIAL", item["origen"], item["solicitado_por"], item["avistamiento_id"])
            if encadenado and encadenado.get("item"):
                db.copiar_avisos_rgm(item_id, encadenado["item"]["id"])

    avisos = db.listar_avisos_rgm(item_id)
    item_final = db.obtener_item_rgm(item_id)
    _emitir("rgm.resultado", {
        "item_id": item_id, "placa": item["placa"], "alcance": item["alcance"], "origen": item["origen"],
        "avistamiento_id": item["avistamiento_id"], "solicitado_por": item["solicitado_por"],
        "resultado": resultado, "diferencias": diferencias, "avisos": avisos,
    })
    return {"item": item_final, "diferencias": diferencias, "encadenado": encadenado}


def marcar_error(item_id: int, mensaje: str):
    db.marcar_item_rgm_error(item_id, mensaje)
    item = db.obtener_item_rgm(item_id)
    db.registrar_diagnostico_rgm("error", item_id, item["placa"] if item else None, mensaje)
    if item and item["estado"] == "ERROR":
        avisos = db.listar_avisos_rgm(item_id)
        _emitir("rgm.error", {"item_id": item_id, "placa": item["placa"], "alcance": item["alcance"],
                               "origen": item["origen"], "error": mensaje, "avisos": avisos})
