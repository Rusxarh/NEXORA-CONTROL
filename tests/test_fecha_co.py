"""
tests/test_fecha_co.py
Un solo filtro Jinja ("fecha_co") para todas las fechas de la web, en
formato colombiano ("17/11/2026, 11:05 p. m."). Cubre los tres tipos de
valor que de verdad circulan por NEXORA: texto "AAAA-MM-DD HH:MM:SS" (la
convención de toda la base), epoch-ms (lo que manda la app NEXCAM) y un
datetime de Python ya construido.
"""

from datetime import datetime, timezone

import database as db
import nexcam_db
import usuarios
from templates import fecha_co

CLAVE = "clave-de-prueba-123"


def test_fecha_co_texto_de_la_base():
    assert fecha_co("2026-11-17 23:05:00") == "17/11/2026, 11:05 p. m."


def test_fecha_co_manana_am():
    assert fecha_co("2026-11-17 09:03:00") == "17/11/2026, 9:03 a. m."


def test_fecha_co_mediodia_y_medianoche():
    assert fecha_co("2026-11-17 12:00:00") == "17/11/2026, 12:00 p. m."
    assert fecha_co("2026-11-17 00:00:00") == "17/11/2026, 12:00 a. m."


def test_fecha_co_epoch_ms():
    # 2026-11-17 23:05:00 en UTC-5 (Bogotá) == 2026-11-18 04:05:00 UTC.
    ts_ms = int(datetime(2026, 11, 18, 4, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert fecha_co(ts_ms) == "17/11/2026, 11:05 p. m."


def test_fecha_co_vacio_o_none():
    assert fecha_co(None) == "—"
    assert fecha_co("") == "—"


def test_fecha_co_valor_irreconocible_no_falla():
    assert fecha_co("no es una fecha") == "no es una fecha"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def test_fecha_co_aplicada_de_verdad_en_vehiculos_y_ficha(cliente):
    admin = _crear_admin("Admin Fecha", "admin.fecha")
    db.insertar_placa("FEC123", "carro", admin["id"], "Prueba de fecha")
    _login_web(cliente, "admin.fecha")

    vehiculo = db.buscar_placa("FEC123")
    esperado = fecha_co(vehiculo["fecha_ingreso"])

    r_lista = cliente.get("/")
    assert esperado in r_lista.text

    r_ficha = cliente.get("/vehiculos/FEC123")
    assert esperado in r_ficha.text
    # formato colombiano real, no el ISO crudo de la base.
    assert vehiculo["fecha_ingreso"] not in r_ficha.text
