"""
tests/test_rgm_cola.py
Pruebas de la FASE 6: cola RGM/Judicial (rgm_cola.py + database.py), el
encolado automático en nexcam_api.py y las rutas de rgm_web.py. Nunca
llama de verdad a NEXORA-CONNECTOR ni a un portal externo (regla del
CLAUDE.md): todo se prueba contra la base de datos y las rutas HTTP
propias de NEXORA-CONTROL.
"""

import json
from datetime import datetime, timedelta

import database as db
import nexcam_db
import rgm_cola
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _crear_placa(placa, usuario_id, **extra):
    db.insertar_placa(placa, "carro", usuario_id, None)
    if extra:
        with db.get_connection() as conn:
            asignaciones = ", ".join(f"{k} = ?" for k in extra)
            conn.execute(f"UPDATE vehiculos SET {asignaciones} WHERE placa = ?", (*extra.values(), placa))
    return db.buscar_placa(placa)


# --------------------------------------------------------------------- encolar sin duplicados

def test_solicitar_consulta_no_duplica(cliente):
    admin = _crear_admin("Admin RGM Uno", "admin.rgm1")
    _crear_placa("ABC123", admin["id"])

    r1 = rgm_cola.solicitar_consulta("ABC123", "RGM", "panel", admin["id"])
    assert r1["accion"] == "ENCOLADA"

    r2 = rgm_cola.solicitar_consulta("ABC123", "RGM", "panel", admin["id"])
    assert r2["accion"] == "YA_EN_COLA"
    assert r2["item"]["id"] == r1["item"]["id"]

    # "JUDICIAL" sin snapshot RGM reciente encadena: como ya hay un ítem
    # RGM pendiente para esa placa, reutiliza ESE mismo (no crea otro).
    r3 = rgm_cola.solicitar_consulta("ABC123", "JUDICIAL", "panel", admin["id"])
    assert r3["accion"] == "YA_EN_COLA"
    assert r3["item"]["id"] == r1["item"]["id"]
    assert r3["item"]["alcance"] == "RGM"


def test_solicitar_consulta_usa_snapshot_vigente(cliente):
    admin = _crear_admin("Admin RGM Dos", "admin.rgm2")
    _crear_placa("DEF456", admin["id"])
    db.crear_snapshot("DEF456", "RGM", json.dumps({"estado": "OK"}))

    resultado = rgm_cola.solicitar_consulta("DEF456", "RGM", "panel", admin["id"])
    assert resultado["accion"] == "SNAPSHOT_VIGENTE"
    assert db.item_rgm_pendiente_o_en_curso("DEF456", "RGM") is None  # no se creó ningún ítem


def test_snapshot_vencido_no_evita_encolar(cliente):
    admin = _crear_admin("Admin RGM Tres", "admin.rgm3")
    _crear_placa("GHI789", admin["id"])
    db.crear_snapshot("GHI789", "RGM", json.dumps({"estado": "OK"}))
    # Lo envejece a 25 h (vencido, el límite es 24 h).
    vieja = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute("UPDATE snapshots_placa SET creado_en = ? WHERE placa = ?", (vieja, "GHI789"))

    resultado = rgm_cola.solicitar_consulta("GHI789", "RGM", "panel", admin["id"])
    assert resultado["accion"] == "ENCOLADA"


# --------------------------------------------------------------------- reserva de a una

def test_reservar_siguiente_toma_una_a_la_vez(cliente):
    admin = _crear_admin("Admin RGM Cuatro", "admin.rgm4")
    _crear_placa("JKL012", admin["id"])
    _crear_placa("MNO345", admin["id"])
    rgm_cola.solicitar_consulta("JKL012", "RGM", "panel", admin["id"])
    rgm_cola.solicitar_consulta("MNO345", "RGM", "panel", admin["id"])

    primero = rgm_cola.reservar_siguiente()
    assert primero["placa"] == "JKL012"
    assert primero["estado"] == "EN_CURSO"

    segundo = rgm_cola.reservar_siguiente()
    assert segundo["placa"] == "MNO345"

    assert rgm_cola.reservar_siguiente() is None  # cola vacía


# --------------------------------------------------------------------- reintentos

def test_marcar_error_reintenta_hasta_el_maximo(cliente):
    admin = _crear_admin("Admin RGM Cinco", "admin.rgm5")
    _crear_placa("PQR678", admin["id"])
    item = rgm_cola.solicitar_consulta("PQR678", "RGM", "panel", admin["id"])["item"]

    rgm_cola.reservar_siguiente()  # intentos=1
    rgm_cola.marcar_error(item["id"], "timeout 1")
    assert db.obtener_item_rgm(item["id"])["estado"] == "PENDIENTE"

    rgm_cola.reservar_siguiente()  # intentos=2 (== MAX_INTENTOS_RGM)
    rgm_cola.marcar_error(item["id"], "timeout 2")
    final = db.obtener_item_rgm(item["id"])
    assert final["estado"] == "ERROR"
    assert final["error"] == "timeout 2"


# --------------------------------------------------------------------- completar + diferencias

def test_completar_item_rgm_completa_marca_y_modelo_vacios(cliente):
    admin = _crear_admin("Admin RGM Seis", "admin.rgm6")
    _crear_placa("STU901", admin["id"])
    item = rgm_cola.solicitar_consulta("STU901", "RGM", "panel", admin["id"])["item"]

    # El resultado que manda la página Consulta es SIEMPRE {"rgm":..., "judicial":...}
    # (igual forma que consulta.rgm/consulta.judicial del Connector).
    # "modelo" es el nombre REAL del campo del RGM (content.js: "Año
    # Correspondiente al Modelo"); nunca "anio_modelo".
    resultado = {"rgm": {"estado": "OK", "deudor_garante": {"nombre": "Juan Pérez"},
                          "vehiculo": {"marca": "Mazda", "modelo": "2019"}}, "judicial": None}
    salida = rgm_cola.completar_item(item["id"], resultado)

    assert salida["diferencias"] == {
        "marca": {"antes": None, "despues": "Mazda"},
        "modelo": {"antes": None, "despues": "2019"},
    }
    vehiculo = db.buscar_placa("STU901")
    assert vehiculo["marca"] == "Mazda"
    assert vehiculo["modelo"] == "2019"
    assert vehiculo["rgm_actualizado_en"] is not None
    assert db.obtener_item_rgm(item["id"])["estado"] == "OK"
    snapshot = db.obtener_ultimo_snapshot_por_fuente("STU901", "RGM")
    assert json.loads(snapshot["datos"])["deudor_garante"]["nombre"] == "Juan Pérez"


def test_completar_item_rgm_sobrescribe_y_queda_en_auditoria(cliente):
    """Prompt 2b: el RGM manda sobre la lista -- ya NO se conserva el
    valor anterior; queda en logs_auditoria (placa_afectada) para que
    tanto la ficha como el ?since= de /api/plates lo vean."""
    admin = _crear_admin("Admin RGM Siete", "admin.rgm7")
    _crear_placa("VWX234", admin["id"], marca="Chevrolet")
    item = rgm_cola.solicitar_consulta("VWX234", "RGM", "panel", admin["id"])["item"]

    resultado = {"rgm": {"estado": "OK", "vehiculo": {"marca": "Renault"}}, "judicial": None}
    salida = rgm_cola.completar_item(item["id"], resultado)

    assert db.buscar_placa("VWX234")["marca"] == "Renault"  # el RGM manda
    assert salida["diferencias"] == {"marca": {"antes": "Chevrolet", "despues": "Renault"}}
    logs = db.listar_logs_de_placa("VWX234")
    assert any(
        l["accion"] == "RGM_ACTUALIZO_VEHICULO" and "Chevrolet" in (l["observaciones"] or "") and "Renault" in (l["observaciones"] or "")
        for l in logs
    )


def test_completar_item_rgm_extrae_linea_chasis_motor_y_serie_de_descripcion_bien(cliente):
    """Prompt 2b/5: línea/chasis/motor/serie no son campos directos del
    RGM -- vienen todos corridos sin separador dentro de
    vehiculo.descripcion_bien, igual formato que ya usa content.js para
    "T. Servicio:"."""
    admin = _crear_admin("Admin RGM Nueve", "admin.rgm9")
    _crear_placa("BCD123", admin["id"])
    item = rgm_cola.solicitar_consulta("BCD123", "RGM", "panel", admin["id"])["item"]

    descripcion = ("T. Servicio: Particular Linea: LOGAN Chasis: 9BW1234567 "
                   "Motor: K4M710 Serie: VF1LSRAC512345678")
    resultado = {"rgm": {"estado": "OK", "vehiculo": {
        "marca": "RENAULT", "modelo": "2020", "tipo_servicio": "Particular",
        "descripcion_bien": descripcion,
    }}, "judicial": None}
    rgm_cola.completar_item(item["id"], resultado)

    vehiculo = db.buscar_placa("BCD123")
    assert vehiculo["linea"] == "LOGAN"
    assert vehiculo["chasis"] == "9BW1234567"
    assert vehiculo["motor"] == "K4M710"
    assert vehiculo["numero_serie"] == "VF1LSRAC512345678"
    assert vehiculo["tipo_servicio"] == "Particular"


def test_actualizacion_rgm_llega_a_la_app_por_api_plates(cliente):
    """Prompt 2b, punto 2: /api/plates (incremental) debe incluir
    tipo_servicio y marcar como cambiada una placa que el RGM acaba de
    actualizar, aunque su fecha_ingreso/fecha_baja no se haya tocado --
    placas_cambiadas_desde ya detecta esto vía logs_auditoria, que
    _actualizar_vehiculo_desde_rgm alimenta con placa_afectada=placa."""
    admin = _crear_admin("Admin RGM Diez", "admin.rgm10")
    usuario_admin = db.obtener_usuario_por_id(admin["id"])
    _crear_placa("EFG456", admin["id"])
    desde_ms = int(datetime.now().timestamp() * 1000)

    item = rgm_cola.solicitar_consulta("EFG456", "RGM", "panel", admin["id"])["item"]
    resultado = {"rgm": {"estado": "OK", "vehiculo": {
        "marca": "RENAULT", "modelo": "2020", "tipo_servicio": "Particular",
        "descripcion_bien": "Linea: LOGAN",
    }}, "judicial": None}
    rgm_cola.completar_item(item["id"], resultado)

    completa = {p["placa"]: p for p in nexcam_db.placas_activas_para_app(usuario_admin)}
    assert completa["EFG456"]["tipo_servicio"] == "Particular"
    assert completa["EFG456"]["linea"] == "LOGAN"

    cambiadas = {p["placa"] for p in nexcam_db.placas_cambiadas_desde(desde_ms - 1000, usuario_admin)}
    assert "EFG456" in cambiadas


def test_completar_item_judicial_no_toca_vehiculo(cliente):
    admin = _crear_admin("Admin RGM Ocho", "admin.rgm8")
    _crear_placa("YZA567", admin["id"])
    # Se crea directo por database.py (no por solicitar_consulta) para
    # probar completar_item de forma aislada, sin que la cadena
    # "Judicial sin snapshot RGM" interfiera con este caso.
    item_id = db.crear_item_rgm_cola("YZA567", "JUDICIAL", "panel", admin["id"], nombre_judicial="Juan Pérez")

    resultado = {"rgm": None, "judicial": {"procesos": [{"radicado": "12345678901234567890"}]}}
    salida = rgm_cola.completar_item(item_id, resultado)
    assert salida["diferencias"] == {}
    assert db.buscar_placa("YZA567")["marca"] is None  # nunca se tocó por un resultado judicial
    snapshot = db.obtener_ultimo_snapshot_por_fuente("YZA567", "JUDICIAL")
    assert json.loads(snapshot["datos"])["procesos"][0]["radicado"] == "12345678901234567890"


def test_judicial_sin_snapshot_rgm_encadena_y_luego_resuelve(cliente):
    """Pedido 'Judicial sola' sin snapshot RGM reciente: encola RGM primero
    (encadenar_judicial=True); al completarse esa RGM con un nombre
    utilizable, se encola sola una 'JUDICIAL' con ese nombre."""
    admin = _crear_admin("Admin RGM Nueve", "admin.rgm9")
    _crear_placa("BCD123", admin["id"])

    r = rgm_cola.solicitar_consulta("BCD123", "JUDICIAL", "panel", admin["id"])
    assert r["accion"] == "ENCOLADA"
    assert r["item"]["alcance"] == "RGM"
    assert r["item"]["encadenar_judicial"] == 1
    assert "Primero consulto RGM" in r["mensaje"]

    salida = rgm_cola.completar_item(r["item"]["id"], {"rgm": {"deudor_garante": {"nombre": "María Gómez"}}, "judicial": None})
    assert salida["encadenado"] is not None
    assert salida["encadenado"]["accion"] == "ENCOLADA"
    assert salida["encadenado"]["item"]["alcance"] == "JUDICIAL"
    assert salida["encadenado"]["item"]["nombre_judicial"] == "María Gómez"


# --------------------------------------------------------------------- encolado automático (nexcam_api)

def test_deteccion_exacta_en_lista_encola_rgm_automatico(cliente):
    admin = _crear_admin("Admin RGM Nueve", "admin.rgm9")
    _crear_placa("BCD890", admin["id"])
    device = "device-rgm-auto"
    cliente.post("/api/device/register", json={"device_uuid": device})
    import nexcam_db
    nexcam_db.autorizar_dispositivo(device)
    token = cliente.post("/api/auth/login", json={"username": "admin.rgm9", "password": CLAVE, "device_uuid": device}).json()["token"]

    cliente.post("/api/detections/upload", headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
                 json={"placa": "BCD890", "matchType": "exact"})

    assert db.item_rgm_pendiente_o_en_curso("BCD890", "RGM") is not None


def test_deteccion_probable_no_encola_nada(cliente):
    admin = _crear_admin("Admin RGM Diez", "admin.rgm10")
    _crear_placa("EFG123", admin["id"])
    device = "device-rgm-probable"
    cliente.post("/api/device/register", json={"device_uuid": device})
    import nexcam_db
    nexcam_db.autorizar_dispositivo(device)
    token = cliente.post("/api/auth/login", json={"username": "admin.rgm10", "password": CLAVE, "device_uuid": device}).json()["token"]

    cliente.post("/api/detections/upload", headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
                 json={"placa": "EFG123", "leida": "EFG123", "matchType": "probable"})

    assert db.item_rgm_pendiente_o_en_curso("EFG123", "RGM") is None


def test_deteccion_fuera_de_lista_no_encola_nada(cliente):
    admin = _crear_admin("Admin RGM Once", "admin.rgm11")
    device = "device-rgm-fuera"
    cliente.post("/api/device/register", json={"device_uuid": device})
    import nexcam_db
    nexcam_db.autorizar_dispositivo(device)
    token = cliente.post("/api/auth/login", json={"username": "admin.rgm11", "password": CLAVE, "device_uuid": device}).json()["token"]

    cliente.post("/api/detections/upload", headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
                 json={"placa": "HIJ456"})  # no está en vehiculos

    assert db.item_rgm_pendiente_o_en_curso("HIJ456", "RGM") is None


# --------------------------------------------------------------------- rutas /rgm/trabajador

def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def test_trabajador_rgm_reserva_completa_y_registra_error(cliente):
    admin = _crear_admin("Admin RGM Panel", "admin.rgmpanel")
    _crear_placa("KLM789", admin["id"])
    _login_web(cliente, "admin.rgmpanel")

    cliente.post("/rgm/solicitar", data={"placa": "KLM789", "alcance": "RGM"})
    assert db.item_rgm_pendiente_o_en_curso("KLM789", "RGM") is not None

    r_latido = cliente.post("/rgm/trabajador/latido")
    assert r_latido.json() == {"ok": True}
    assert rgm_cola.trabajador_conectado() is True

    r_sig = cliente.post("/rgm/trabajador/siguiente")
    item = r_sig.json()["item"]
    assert item["placa"] == "KLM789"

    r_res = cliente.post("/rgm/trabajador/resultado", json={
        "item_id": item["id"],
        "resultado": {"rgm": {"estado": "OK", "deudor_garante": {"nombre": "Pedro Ramírez"}}, "judicial": None},
    })
    assert r_res.json()["ok"] is True
    assert db.obtener_item_rgm(item["id"])["estado"] == "OK"

    # Un segundo ítem: ahora SÍ hay snapshot RGM con nombre -> "JUDICIAL" se
    # encola directo (sin encadenar otra RGM), para probar la ruta de error.
    resultado_judicial = rgm_cola.solicitar_consulta("KLM789", "JUDICIAL", "panel", admin["id"])
    assert resultado_judicial["accion"] == "ENCOLADA"
    assert resultado_judicial["item"]["alcance"] == "JUDICIAL"
    item2 = cliente.post("/rgm/trabajador/siguiente").json()["item"]
    assert item2["alcance"] == "JUDICIAL"
    r_err = cliente.post("/rgm/trabajador/error", json={"item_id": item2["id"], "mensaje": "timeout de prueba"})
    assert r_err.json() == {"ok": True}


def test_error_del_connector_muestra_su_motivo_en_ultimas_20(cliente):
    """Prueba real del Connector: el timeout de 2 min de una consulta
    externa (o cualquier consulta.errores que mande el Connector) debe
    verse en /consulta -> "Últimas 20", no solo quedar en la base. El
    motivo ya viaja completo desde consulta.html (terminarConError junta
    evento.motivo + consulta.errores) hasta rgm_cola.marcar_error ->
    columna `error`; esta prueba confirma que /consulta de verdad lo
    renderiza (como title del badge "Error", consistente con el resto del
    patrón de la tabla)."""
    admin = _crear_admin("Admin RGM Timeout", "admin.rgmtimeout")
    _crear_placa("TMO456", admin["id"])
    _login_web(cliente, "admin.rgmtimeout")

    cliente.post("/rgm/solicitar", data={"placa": "TMO456", "alcance": "RGM"})
    motivo = "TIMEOUT_RGM_2MIN (se quedo colgada sin resultado final, revisa el NEXORA DEBUG de la pestana)"

    # marcar_item_rgm_error reintenta (vuelve a PENDIENTE) hasta
    # MAX_INTENTOS_RGM: hay que agotarlos para que de verdad quede en
    # ERROR (final) con el motivo, en vez de solo reintentar en silencio.
    item_id = None
    for _ in range(db.MAX_INTENTOS_RGM):
        item = cliente.post("/rgm/trabajador/siguiente").json()["item"]
        item_id = item["id"]
        r_err = cliente.post("/rgm/trabajador/error", json={"item_id": item_id, "mensaje": motivo})
        assert r_err.json() == {"ok": True}

    assert db.obtener_item_rgm(item_id)["estado"] == "ERROR"
    assert db.obtener_item_rgm(item_id)["error"] == motivo

    r_consulta = cliente.get("/consulta")
    assert r_consulta.status_code == 200
    assert motivo in r_consulta.text


def test_rgm_solicitar_usa_snapshot_vigente_sin_encolar_de_nuevo(cliente):
    admin = _crear_admin("Admin RGM Panel2", "admin.rgmpanel2")
    _crear_placa("NOP012", admin["id"])
    db.crear_snapshot("NOP012", "RGM", json.dumps({"estado": "OK"}))
    _login_web(cliente, "admin.rgmpanel2")

    r = cliente.post("/rgm/solicitar", data={"placa": "NOP012", "alcance": "RGM"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.item_rgm_pendiente_o_en_curso("NOP012", "RGM") is None


# --------------------------------------------------------------------- Prompt 6 punto 2: arreglo de RGM viejos

def test_backfill_rgm_aplica_el_ultimo_snapshot_ok_una_sola_vez(cliente):
    """Un snapshot RGM guardado ANTES del Prompt 2b (insertado directo,
    sin pasar por completar_item) nunca escribió `vehiculos` -- GCT 953
    se quedaba en "CHEVROLET / —" aunque el snapshot ya traía línea
    TRACKER y modelo 2021. _migrar_backfill_rgm_a_vehiculos debe
    aplicarlo retroactivamente, una sola vez (idempotente)."""
    admin = _crear_admin("Admin Backfill Uno", "admin.backfill1")
    _crear_placa("GCT953", admin["id"])
    descripcion = "T. Servicio: Particular Linea: TRACKER Motor: ABC123"
    db.crear_snapshot("GCT953", "RGM", json.dumps({
        "estado": "OK", "vehiculo": {"marca": "CHEVROLET", "modelo": "2021",
                                      "tipo_servicio": "Particular", "descripcion_bien": descripcion},
    }))
    assert db.buscar_placa("GCT953")["marca"] is None  # todavía no se aplicó (simula el bug viejo)

    db._migrar_backfill_rgm_a_vehiculos()

    vehiculo = db.buscar_placa("GCT953")
    assert vehiculo["marca"] == "CHEVROLET"
    assert vehiculo["linea"] == "TRACKER"
    assert vehiculo["modelo"] == "2021"
    assert vehiculo["rgm_actualizado_en"] is not None
    logs = db.listar_logs(usuario_id=None)
    assert sum(1 for l in logs if l["accion"] == "RGM_BACKFILL_PROMPT6") == 1
    assert any(l["accion"] == "RGM_ACTUALIZO_VEHICULO" for l in db.listar_logs_de_placa("GCT953"))

    # Una segunda llamada (ej. el servidor se reinicia otra vez) no debe
    # volver a tocar nada ni duplicar el log centinela.
    with db.get_connection() as conn:
        conn.execute("UPDATE vehiculos SET marca = 'OTRA' WHERE placa = 'GCT953'")
    db._migrar_backfill_rgm_a_vehiculos()
    assert db.buscar_placa("GCT953")["marca"] == "OTRA"  # no se reprocesó
    logs_despues = db.listar_logs(usuario_id=None)
    assert sum(1 for l in logs_despues if l["accion"] == "RGM_BACKFILL_PROMPT6") == 1


def test_backfill_rgm_ignora_snapshots_sin_estado_ok(cliente):
    admin = _crear_admin("Admin Backfill Dos", "admin.backfill2")
    _crear_placa("ERR789", admin["id"])
    db.crear_snapshot("ERR789", "RGM", json.dumps({"estado": "SIN_DATOS", "vehiculo": {"marca": "FORD"}}))

    db._migrar_backfill_rgm_a_vehiculos()

    assert db.buscar_placa("ERR789")["marca"] is None
