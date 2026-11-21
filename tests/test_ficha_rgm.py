"""
tests/test_ficha_rgm.py
Prompt 5 punto 3: la ficha de Vehículos mostraba el JSON crudo de
snapshots_placa para "Datos RGM"; ahora es una tabla legible, con el
JSON original solo detrás de un "Ver datos originales" plegado.
Prompt 6 punto 2: el vehículo (marca/línea/modelo/servicio/serie/motor/
chasis) se movió al bloque principal, bajo la placa -- se lee directo de
`vehiculos` (la misma tabla que el RGM sobrescribe), no del snapshot; la
caja "Datos RGM" ya solo trae fecha de consulta, Actualizar, acreedor y
deudor. Acreedor/deudor quedan filtrados por rol (Etapa 5 §2/§37): antes
el JSON crudo se mostraba completo a CUALQUIER usuario autenticado,
Operador incluido.
"""

import json

import database as db
import rgm_cola
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_usuario(nombre, rol, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, rol,
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def _snapshot_de_prueba():
    return json.dumps({
        "estado": "OK",
        "vehiculo": {"marca": "CHEVROLET", "modelo": "2021", "tipo_servicio": "Particular",
                     "descripcion_bien": "T. Servicio: Particular Linea: TRACKER Motor: ABC123"},
        "acreedor": {"razon_social": "Bancolombia"},
        "deudor_garante": {"nombre": "Juan Pérez", "tipo_identificacion": "CC",
                            "numero_identificacion": "123456789", "ciudad": "Bogotá"},
    })


def test_ficha_admin_ve_tabla_legible_y_acreedor_deudor(cliente):
    """El vehículo (marca/línea/modelo/servicio/motor) debe verse en el
    bloque principal, bajo la placa (Prompt 6 punto 2) -- se arma con
    completar_item (el camino real: el RGM sobrescribe `vehiculos`), no
    insertando el snapshot a mano."""
    admin = _crear_usuario("Admin Ficha RGM", "Admin", "admin.fichargm")
    db.insertar_placa("GCT953", "carro", admin["id"], None)
    item = rgm_cola.solicitar_consulta("GCT953", "RGM", "panel", admin["id"])["item"]
    rgm_cola.completar_item(item["id"], {"rgm": json.loads(_snapshot_de_prueba()), "judicial": None})
    _login_web(cliente, "admin.fichargm")

    r = cliente.get("/vehiculos/GCT953")
    assert r.status_code == 200
    assert "CHEVROLET" in r.text and "TRACKER" in r.text and "2021" in r.text and "Particular" in r.text
    assert "ABC123" in r.text  # motor extraído de descripcion_bien
    assert "según RGM del" in r.text
    assert "Bancolombia" in r.text
    assert "Juan Pérez" in r.text
    assert "123456789" in r.text
    assert "Ver datos originales" in r.text


def test_ficha_filtra_acreedor_y_deudor_sin_rol_admin(cliente):
    """
    La web de NEXORA es Admin-only por diseño (usuarios.login_procesar
    rechaza con 403 a cualquiera que no sea Admin -- un Operador NUNCA
    llega a tener sesión web, así que no se puede probar esto iniciando
    sesión como Operador de verdad). El filtro de rol en la plantilla es
    defensa en profundidad consistente con la regla de Etapa 5 §2/§37
    (antes el JSON crudo se mostraba completo sin filtrar nada): se
    prueba directo contra el motor de plantillas, pasando un
    usuario_actual con rol "Operador" como lo haría cualquier vista.
    """
    from templates import templates

    contexto = {
        "request": None, "usuario_actual": {"rol": "Operador", "nombre": "Oper"},
        "ruta_activa": "vehiculos", "placa": "GCT954",
        "vehiculo": {"placa": "GCT954", "tipo_vehiculo": "carro", "estado": "activo",
                     "fecha_ingreso": "2026-01-01 00:00:00", "fecha_baja": None,
                     "observaciones": None, "marca": "CHEVROLET", "linea": "TRACKER", "color": None,
                     "modelo": "2021", "tipo_servicio": "Particular", "numero_serie": None,
                     "motor": "ABC123", "chasis": None, "rgm_actualizado_en": "2026-11-18 00:00:00",
                     "registrado_por": None},
        "grado_placa": None, "avistamientos": [], "logs": [],
        "snapshot_rgm": {"creado_en": "2026-11-18 00:00:00"},
        "rgm_datos": {
            "acreedor": "Bancolombia", "deudor_nombre": "Juan Pérez",
            "deudor_documento": "CC 123456789", "deudor_ciudad": "Bogotá", "crudo": "{}",
        },
        "snapshot_judicial": None,
    }
    html = templates.get_template("vehiculo_ficha.html").render(contexto)
    assert "CHEVROLET" in html and "TRACKER" in html and "ABC123" in html  # el vehículo SÍ es visible para todos
    assert "Bancolombia" not in html
    assert "Juan Pérez" not in html
    assert "123456789" not in html


def test_ficha_sin_rgm_muestra_mensaje_vacio(cliente):
    admin = _crear_usuario("Admin Ficha RGM3", "Admin", "admin.fichargm3")
    db.insertar_placa("GCT955", "carro", admin["id"], None)
    _login_web(cliente, "admin.fichargm3")

    r = cliente.get("/vehiculos/GCT955")
    assert r.status_code == 200
    # Prompt 6b punto 2: mensaje vacío accionable, no una frase muerta.
    assert "Sin RGM todavía" in r.text
    assert "🔎 Consultar" in r.text


# --------------------------------------------------------------------- Prompt 8 punto 6: tarjetas judiciales

def test_ficha_muestra_tarjetas_judiciales_con_coincidencia_de_acreedor(cliente):
    admin = _crear_usuario("Admin Ficha Judicial", "Admin", "admin.fichajudicial")
    db.insertar_placa("JUD777", "carro", admin["id"], None)
    item_rgm = rgm_cola.solicitar_consulta("JUD777", "RGM", "panel", admin["id"])["item"]
    rgm_cola.completar_item(item_rgm["id"], {
        "rgm": {"estado": "OK", "acreedor": {"razon_social": "BANCO FINANDINA S.A. BIC"}}, "judicial": None,
    })
    db.crear_snapshot("JUD777", "JUDICIAL", json.dumps({
        "nombre_consultado": "BANCO FINANDINA SA BIC",
        "procesos": [{"radicado": "R-FICTICIO-1", "despacho": "Juzgado 3 Civil", "departamento": "ANTIOQUIA",
                      "fecha_radicacion": "2025-01-01", "fecha_ultima_actuacion": "2026-09-01", "privado": False,
                      "id_proceso": 55, "sujetos": [{"rol": "Demandante/accionante", "nombre": "BANCO FINANDINA SA BIC"},
                                                     {"rol": "Demandado", "nombre": "Juan Pérez"}]}],
    }))
    _login_web(cliente, "admin.fichajudicial")

    r = cliente.get("/vehiculos/JUD777")
    assert r.status_code == 200
    assert "R-FICTICIO-1" in r.text
    assert "🏦 Demanda la financiera" in r.text
    assert "Ver proceso" in r.text
    assert 'data-id-proceso="55"' in r.text


def test_ver_proceso_judicial_trae_detalle_y_actuaciones(cliente, monkeypatch):
    import judicial_directo

    admin = _crear_usuario("Admin VerProceso", "Admin", "admin.verproceso")
    db.insertar_placa("VPR111", "carro", admin["id"], None)
    _login_web(cliente, "admin.verproceso")

    monkeypatch.setattr(judicial_directo, "obtener_detalle_proceso",
                        lambda id_proceso: {"id_proceso": id_proceso, "despacho": "Juzgado Ficticio", "tipo_proceso": "Ejecutivo"})
    monkeypatch.setattr(judicial_directo, "obtener_actuaciones_proceso",
                        lambda id_proceso: [{"fecha_actuacion": "2026-01-01", "actuacion": "Auto admisorio"}])

    r = cliente.post("/rgm/judicial/ver-proceso", data={"placa": "VPR111", "id_proceso": 99})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert cuerpo["detalle"]["despacho"] == "Juzgado Ficticio"
    assert cuerpo["actuaciones"][0]["actuacion"] == "Auto admisorio"
    snapshot = db.obtener_ultimo_snapshot_por_fuente("VPR111", "JUDICIAL_DETALLE")
    assert snapshot is not None


def test_ver_proceso_judicial_error_de_red_responde_502(cliente, monkeypatch):
    import judicial_directo

    admin = _crear_usuario("Admin VerProceso2", "Admin", "admin.verproceso2")
    db.insertar_placa("VPR222", "carro", admin["id"], None)
    _login_web(cliente, "admin.verproceso2")

    def _falla(id_proceso):
        raise judicial_directo.ErrorJudicialDirecto("timeout")

    monkeypatch.setattr(judicial_directo, "obtener_detalle_proceso", _falla)

    r = cliente.post("/rgm/judicial/ver-proceso", data={"placa": "VPR222", "id_proceso": 1})
    assert r.status_code == 502
    assert r.json()["ok"] is False


def test_rgm_stream_exige_sesion_admin(cliente):
    r = cliente.get("/rgm/stream", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]
