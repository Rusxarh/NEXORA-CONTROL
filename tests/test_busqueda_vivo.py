"""
tests/test_busqueda_vivo.py
Prompt 6c punto 2: búsqueda "mientras se escribe" en Vehículos (filtra la
tabla sin recargar, respetando Todos/Activos/Dados de baja) y en Consulta
(sugerencias debajo del input). Ambas comparten database.buscar_vehiculos_vivo
(prefijo primero, luego "contiene", máx. `limite`).
"""

import database as db
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
    return cliente


def test_buscar_vehiculos_vivo_pone_los_prefijos_primero(cliente):
    admin = _crear_admin("Admin Vivo Uno", "admin.vivo1")
    db.insertar_placa("ABC123", "carro", admin["id"], None)
    db.insertar_placa("ZZABC9", "carro", admin["id"], None)  # contiene "ABC" pero no empieza por ahí

    resultado = db.buscar_vehiculos_vivo("ABC")
    placas = [r["placa"] for r in resultado]
    assert placas.index("ABC123") < placas.index("ZZABC9")


def test_buscar_vehiculos_vivo_respeta_el_estado(cliente):
    admin = _crear_admin("Admin Vivo Dos", "admin.vivo2")
    db.insertar_placa("DEF111", "carro", admin["id"], None)
    db.insertar_placa("DEF222", "carro", admin["id"], None)
    db.cambiar_estado_a_baja("DEF222", admin["id"], None)

    solo_activos = db.buscar_vehiculos_vivo("DEF", estado="activo")
    assert [r["placa"] for r in solo_activos] == ["DEF111"]

    solo_baja = db.buscar_vehiculos_vivo("DEF", estado="baja")
    assert [r["placa"] for r in solo_baja] == ["DEF222"]


def test_buscar_vehiculos_vivo_respeta_el_limite(cliente):
    admin_id = db.crear_usuario(db.telegram_id_provisional(), "Admin Vivo Tres", "Admin")
    for i in range(5):
        db.insertar_placa(f"LIM{i:03d}", "carro", admin_id, None)

    assert len(db.buscar_vehiculos_vivo("LIM", limite=3)) == 3
    assert len(db.buscar_vehiculos_vivo("LIM", limite=50)) == 5


def test_ruta_vehiculos_buscar_vivo_filtra_sin_recargar(cliente):
    admin = _crear_admin("Admin Vivo Cuatro", "admin.vivo4")
    _login_web(cliente, "admin.vivo4")
    db.insertar_placa("GHI333", "carro", admin["id"], None)
    db.insertar_placa("GHI444", "carro", admin["id"], None)
    db.cambiar_estado_a_baja("GHI444", admin["id"], None)

    r = cliente.get("/vehiculos/buscar-vivo?q=GHI&filtro=activo")
    assert r.status_code == 200
    datos = r.json()
    assert [c["placa"] for c in datos["coincidencias"]] == ["GHI333"]
    assert datos["coincidencias"][0]["fecha_ingreso"] != "—"


def test_ruta_vehiculos_buscar_vivo_exige_sesion(cliente):
    r = cliente.get("/vehiculos/buscar-vivo?q=GHI", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_ruta_consulta_autocompletar_ahora_tambien_busca_por_contiene(cliente):
    admin = _crear_admin("Admin Vivo Cinco", "admin.vivo5")
    _login_web(cliente, "admin.vivo5")
    db.insertar_placa("JKL555", "carro", admin["id"], None)
    db.insertar_placa("ZZJKL5", "carro", admin["id"], None)

    r = cliente.get("/consulta/autocompletar?q=JKL")
    assert r.status_code == 200
    placas = [c["placa"] for c in r.json()["coincidencias"]]
    assert "JKL555" in placas and "ZZJKL5" in placas
    assert placas.index("JKL555") < placas.index("ZZJKL5")  # el prefijo primero
