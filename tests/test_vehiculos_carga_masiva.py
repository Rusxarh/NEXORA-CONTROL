"""
tests/test_vehiculos_carga_masiva.py
Prompt 3 bloque A (antes del Prompt 10): database.aplicar_carga_masiva
(inserta placas nuevas, ACTUALIZA las que ya están activas conservando su
fecha de ingreso, nunca toca las dadas de baja) y
vehiculos.procesar_carga_masiva_texto (el texto pegado, reescrito para
usar esa misma función).
"""

import database as db
import nexcam_db
import usuarios
import vehiculos

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def test_aplicar_carga_masiva_inserta_placas_nuevas_con_fecha_original(cliente):
    admin = _crear_admin("Admin CM Uno", "admin.cmuno")
    resumen = db.aplicar_carga_masiva([
        {"placa": "AAA111", "tipo_vehiculo": "carro", "fecha_ingreso_original": "2022-03-15",
         "banco": "BANCO FICTICIO"},
    ], admin["id"])
    assert resumen["insertadas"] == ["AAA111"]
    vehiculo = db.buscar_placa("AAA111")
    assert vehiculo["fecha_ingreso"].startswith("2022-03-15")
    assert vehiculo["banco"] == "BANCO FICTICIO"
    assert vehiculo["estado"] == "activo"


def test_aplicar_carga_masiva_sin_fecha_original_usa_ahora(cliente):
    admin = _crear_admin("Admin CM Dos", "admin.cmdos")
    resumen = db.aplicar_carga_masiva([{"placa": "BBB222", "tipo_vehiculo": "carro"}], admin["id"])
    assert resumen["insertadas"] == ["BBB222"]
    vehiculo = db.buscar_placa("BBB222")
    assert vehiculo["fecha_ingreso"]  # no vacío; no comprobamos el valor exacto (depende del reloj)


def test_aplicar_carga_masiva_actualiza_placa_activa_sin_tocar_fecha_de_ingreso(cliente):
    admin = _crear_admin("Admin CM Tres", "admin.cmtres")
    db.aplicar_carga_masiva([{"placa": "CCC333", "tipo_vehiculo": "carro",
                               "fecha_ingreso_original": "2020-01-01"}], admin["id"])
    fecha_original = db.buscar_placa("CCC333")["fecha_ingreso"]

    resumen = db.aplicar_carga_masiva([
        {"placa": "CCC333", "tipo_vehiculo": "carro", "banco": "BANCO FICTICIO 2",
         "fecha_ingreso_original": "2026-11-24"},  # debe IGNORARSE: la placa ya existe
    ], admin["id"])
    assert resumen["actualizadas"] == ["CCC333"]
    vehiculo = db.buscar_placa("CCC333")
    assert vehiculo["banco"] == "BANCO FICTICIO 2"
    assert vehiculo["fecha_ingreso"] == fecha_original  # conserva la fecha de ingreso original


def test_aplicar_carga_masiva_no_borra_un_dato_existente_con_uno_vacio(cliente):
    admin = _crear_admin("Admin CM Cuatro", "admin.cmcuatro")
    db.aplicar_carga_masiva([{"placa": "DDD444", "tipo_vehiculo": "carro", "marca": "CHEVROLET"}], admin["id"])
    db.aplicar_carga_masiva([{"placa": "DDD444", "tipo_vehiculo": "carro", "color": "ROJO"}], admin["id"])
    vehiculo = db.buscar_placa("DDD444")
    assert vehiculo["marca"] == "CHEVROLET"  # lo que no trajo la segunda carga se conserva
    assert vehiculo["color"] == "ROJO"


def test_aplicar_carga_masiva_no_toca_una_placa_dada_de_baja(cliente):
    admin = _crear_admin("Admin CM Cinco", "admin.cmcinco")
    db.insertar_placa("EEE555", "carro", admin["id"], None)
    db.cambiar_estado_a_baja("EEE555", admin["id"], "de prueba")

    resumen = db.aplicar_carga_masiva([{"placa": "EEE555", "tipo_vehiculo": "carro", "banco": "BANCO FICTICIO"}], admin["id"])
    assert resumen["de_baja_no_tocadas"] == ["EEE555"]
    vehiculo = db.buscar_placa("EEE555")
    assert vehiculo["estado"] == "baja"
    assert vehiculo["banco"] is None  # ninguna reactivación ni dato nuevo, automático


def test_aplicar_carga_masiva_placa_activa_sin_datos_nuevos_queda_sin_cambios(cliente):
    """La mayoría de CARGAR_base_consolidada.csv solo trae placa + fecha de
    ingreso -- para una placa YA activa esa fecha se ignora (se conserva
    la original), así que no debe contarse como "actualizada" si no trajo
    ningún dato realmente nuevo."""
    admin = _crear_admin("Admin CM Diez", "admin.cmdiez")
    db.insertar_placa("JJJ000", "carro", admin["id"], None)

    resumen = db.aplicar_carga_masiva(
        [{"placa": "JJJ000", "tipo_vehiculo": "carro", "fecha_ingreso_original": "2020-01-01"}], admin["id"])
    assert resumen["sin_cambios"] == ["JJJ000"]
    assert resumen["actualizadas"] == []


def test_aplicar_carga_masiva_tipo_invalido_queda_en_invalidas(cliente):
    admin = _crear_admin("Admin CM Seis", "admin.cmseis")
    resumen = db.aplicar_carga_masiva([{"placa": "FFF666", "tipo_vehiculo": "avion"}], admin["id"])
    assert resumen["invalidas"] == ["FFF666"]
    assert db.buscar_placa("FFF666") is None


def test_aplicar_carga_masiva_registrar_auditoria_individual_false_no_crea_log_por_placa(cliente):
    admin = _crear_admin("Admin CM Siete", "admin.cmsiete")
    db.aplicar_carga_masiva([{"placa": "GGG777", "tipo_vehiculo": "carro"}], admin["id"],
                             registrar_auditoria_individual=False)
    logs = db.listar_logs_de_placa("GGG777")
    assert logs == []
    assert db.buscar_placa("GGG777") is not None  # se insertó igual


def test_procesar_carga_masiva_texto_actualiza_en_vez_de_marcar_duplicada(cliente):
    admin = _crear_admin("Admin CM Ocho", "admin.cmocho")
    vehiculos.procesar_carga_masiva_texto("HHH888,carro,Primera observacion", admin["id"])

    resumen = vehiculos.procesar_carga_masiva_texto("HHH888,carro,Segunda observacion", admin["id"])
    assert resumen["actualizadas"] == ["HHH888"]
    assert resumen["insertadas"] == []
    vehiculo = db.buscar_placa("HHH888")
    assert vehiculo["observaciones"] == "Segunda observacion"


def test_procesar_carga_masiva_texto_formato_invalido_no_rompe_el_resto(cliente):
    admin = _crear_admin("Admin CM Nueve", "admin.cmnueve")
    resumen = vehiculos.procesar_carga_masiva_texto("III999,carro\nNO-VALIDA-XYZ,carro", admin["id"])
    assert resumen["insertadas"] == ["III999"]
    assert "NO-VALIDA-XYZ" in resumen["invalidas"]


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def test_ficha_muestra_banco_deudor_solo_a_admin(cliente):
    admin = _crear_admin("Admin CM Diez A", "admin.cmdieza")
    db.aplicar_carga_masiva([{
        "placa": "KKK111", "tipo_vehiculo": "carro", "banco": "BANCO FICTICIO",
        "deudor_nombre": "PERSONA FICTICIA", "deudor_documento": "CC 555", "direcciones": "CL FALSA 1",
    }], admin["id"])
    _login_web(cliente, "admin.cmdieza")

    r = cliente.get("/vehiculos/KKK111")
    assert r.status_code == 200
    assert "BANCO FICTICIO" in r.text
    assert "PERSONA FICTICIA" in r.text
    assert "CL FALSA 1" in r.text


def test_ficha_no_muestra_banco_deudor_a_operador(cliente):
    from templates import templates

    contexto = {
        "request": None, "usuario_actual": {"rol": "Operador", "nombre": "Oper"},
        "ruta_activa": "vehiculos", "placa": "KKK222",
        "vehiculo": {"placa": "KKK222", "tipo_vehiculo": "carro", "estado": "activo",
                     "fecha_ingreso": "2026-01-01 00:00:00", "fecha_baja": None, "observaciones": None,
                     "marca": None, "linea": None, "color": None, "modelo": None, "tipo_servicio": None,
                     "numero_serie": None, "motor": None, "chasis": None, "rgm_actualizado_en": None,
                     "registrado_por": None, "clase": None,
                     "banco": "BANCO FICTICIO", "deudor_nombre": "PERSONA FICTICIA",
                     "deudor_documento": "CC 555", "direcciones": "CL FALSA 1"},
        "grado_placa": None, "avistamientos": [], "logs": [],
        "snapshot_rgm": None, "rgm_datos": None, "snapshot_judicial": None,
    }
    html = templates.get_template("vehiculo_ficha.html").render(contexto)
    assert "BANCO FICTICIO" not in html
    assert "PERSONA FICTICIA" not in html
    assert "CL FALSA 1" not in html


def test_banco_deudor_nunca_llegan_a_la_app_nexcam(cliente):
    """banco/deudor_nombre/deudor_documento/direcciones son sensibles:
    NUNCA deben llegar a la app NEXCAM (que usan también los Operadores).
    nexcam_db._vehiculo_para_app es una lista blanca explícita de campos
    -- esta prueba confirma que sigue siéndolo tras agregar las columnas
    nuevas, no solo lo documenta en un comentario."""
    admin = _crear_admin("Admin CM Once", "admin.cmonce")
    usuario_admin = db.obtener_usuario_por_id(admin["id"])
    db.aplicar_carga_masiva([{
        "placa": "LLL333", "tipo_vehiculo": "carro", "banco": "BANCO FICTICIO",
        "deudor_nombre": "PERSONA FICTICIA", "deudor_documento": "CC 555", "direcciones": "CL FALSA 1",
    }], admin["id"])

    placas_app = {p["placa"]: p for p in nexcam_db.placas_activas_para_app(usuario_admin)}
    campos_app = set(placas_app["LLL333"].keys())
    assert not ({"banco", "deudor_nombre", "deudor_documento", "direcciones"} & campos_app)
