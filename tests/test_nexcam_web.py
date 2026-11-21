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


def test_telegram_asignar_usuario_lee_el_formulario_no_la_query(tmp_path, monkeypatch, cliente):
    """Prompt 6c punto 0: usuario_id no tenía Form(), así que FastAPI lo
    leía de la query string (siempre vacía) y el selector de NEXCAM ->
    Telegram nunca guardaba el usuario elegido, por más que se reenviara
    el formulario."""
    import nexcam_telegram

    _crear_admin("Admin Web Tele", "admin.web.tele")
    _login_web(cliente, "admin.web.tele")
    operador_id = db.crear_usuario(555001, "Operador Tele", "Operador")

    ruta_cfg = tmp_path / "telegram.json"
    ruta_cfg.write_text(
        '{"token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabc12", '
        '"chats": [{"id": 777, "nombre": "Chat de prueba", "desde": 0, "usuario_id": null}], '
        '"modo": "alertas", "silencio": false, "en_vivo": true}'
    )
    bot = nexcam_telegram.BotTelegram(ruta_cfg, log=lambda *a: None)
    bot.activo = True
    monkeypatch.setattr(nexcam_telegram, "_instancia", bot)

    r = cliente.post(
        "/nexcam/telegram/777/asignar-usuario", data={"usuario_id": str(operador_id)}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert bot._chat(777)["usuario_id"] == operador_id

    r_sin_usuario = cliente.post(
        "/nexcam/telegram/777/asignar-usuario", data={"usuario_id": ""}, follow_redirects=False,
    )
    assert r_sin_usuario.status_code == 303
    assert bot._chat(777)["usuario_id"] is None


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


def test_panel_en_vivo_pagina_30_y_tiene_el_visor(cliente):
    """Prompt 5 punto 4: NEXCAM -> En vivo con paginación real de 30 (no
    un límite fijo que ignora el resto) y el visor ampliado para las
    miniaturas."""
    admin = _crear_admin("Admin Web Cinco", "admin.web5")
    _login_web(cliente, "admin.web5")
    sesion = {"usuario_id": admin["id"], "nombre": "Admin Web Cinco", "device_uuid": "device-paginado"}
    for i in range(35):
        nexcam_db.registrar_avistamiento(
            sesion, "NOP111", "NOP111", "exact", 0.9, None, None, "prueba",
            None, None, int(datetime.now().timestamp() * 1000) + i, None, None,
        )

    r1 = cliente.get("/nexcam?tab=en-vivo")
    assert r1.status_code == 200
    assert "Página 1 de 2" in r1.text
    assert "visorFotoEnVivo" in r1.text  # el visor ampliado existe en la página

    r2 = cliente.get("/nexcam?tab=en-vivo&pagina=2")
    assert r2.status_code == 200
    assert "Página 2 de 2" in r2.text

    # pagina fuera de rango no revienta: se recorta al máximo disponible.
    r3 = cliente.get("/nexcam?tab=en-vivo&pagina=99")
    assert r3.status_code == 200
    assert "Página 2 de 2" in r3.text
