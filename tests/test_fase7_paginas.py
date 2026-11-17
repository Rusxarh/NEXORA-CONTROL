"""
tests/test_fase7_paginas.py
FASE 7 ("Asfalto y placa"): confirma que las páginas nuevas/renombradas
(Hoy, ficha de Vehículos) responden 200 sin errores del servidor, con
datos reales de una base de prueba (nunca contra MasterPlacas.db ni con
credenciales reales: esta es la forma correcta de probar el render, en
vez de adivinar credenciales de producción).
"""

from datetime import datetime

import database as db
import nexcam_db
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def test_hoy_responde_200_y_renombra_dashboard(cliente):
    admin = _crear_admin("Admin Hoy", "admin.hoy")
    db.insertar_placa("ABC123", "carro", admin["id"], "Prueba")
    _login_web(cliente, "admin.hoy")

    r = cliente.get("/hoy")
    assert r.status_code == 200
    assert "Hoy," in r.text

    r_redirect = cliente.get("/dashboard", follow_redirects=False)
    assert r_redirect.status_code == 303
    assert r_redirect.headers["location"] == "/hoy"


def test_hoy_muestra_coincidencia_de_hoy(cliente):
    admin = _crear_admin("Admin Hoy2", "admin.hoy2")
    db.insertar_placa("DEF456", "carro", admin["id"], "Prueba coincidencia")
    device = "device-hoy"
    import nexcam_api  # noqa: F401  (asegura que el router ya está cargado en main reload)

    sesion = {"usuario_id": admin["id"], "nombre": "Admin Hoy2", "device_uuid": device}
    nexcam_db.registrar_avistamiento(
        sesion, "DEF456", "DEF456", "exact", 0.9, None, None, "prueba",
        None, None, int(datetime.now().timestamp() * 1000), None, None,
    )
    _login_web(cliente, "admin.hoy2")

    r = cliente.get("/hoy")
    assert r.status_code == 200
    assert "DEF456" in r.text
    assert "1" in r.text  # al menos la cifra de coincidencias


def test_ficha_vehiculo_existente(cliente):
    admin = _crear_admin("Admin Ficha", "admin.ficha")
    db.insertar_placa("GHI789", "carro", admin["id"], "Observación de prueba")
    _login_web(cliente, "admin.ficha")

    r = cliente.get("/vehiculos/GHI789")
    assert r.status_code == 200
    assert "GHI 789" in r.text  # formateada por el macro placa()
    assert "Observación de prueba" in r.text


def test_ficha_vehiculo_inexistente_no_rompe(cliente):
    _crear_admin("Admin Ficha2", "admin.ficha2")
    _login_web(cliente, "admin.ficha2")

    r = cliente.get("/vehiculos/ZZZ999")
    assert r.status_code == 200
    assert "No se encontró" in r.text


def test_menu_nuevo_aparece_en_base(cliente):
    admin = _crear_admin("Admin Menu", "admin.menu")
    _login_web(cliente, "admin.menu")
    r = cliente.get("/hoy")
    assert 'href="/hoy"' in r.text
    assert "Vehículos" in r.text
    assert 'href="/dashboard"' not in r.text  # ya no hay enlace del menú a /dashboard
    assert "Inventario" not in r.text
