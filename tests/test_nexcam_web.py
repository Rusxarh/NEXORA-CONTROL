"""
tests/test_nexcam_web.py
Pruebas de la FASE 2 de la integración NEXCAM: panel web (exclusivo Admin).
"""

from datetime import datetime

import database as db
import nexcam_db
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username) -> dict:
    uid = db.crear_usuario(
        db.telegram_id_provisional(), nombre, "Admin",
        username=username, password_hash=usuarios.hash_password(CLAVE),
    )
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303
    return cliente  # TestClient conserva cookies entre peticiones


def test_panel_sin_sesion_redirige_a_login(cliente):
    r = cliente.get("/nexcam", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_panel_telefonos_autorizar_y_revocar(cliente):
    _crear_admin("Admin Web Uno", "admin.web1")
    _login_web(cliente, "admin.web1")

    cliente.post("/api/device/register", json={"device_uuid": "device-web-1", "brand": "Motorola"})

    r = cliente.get("/nexcam?tab=telefonos")
    assert r.status_code == 200
    assert "device-web-1" in r.text
    assert "Pendiente" in r.text

    r_auth = cliente.post("/nexcam/telefonos/device-web-1/autorizar", follow_redirects=False)
    assert r_auth.status_code == 303
    assert nexcam_db.obtener_dispositivo("device-web-1")["authorized"] is True

    logs = db.listar_logs(usuario_id=None)
    assert any(l["accion"] == "NEXCAM_TELEFONO_AUTORIZADO" for l in logs)

    r_rev = cliente.post("/nexcam/telefonos/device-web-1/revocar", follow_redirects=False)
    assert r_rev.status_code == 303
    dispositivo = nexcam_db.obtener_dispositivo("device-web-1")
    assert dispositivo["authorized"] is False
    assert dispositivo["revoked"] is True


def test_panel_buscar_placa_muestra_ficha_y_avistamientos(cliente):
    admin = _crear_admin("Admin Web Dos", "admin.web2")
    _login_web(cliente, "admin.web2")

    db.insertar_placa("HIJ567", "carro", admin["id"], "Prueba de ficha")
    sesion = {"usuario_id": admin["id"], "nombre": "Admin Web Dos", "device_uuid": "device-ficha"}
    nexcam_db.registrar_avistamiento(
        sesion, "HIJ567", "HIJ567", "exact", 0.95, None, None, "prueba",
        None, None, int(datetime.now().timestamp() * 1000), None, None,
    )

    r = cliente.get("/nexcam?tab=buscar&q=HIJ567")
    assert r.status_code == 200
    assert "HIJ567" in r.text
    assert "Prueba de ficha" in r.text
    assert "Exacta" in r.text


def test_panel_ya_vistas_refleja_carga_masiva(cliente):
    admin = _crear_admin("Admin Web Tres", "admin.web3")
    _login_web(cliente, "admin.web3")

    sesion = {"usuario_id": admin["id"], "nombre": "Admin Web Tres", "device_uuid": "device-cruce"}
    nexcam_db.registrar_avistamiento(
        sesion, "KLM678", "KLM678", "exact", 0.9, None, None, "prueba",
        None, None, int(datetime.now().timestamp() * 1000), None, None,
    )

    r_cruce = cliente.get("/nexcam?tab=ya-vistas")
    assert "Todavía no se ha procesado" in r_cruce.text

    r_carga = cliente.post("/vehiculos/carga-masiva", data={"lineas": "KLM678,carro,Carga de prueba"}, follow_redirects=False)
    assert r_carga.status_code == 303

    r_despues = cliente.get("/nexcam?tab=ya-vistas")
    assert "KLM678" in r_despues.text
    assert "1 placa(s) cargada(s)" in r_despues.text or "ya vista(s)" in r_despues.text


def test_panel_ocr_sin_confusiones(cliente):
    _crear_admin("Admin Web Cuatro", "admin.web4")
    _login_web(cliente, "admin.web4")
    r = cliente.get("/nexcam?tab=ocr")
    assert r.status_code == 200
    assert "Todavía no hay correcciones registradas." in r.text
