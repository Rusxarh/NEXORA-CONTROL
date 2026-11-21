"""
tests/test_usuarios_web.py
Prompt 6c punto 3: si el único Admin activo no tiene Telegram registrado
(telegram_id provisional -- siempre negativo), /vincular nunca podrá
emparejarlo solo; la página de Usuarios lo sugiere.
"""

import database as db
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username, telegram_id=None) -> dict:
    uid = db.crear_usuario(
        telegram_id or db.telegram_id_provisional(), nombre, "Admin",
        username=username, password_hash=usuarios.hash_password(CLAVE),
    )
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303
    return cliente


def test_sugiere_vincular_si_el_unico_admin_no_tiene_telegram(cliente):
    _crear_admin("Admin Solo Sin Tele", "admin.solo1")  # telegram_id provisional (negativo)
    _login_web(cliente, "admin.solo1")

    r = cliente.get("/usuarios")
    assert r.status_code == 200
    assert "no tiene un Telegram ID registrado" in r.text
    assert "Admin Solo Sin Tele" in r.text


def test_no_sugiere_si_el_admin_ya_tiene_telegram(cliente):
    _crear_admin("Admin Con Tele", "admin.contele", telegram_id=666777)
    _login_web(cliente, "admin.contele")

    r = cliente.get("/usuarios")
    assert r.status_code == 200
    assert "no tiene un Telegram ID registrado" not in r.text


def test_no_sugiere_si_hay_mas_de_un_admin_activo(cliente):
    _crear_admin("Admin Solo Sin Tele Dos", "admin.solo2")
    _crear_admin("Otro Admin", "admin.otro")
    _login_web(cliente, "admin.solo2")

    r = cliente.get("/usuarios")
    assert r.status_code == 200
    assert "no tiene un Telegram ID registrado" not in r.text
