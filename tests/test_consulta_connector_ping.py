"""
tests/test_consulta_connector_ping.py
Bug visto en una prueba real (Brave, extensión NO disponible): la página
/consulta mandaba igual el latido de trabajador (fetch directo a
/rgm/trabajador/latido), así que el servidor creía que SÍ había alguien
atendiendo la cola aunque chrome.runtime nunca respondiera. No hay
infraestructura de pruebas de JavaScript en este proyecto (solo
pytest contra las rutas FastAPI); esta prueba confirma, sobre el HTML/JS
que de verdad se sirve, que el arreglo quedó en el archivo: un ping real
a la extensión antes de cada latido, el badge en rojo cuando no responde,
y que ciclo() no reserva nada de la cola mientras el Connector no esté
disponible (antes la dejaba tomar un ítem y terminaba marcándolo Error).
"""

import database as db
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def test_latido_solo_se_manda_si_el_ping_a_la_extension_responde(cliente):
    admin = _crear_admin("Admin Ping", "admin.ping")
    _login_web(cliente, "admin.ping")

    r = cliente.get("/consulta")
    assert r.status_code == 200
    js = r.text

    assert "function pingExtension(" in js
    assert '{ accion: "PING" }' in js

    # latido() debe llamar a pingExtension y solo hacer fetch al servidor
    # cuando el callback diga "disponible"; si no, badge en rojo y ni
    # intenta /rgm/trabajador/latido.
    inicio = js.index("function latido()")
    cuerpo_latido = js[inicio:inicio + 900]
    assert "pingExtension(function (disponible)" in cuerpo_latido
    assert "Connector no disponible: revisa la extensión" in cuerpo_latido
    assert '/rgm/trabajador/latido' in cuerpo_latido


def test_ciclo_no_reserva_de_la_cola_sin_connector_disponible(cliente):
    admin = _crear_admin("Admin Ping2", "admin.ping2")
    _login_web(cliente, "admin.ping2")

    r = cliente.get("/consulta")
    js = r.text

    inicio = js.index("function ciclo()")
    cuerpo_ciclo = js[inicio:inicio + 600]
    assert "if (!connectorDisponible)" in cuerpo_ciclo
    # el chequeo tiene que estar ANTES del fetch a /rgm/trabajador/siguiente
    assert cuerpo_ciclo.index("connectorDisponible") < cuerpo_ciclo.index("/rgm/trabajador/siguiente")


def test_connector_disponible_arranca_en_false(cliente):
    """Hasta que el primer ping no confirme que la extensión responde,
    ciclo() no debe reservar nada -- el valor inicial no puede ser true."""
    admin = _crear_admin("Admin Ping3", "admin.ping3")
    _login_web(cliente, "admin.ping3")

    r = cliente.get("/consulta")
    js = r.text
    assert "var connectorDisponible = false;" in js
