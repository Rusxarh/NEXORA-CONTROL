"""
tests/test_ficha_rgm.py
Prompt 5 punto 3: la ficha de Vehículos mostraba el JSON crudo de
snapshots_placa para "Datos RGM"; ahora es una tabla legible (vehículo,
serie/VIN, motor, acreedor y deudor), con el JSON original solo detrás
de un "Ver datos originales" plegado. De paso, acreedor/deudor ahora
quedan filtrados por rol (Etapa 5 §2/§37): antes el JSON crudo se
mostraba completo a CUALQUIER usuario autenticado, Operador incluido.
"""

import json

import database as db
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
    admin = _crear_usuario("Admin Ficha RGM", "Admin", "admin.fichargm")
    db.insertar_placa("GCT953", "carro", admin["id"], None)
    db.crear_snapshot("GCT953", "RGM", _snapshot_de_prueba())
    _login_web(cliente, "admin.fichargm")

    r = cliente.get("/vehiculos/GCT953")
    assert r.status_code == 200
    assert "CHEVROLET" in r.text and "TRACKER" in r.text and "2021" in r.text and "Particular" in r.text
    assert "ABC123" in r.text  # motor extraído de descripcion_bien
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
                     "observaciones": None, "marca": None, "linea": None, "color": None, "modelo": None,
                     "registrado_por": None},
        "grado_placa": None, "avistamientos": [], "logs": [],
        "snapshot_rgm": {"creado_en": "2026-11-18 00:00:00"},
        "rgm_datos": {
            "resumen_vehiculo": "CHEVROLET · TRACKER · 2021", "serie": None, "motor": "ABC123",
            "acreedor": "Bancolombia", "deudor_nombre": "Juan Pérez",
            "deudor_documento": "CC 123456789", "deudor_ciudad": "Bogotá", "crudo": "{}",
        },
        "snapshot_judicial": None,
    }
    html = templates.get_template("vehiculo_ficha.html").render(contexto)
    assert "CHEVROLET" in html  # el dato del vehículo SÍ es visible para todos
    assert "Bancolombia" not in html
    assert "Juan Pérez" not in html
    assert "123456789" not in html


def test_ficha_sin_rgm_muestra_mensaje_vacio(cliente):
    admin = _crear_usuario("Admin Ficha RGM3", "Admin", "admin.fichargm3")
    db.insertar_placa("GCT955", "carro", admin["id"], None)
    _login_web(cliente, "admin.fichargm3")

    r = cliente.get("/vehiculos/GCT955")
    assert r.status_code == 200
    assert "Todavía no se ha consultado RGM para esta placa." in r.text
