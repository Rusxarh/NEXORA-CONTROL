"""
tests/test_rgm_cola.py
Pruebas de la FASE 6: cola RGM/Judicial (rgm_cola.py + database.py), el
encolado automático en nexcam_api.py y las rutas de rgm_web.py. Nunca
llama de verdad a NEXORA-CONNECTOR ni a un portal externo (regla del
CLAUDE.md): todo se prueba contra la base de datos y las rutas HTTP
propias de NEXORA-CONTROL.
"""

import json
import time
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


def test_resultado_de_placa_que_no_esta_en_vehiculos_no_da_500(cliente):
    """Prompt 8b punto 2: NHP973 pedida por /buscar en Telegram (que no
    exige que la placa ya esté cargada) terminaba en 500
    (sqlite3.IntegrityError de la FK de snapshots_placa a vehiculos.placa)
    y el ítem se quedaba EN_CURSO para siempre -- la cola de Consulta lo
    mostraba sin avanzar aunque el 'portal' (simulado aquí) ya había
    respondido. Ahora guarda el snapshot igual y el ítem queda OK."""
    admin = _crear_admin("Admin RGM SinVehiculo", "admin.rgmsinveh")
    _login_web(cliente, "admin.rgmsinveh")

    item = rgm_cola.solicitar_consulta("NHP973", "RGM", "telegram", admin["id"])["item"]
    assert item["placa"] == "NHP973"

    r_sig = cliente.post("/rgm/trabajador/siguiente")
    assert r_sig.json()["item"]["id"] == item["id"]

    r_res = cliente.post("/rgm/trabajador/resultado", json={
        "item_id": item["id"],
        "resultado": {"rgm": {"estado": "OK", "vehiculo": {}}, "judicial": None},
    })
    assert r_res.status_code == 200
    assert r_res.json()["ok"] is True
    assert db.obtener_item_rgm(item["id"])["estado"] == "OK"
    assert db.obtener_ultimo_snapshot_por_fuente("NHP973", "RGM") is not None


def test_resultado_con_fallo_inesperado_marca_error_en_vez_de_500(cliente, monkeypatch):
    """Red de seguridad general del punto 2: cualquier excepción futura
    dentro de completar_item (no solo la FK ya corregida) tampoco debe
    devolver 500. Se agotan los MAX_INTENTOS_RGM (mismo patrón que
    test_error_del_connector_muestra_su_motivo_en_ultimas_20) para que el
    ítem quede en ERROR final en vez de reintentando en silencio."""
    admin = _crear_admin("Admin RGM Fallo", "admin.rgmfallo")
    _crear_placa("FAL999", admin["id"])
    _login_web(cliente, "admin.rgmfallo")

    cliente.post("/rgm/solicitar", data={"placa": "FAL999", "alcance": "RGM"})

    def _reventar(item_id, resultado):
        raise RuntimeError("fallo simulado de completar_item")

    monkeypatch.setattr(rgm_cola, "completar_item", _reventar)

    item_id = None
    for _ in range(db.MAX_INTENTOS_RGM):
        item = cliente.post("/rgm/trabajador/siguiente").json()["item"]
        item_id = item["id"]
        r_res = cliente.post("/rgm/trabajador/resultado", json={"item_id": item_id, "resultado": {"rgm": None, "judicial": None}})
        assert r_res.status_code == 200
        assert r_res.json()["ok"] is False

    assert db.obtener_item_rgm(item_id)["estado"] == "ERROR"
    assert "fallo simulado" in db.obtener_item_rgm(item_id)["error"]


def test_resultado_registra_el_plan_usado_en_diagnostico(cliente):
    """Prompt 8b punto 1: el Connector manda "plan" ("A" sin pestaña o
    "B" con pestaña/ventana, con "plan_motivo" si fue B) junto con el
    resultado; /consulta/diagnostico debe reflejar por qué se usó cada
    uno, sin importar si la propia consulta terminó OK o en error."""
    admin = _crear_admin("Admin RGM Plan", "admin.rgmplan")
    _crear_placa("PLN001", admin["id"])
    _login_web(cliente, "admin.rgmplan")

    cliente.post("/rgm/solicitar", data={"placa": "PLN001", "alcance": "RGM"})
    item = cliente.post("/rgm/trabajador/siguiente").json()["item"]
    motivo = "no existe un plan A (fetch sin pestaña) implementado todavía"
    r = cliente.post("/rgm/trabajador/resultado", json={
        "item_id": item["id"],
        "resultado": {"rgm": {"estado": "OK", "vehiculo": {}}, "judicial": None},
        "plan": "B", "plan_motivo": motivo,
    })
    assert r.json()["ok"] is True

    pasos = db.listar_diagnostico_rgm(50)
    assert any(p["paso"] == f"plan B porque: {motivo}" and p["item_id"] == item["id"] for p in pasos)

    r_diag = cliente.get("/consulta/diagnostico")
    assert f"plan B porque: {motivo}" in r_diag.text


def test_resultado_registra_plan_a_ok(cliente):
    """Prompt 8c punto 2: texto exacto pedido, "plan A OK" (sin el
    paréntesis que tenía la versión del Prompt 8b)."""
    admin = _crear_admin("Admin RGM PlanA", "admin.rgmplana")
    _crear_placa("PLN002", admin["id"])
    _login_web(cliente, "admin.rgmplana")

    cliente.post("/rgm/solicitar", data={"placa": "PLN002", "alcance": "RGM"})
    item = cliente.post("/rgm/trabajador/siguiente").json()["item"]
    r = cliente.post("/rgm/trabajador/resultado", json={
        "item_id": item["id"],
        "resultado": {"rgm": {"estado": "OK", "vehiculo": {}}, "judicial": None},
        "plan": "A",
    })
    assert r.json()["ok"] is True

    pasos = db.listar_diagnostico_rgm(50)
    assert any(p["paso"] == "plan A OK" and p["item_id"] == item["id"] for p in pasos)
    r_diag = cliente.get("/consulta/diagnostico")
    assert "plan A OK" in r_diag.text


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


def test_consulta_diagnostico_muestra_los_pasos(cliente):
    admin = _crear_admin("Admin P7A Diag", "admin.p7adiag")
    _crear_placa("DGW777", admin["id"])
    _login_web(cliente, "admin.p7adiag")

    cliente.post("/rgm/solicitar", data={"placa": "DGW777", "alcance": "RGM"})

    r = cliente.get("/consulta/diagnostico")
    assert r.status_code == 200
    assert "DGW777" in r.text
    assert "pedido" in r.text


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


# --------------------------------------------------------------------- Prompt 7 bloque A: RGM a pedido que no responde

def test_pedido_de_telegram_sobre_item_automatico_sube_su_prioridad(cliente):
    """El bug real: la cámara encola GCT953 (origen "camara", prioridad
    automática); si alguien lo pide por Telegram DESPUÉS, debe pasar a
    atenderse antes que otro ítem automático más viejo, no quedarse
    esperando detrás en el FIFO."""
    admin = _crear_admin("Admin P7A Uno", "admin.p7a1")
    _crear_placa("VIE111", admin["id"])
    _crear_placa("NUE222", admin["id"])

    viejo = rgm_cola.solicitar_consulta("VIE111", "RGM", "camara", admin["id"])["item"]
    nuevo = rgm_cola.solicitar_consulta("NUE222", "RGM", "camara", admin["id"])["item"]
    assert db.obtener_item_rgm(viejo["id"])["prioridad"] == db.PRIORIDAD_AUTOMATICA

    # Alguien pide por Telegram el NUEVO (el segundo, más joven) -- debe
    # saltar delante del viejo en la cola real.
    resultado = rgm_cola.solicitar_consulta("NUE222", "RGM", "telegram", admin["id"], chat_id=999111)
    assert resultado["accion"] == "YA_EN_COLA"
    assert db.obtener_item_rgm(nuevo["id"])["prioridad"] == db.PRIORIDAD_TELEGRAM

    primero_atendido = rgm_cola.reservar_siguiente()
    assert primero_atendido["placa"] == "NUE222"  # el pedido por Telegram, no el más viejo


def test_resultado_automatico_pedido_despues_por_telegram_si_avisa(cliente):
    """EL bug de fondo (Prompt 7 bloque A): un ítem con origen="camara"
    que alguien pidió DESPUÉS por Telegram debe avisarle al terminar --
    antes, _on_rgm_resultado se quedaba callado solo por mirar `origen`
    (ver nexcam_telegram.py), sin fijarse en quién esperaba. Esta prueba
    es sobre rgm_cola/database: confirma que el AVISO queda persistido y
    que completar_item lo incluye en el payload que emite, sin importar
    el origen original del ítem."""
    admin = _crear_admin("Admin P7A Dos", "admin.p7a2")
    _crear_placa("AUT333", admin["id"])

    item = rgm_cola.solicitar_consulta("AUT333", "RGM", "camara", admin["id"])["item"]
    assert db.listar_avisos_rgm(item["id"]) == []  # nadie lo ha pedido todavía

    resultado = rgm_cola.solicitar_consulta("AUT333", "RGM", "telegram", admin["id"], chat_id=777222)
    assert resultado["accion"] == "YA_EN_COLA"
    avisos = db.listar_avisos_rgm(item["id"])
    assert len(avisos) == 1 and avisos[0]["chat_id"] == 777222

    capturado = {}
    import nexcam_api

    nexcam_api.suscribir(lambda evento, payload: capturado.update(payload) if evento == "rgm.resultado" else None)
    salida = rgm_cola.completar_item(item["id"], {"rgm": {"estado": "OK", "vehiculo": {"marca": "Kia"}}, "judicial": None})
    assert salida["diferencias"].get("marca", {}).get("despues") == "Kia"

    limite = time.time() + 3
    while not capturado and time.time() < limite:
        time.sleep(0.02)
    assert capturado.get("origen") == "camara"  # el origen del ítem no cambió...
    assert any(a["chat_id"] == 777222 for a in capturado.get("avisos") or [])  # ...pero el aviso SÍ llegó


def test_posicion_en_cola_y_puesto_al_pedir(cliente):
    admin = _crear_admin("Admin P7A Tres", "admin.p7a3")
    for i in range(3):
        _crear_placa(f"POS{i:03d}", admin["id"])
    items = [rgm_cola.solicitar_consulta(f"POS{i:03d}", "RGM", "panel", admin["id"])["item"] for i in range(3)]

    assert db.posicion_en_cola_rgm(items[0]["id"]) == 1
    assert db.posicion_en_cola_rgm(items[1]["id"]) == 2
    assert db.posicion_en_cola_rgm(items[2]["id"]) == 3

    rgm_cola.reservar_siguiente()  # items[0] pasa a EN_CURSO
    assert db.posicion_en_cola_rgm(items[0]["id"]) == 0
    assert db.posicion_en_cola_rgm(items[1]["id"]) == 1


def test_aviso_sobrevive_a_un_reinicio_de_nexora(cliente):
    """El pedido queda en la base (rgm_avisos), no en memoria: "reiniciar
    NEXORA" se simula aquí simplemente leyendo los avisos desde database.py
    sin pasar por ningún objeto de nexcam_telegram.py."""
    admin = _crear_admin("Admin P7A Cuatro", "admin.p7a4")
    _crear_placa("RST444", admin["id"])

    resultado = rgm_cola.solicitar_consulta("RST444", "RGM", "telegram", admin["id"], chat_id=555666)
    item_id = resultado["item"]["id"]

    # "reinicio": nada en memoria de nexcam_telegram.py sobrevive, pero
    # rgm_avisos sigue ahí con el chat_id real.
    avisos_tras_reinicio = db.listar_avisos_rgm(item_id)
    assert len(avisos_tras_reinicio) == 1
    assert avisos_tras_reinicio[0]["chat_id"] == 555666

    rgm_cola.completar_item(item_id, {"rgm": {"estado": "OK", "vehiculo": {"marca": "Renault"}}, "judicial": None})
    assert db.obtener_item_rgm(item_id)["estado"] == "OK"


def test_dos_pedidos_de_la_misma_placa_una_sola_consulta_y_dos_avisos(cliente):
    admin = _crear_admin("Admin P7A Siete", "admin.p7a7")
    _crear_placa("DUP777", admin["id"])

    r1 = rgm_cola.solicitar_consulta("DUP777", "RGM", "telegram", admin["id"], chat_id=111111)
    r2 = rgm_cola.solicitar_consulta("DUP777", "RGM", "telegram", admin["id"], chat_id=222222)
    assert r1["accion"] == "ENCOLADA"
    assert r2["accion"] == "YA_EN_COLA"
    assert r1["item"]["id"] == r2["item"]["id"]  # una sola consulta real

    avisos = db.listar_avisos_rgm(r1["item"]["id"])
    assert {a["chat_id"] for a in avisos} == {111111, 222222}  # dos avisos distintos

    capturado = {}
    import nexcam_api

    nexcam_api.suscribir(lambda evento, payload: capturado.update(payload) if evento == "rgm.resultado" else None)
    rgm_cola.completar_item(r1["item"]["id"], {"rgm": {"estado": "OK", "vehiculo": {"marca": "Toyota"}}, "judicial": None})

    limite = time.time() + 3
    while not capturado and time.time() < limite:
        time.sleep(0.02)
    assert {a["chat_id"] for a in capturado.get("avisos") or []} == {111111, 222222}


def test_judicial_encadenado_copia_los_avisos_del_item_rgm(cliente):
    admin = _crear_admin("Admin P7A Cinco", "admin.p7a5")
    _crear_placa("JUD555", admin["id"])

    r = rgm_cola.solicitar_consulta("JUD555", "JUDICIAL", "telegram", admin["id"], chat_id=444555)
    assert r["accion"] == "ENCOLADA"
    assert r["item"]["alcance"] == "RGM"  # encadena: primero pide RGM

    salida = rgm_cola.completar_item(r["item"]["id"], {"rgm": {"deudor_garante": {"nombre": "Pedro Gómez"}}, "judicial": None})
    assert salida["encadenado"]["accion"] == "ENCOLADA"
    item_judicial_id = salida["encadenado"]["item"]["id"]

    avisos_judicial = db.listar_avisos_rgm(item_judicial_id)
    assert len(avisos_judicial) == 1 and avisos_judicial[0]["chat_id"] == 444555


def test_diagnostico_registra_cada_paso(cliente):
    admin = _crear_admin("Admin P7A Seis", "admin.p7a6")
    _crear_placa("DIA666", admin["id"])

    item = rgm_cola.solicitar_consulta("DIA666", "RGM", "panel", admin["id"])["item"]
    rgm_cola.reservar_siguiente()
    rgm_cola.completar_item(item["id"], {"rgm": {"estado": "OK"}, "judicial": None})

    pasos = [p["paso"] for p in db.listar_diagnostico_rgm(50) if p["item_id"] == item["id"]]
    assert "pedido" in pasos
    assert "reservado por Consulta" in pasos


# --------------------------------------------------------------------- Prompt 8: Judicial directo (sin Connector)

def test_completo_ya_no_encola_literal_sino_rgm_encadenado(cliente):
    """Prompt 8: "COMPLETO" ya no se manda al Connector como tal -- se
    resuelve como una RGM con encadenar_judicial=True (el Judicial lo
    atiende el hilo de judicial_directo al terminar esa RGM, nunca el
    Connector)."""
    admin = _crear_admin("Admin P8 Uno", "admin.p8uno")
    _crear_placa("CPL111", admin["id"])

    resultado = rgm_cola.solicitar_consulta("CPL111", "COMPLETO", "panel", admin["id"])
    assert resultado["accion"] == "ENCOLADA"
    assert resultado["item"]["alcance"] == "RGM"
    assert resultado["item"]["encadenar_judicial"] == 1


def test_judicial_encadenado_lo_completa_el_hilo_directo_no_el_connector(cliente, monkeypatch):
    """Al terminar la RGM encadenada, el Judicial resultante lo resuelve
    judicial_directo.py (un hilo propio), no un segundo ítem que espere
    a la página Consulta."""
    import judicial_directo

    admin = _crear_admin("Admin P8 Dos", "admin.p8dos")
    _crear_placa("CPL222", admin["id"])

    r = rgm_cola.solicitar_consulta("CPL222", "COMPLETO", "panel", admin["id"])
    item_rgm_id = r["item"]["id"]
    salida = rgm_cola.completar_item(item_rgm_id, {
        "rgm": {"estado": "OK", "deudor_garante": {"nombre": "Banco Ficticio S.A."}}, "judicial": None,
    })
    item_judicial_id = salida["encadenado"]["item"]["id"]
    assert db.obtener_item_rgm(item_judicial_id)["estado"] == "PENDIENTE"

    monkeypatch.setattr(judicial_directo, "consultar_judicial_por_nombre",
                         lambda nombre, tipo_persona="nat": {"nombre_consultado": nombre, "procesos": []})
    reservado = db.reservar_siguiente_item_rgm_judicial()
    assert reservado["id"] == item_judicial_id
    rgm_cola.completar_judicial_directo(item_judicial_id)

    assert db.obtener_item_rgm(item_judicial_id)["estado"] == "OK"
    snapshot = db.obtener_ultimo_snapshot_por_fuente("CPL222", "JUDICIAL")
    assert json.loads(snapshot["datos"])["nombre_consultado"] == "Banco Ficticio S.A."


def test_reservar_judicial_nunca_toca_items_rgm(cliente):
    admin = _crear_admin("Admin P8 Tres", "admin.p8tres")
    _crear_placa("CPL333", admin["id"])
    rgm_cola.solicitar_consulta("CPL333", "RGM", "panel", admin["id"])

    assert db.reservar_siguiente_item_rgm_judicial() is None  # no hay ningún JUDICIAL pendiente
    assert db.item_rgm_pendiente_o_en_curso("CPL333", "RGM")["estado"] == "PENDIENTE"  # el RGM sigue intacto


def test_judicial_directo_error_de_red_queda_en_error(cliente, monkeypatch):
    import judicial_directo

    admin = _crear_admin("Admin P8 Cuatro", "admin.p8cuatro")
    _crear_placa("CPL444", admin["id"])
    item_id = db.crear_item_rgm_cola("CPL444", "JUDICIAL", "panel", admin["id"], nombre_judicial="Juan Pérez")

    def _falla(nombre, tipo_persona="nat"):
        raise judicial_directo.ErrorJudicialDirecto("timeout de prueba")

    monkeypatch.setattr(judicial_directo, "consultar_judicial_por_nombre", _falla)
    rgm_cola.completar_judicial_directo(item_id)

    item = db.obtener_item_rgm(item_id)
    assert item["estado"] == "PENDIENTE"  # primer intento: reintenta (MAX_INTENTOS_RGM = 2)
    assert "timeout de prueba" in item["error"]


def test_ciclo_judicial_directo_arma_y_atiende_un_item(cliente, monkeypatch):
    import judicial_directo

    admin = _crear_admin("Admin P8 Cinco", "admin.p8cinco")
    _crear_placa("CPL555", admin["id"])
    db.crear_item_rgm_cola("CPL555", "JUDICIAL", "panel", admin["id"], nombre_judicial="María Gómez")
    monkeypatch.setattr(judicial_directo, "consultar_judicial_por_nombre",
                         lambda nombre, tipo_persona="nat": {"nombre_consultado": nombre, "procesos": []})

    hubo_trabajo = rgm_cola._ciclo_judicial_directo()
    assert hubo_trabajo is True
    assert rgm_cola._ciclo_judicial_directo() is False  # ya no queda nada pendiente


# --------------------------------------------------------------------- Prompt 8d punto 5: ítems colgados

def _envejecer_reserva(item_id, minutos):
    viejo = (datetime.now() - timedelta(minutes=minutos)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute("UPDATE rgm_cola SET reservado_en = ? WHERE id = ?", (viejo, item_id))


def test_vigilar_colgados_no_toca_items_en_curso_recientes(cliente):
    admin = _crear_admin("Admin P8d Uno", "admin.p8duno")
    _crear_placa("COL111", admin["id"])
    item = rgm_cola.solicitar_consulta("COL111", "RGM", "panel", admin["id"])["item"]
    rgm_cola.reservar_siguiente()  # EN_CURSO, reservado_en = ahora

    assert rgm_cola.vigilar_items_en_curso_colgados() == 0
    assert db.obtener_item_rgm(item["id"])["estado"] == "EN_CURSO"


def test_vigilar_colgados_libera_a_pendiente_con_motivo_connector_si_quedan_reintentos(cliente):
    admin = _crear_admin("Admin P8d Dos", "admin.p8ddos")
    _crear_placa("COL222", admin["id"])
    item = rgm_cola.solicitar_consulta("COL222", "RGM", "panel", admin["id"])["item"]
    rgm_cola.reservar_siguiente()  # intentos=1 (MAX_INTENTOS_RGM=2: todavía le queda un reintento)
    _envejecer_reserva(item["id"], db.MINUTOS_ABANDONO_ITEM_RGM + 1)

    liberados = rgm_cola.vigilar_items_en_curso_colgados()
    assert liberados == 1
    final = db.obtener_item_rgm(item["id"])
    assert final["estado"] == "PENDIENTE"
    assert final["error"] == "sin respuesta del Connector"


def test_vigilar_colgados_pasa_a_error_tras_agotar_los_reintentos(cliente):
    admin = _crear_admin("Admin P8d Tres", "admin.p8dtres")
    _crear_placa("COL333", admin["id"])
    item = rgm_cola.solicitar_consulta("COL333", "RGM", "panel", admin["id"])["item"]

    rgm_cola.reservar_siguiente()  # intentos=1
    _envejecer_reserva(item["id"], db.MINUTOS_ABANDONO_ITEM_RGM + 1)
    rgm_cola.vigilar_items_en_curso_colgados()  # -> PENDIENTE
    rgm_cola.reservar_siguiente()  # intentos=2 (== MAX_INTENTOS_RGM)
    _envejecer_reserva(item["id"], db.MINUTOS_ABANDONO_ITEM_RGM + 1)

    rgm_cola.vigilar_items_en_curso_colgados()
    final = db.obtener_item_rgm(item["id"])
    assert final["estado"] == "ERROR"
    assert final["error"] == "sin respuesta del Connector"


def test_vigilar_colgados_judicial_usa_motivo_del_hilo_directo_no_del_connector(cliente):
    admin = _crear_admin("Admin P8d Cuatro", "admin.p8dcuatro")
    _crear_placa("COL444", admin["id"])
    item_id = db.crear_item_rgm_cola("COL444", "JUDICIAL", "panel", admin["id"], nombre_judicial="Juan Pérez")
    db.reservar_siguiente_item_rgm_judicial()
    _envejecer_reserva(item_id, db.MINUTOS_ABANDONO_ITEM_RGM + 1)

    rgm_cola.vigilar_items_en_curso_colgados()
    final = db.obtener_item_rgm(item_id)
    assert final["estado"] == "PENDIENTE"
    assert final["error"] == "sin respuesta del hilo de Judicial directo"


def test_vigilar_colgados_queda_anotado_en_diagnostico(cliente):
    admin = _crear_admin("Admin P8d Cinco", "admin.p8dcinco")
    _crear_placa("COL555", admin["id"])
    item = rgm_cola.solicitar_consulta("COL555", "RGM", "panel", admin["id"])["item"]
    rgm_cola.reservar_siguiente()
    _envejecer_reserva(item["id"], db.MINUTOS_ABANDONO_ITEM_RGM + 1)

    rgm_cola.vigilar_items_en_curso_colgados()
    pasos = [d["paso"] for d in db.listar_diagnostico_rgm(50) if d["item_id"] == item["id"]]
    assert "error" in pasos


def test_ya_no_impide_pedirla_de_nuevo_una_vez_liberada(cliente):
    """El bug real del Prompt 8d punto 5: NHP973 quedó "En curso" y el
    aviso "Ya hay una consulta en curso" impedía pedirla de nuevo. Tras
    liberarse sola, un nuevo pedido debe poder encolarse (o reservarse)
    normalmente."""
    admin = _crear_admin("Admin P8d Seis", "admin.p8dseis")
    _crear_placa("COL666", admin["id"])
    item = rgm_cola.solicitar_consulta("COL666", "RGM", "panel", admin["id"])["item"]
    rgm_cola.reservar_siguiente()
    _envejecer_reserva(item["id"], db.MINUTOS_ABANDONO_ITEM_RGM + 1)
    rgm_cola.vigilar_items_en_curso_colgados()

    de_nuevo = rgm_cola.solicitar_consulta("COL666", "RGM", "panel", admin["id"])
    assert de_nuevo["accion"] == "YA_EN_COLA"  # sigue siendo el mismo ítem, pero ya es PENDIENTE, no un limbo
    assert db.obtener_item_rgm(item["id"])["estado"] == "PENDIENTE"
