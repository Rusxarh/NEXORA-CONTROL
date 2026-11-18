"""
tests/test_nexcam_api.py
Pruebas de la FASE 1 de la integración NEXCAM.

Usan una base de datos SQLite temporal (nunca MasterPlacas.db): cada
prueba redirige database.DB_PATH a un archivo en tmp_path y recarga
main.py para que su inicialización (db.inicializar_db() +
nexcam_db.inicializar_nexcam_db()) corra contra esa base nueva.
"""

import base64
from datetime import datetime, timedelta

import database as db
import nexcam_db
import usuarios

CLAVE = "clave-de-prueba-123"


# --------------------------------------------------------------------- helpers

def _crear_usuario(nombre, rol, username, grado=None):
    uid = db.crear_usuario(
        db.telegram_id_provisional(), nombre, rol,
        username=username, password_hash=usuarios.hash_password(CLAVE),
    )
    if grado:
        db.establecer_grado_operativo(uid, grado)
    return db.obtener_usuario_por_id(uid)


def _crear_placa(placa, usuario_id, dias_antiguedad=0, activo=True):
    db.insertar_placa(placa, "carro", usuario_id, None)
    if dias_antiguedad:
        fecha = (datetime.now() - timedelta(days=dias_antiguedad)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            conn.execute("UPDATE vehiculos SET fecha_ingreso = ? WHERE placa = ?", (fecha, placa))
    if not activo:
        db.cambiar_estado_a_baja(placa, usuario_id, "prueba")
    return db.buscar_placa(placa)


def _registrar_y_autorizar(cliente, device_uuid="device-001"):
    r = cliente.post("/api/device/register", json={"device_uuid": device_uuid, "brand": "Prueba"})
    assert r.status_code == 200
    assert r.json()["authorized"] is False
    nexcam_db.autorizar_dispositivo(device_uuid)
    return device_uuid


def _login(cliente, username, device_uuid, password=CLAVE):
    return cliente.post(
        "/api/auth/login",
        json={"username": username, "password": password, "device_uuid": device_uuid},
    )


# --------------------------------------------------------------------- registro y autorización

def test_registro_dispositivo_nuevo_queda_sin_autorizar(cliente):
    r = cliente.post("/api/device/register", json={"device_uuid": "nuevo-1", "brand": "X", "model": "Y"})
    assert r.status_code == 200
    assert r.json() == {"device_uuid": "nuevo-1", "authorized": False}

    r2 = cliente.get("/api/device/status", params={"device_uuid": "nuevo-1"})
    assert r2.json() == {"authorized": False, "revoked": False}

    nexcam_db.autorizar_dispositivo("nuevo-1")
    r3 = cliente.get("/api/device/status", params={"device_uuid": "nuevo-1"})
    assert r3.json() == {"authorized": True, "revoked": False}


def test_registro_sin_device_uuid_es_422(cliente):
    r = cliente.post("/api/device/register", json={"brand": "X"})
    assert r.status_code == 422


# --------------------------------------------------------------------- login

def test_login_bien(cliente):
    admin = _crear_usuario("Admin Uno", "Admin", "admin.uno")
    device = _registrar_y_autorizar(cliente)
    r = _login(cliente, "admin.uno", device)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["user"] == {"id": admin["id"], "name": "Admin Uno", "role": "Admin"}
    assert "token" in cuerpo and "expiresAt" in cuerpo


def test_login_clave_mala(cliente):
    _crear_usuario("Admin Dos", "Admin", "admin.dos")
    device = _registrar_y_autorizar(cliente, "device-malo")
    r = _login(cliente, "admin.dos", device, password="otra-clave")
    assert r.status_code == 401
    assert r.json()["code"] == "BAD_CREDENTIALS"


def test_login_telefono_no_registrado(cliente):
    _crear_usuario("Oper Uno", "Operador", "oper.uno", grado="Grado 1")
    r = _login(cliente, "oper.uno", "device-fantasma")
    assert r.status_code == 403
    assert r.json()["code"] == "DEVICE_UNKNOWN"


def test_login_telefono_no_autorizado(cliente):
    _crear_usuario("Oper Dos", "Operador", "oper.dos", grado="Grado 1")
    r0 = cliente.post("/api/device/register", json={"device_uuid": "device-pendiente"})
    assert r0.json()["authorized"] is False
    r = _login(cliente, "oper.dos", "device-pendiente")
    assert r.status_code == 403
    assert r.json()["code"] == "DEVICE_NOT_AUTHORIZED"


def test_login_operador_suspendido(cliente):
    oper = _crear_usuario("Oper Tres", "Operador", "oper.tres", grado="Grado 1")
    db.actualizar_estado_camara(oper["id"], "SUSPENDIDA")
    device = _registrar_y_autorizar(cliente, "device-susp")
    r = _login(cliente, "oper.tres", device)
    assert r.status_code == 403
    assert r.json()["code"] == "CAMERA_SUSPENDED"


def test_login_sesion_reemplazada(cliente):
    _crear_usuario("Admin Tres", "Admin", "admin.tres")
    device = _registrar_y_autorizar(cliente, "device-reemplazo")
    token1 = _login(cliente, "admin.tres", device).json()["token"]
    token2 = _login(cliente, "admin.tres", device).json()["token"]
    assert token1 != token2

    r_viejo = cliente.get("/api/ocr/confusions", headers={
        "Authorization": f"Bearer {token1}", "X-Device-UUID": device,
    })
    assert r_viejo.status_code == 401
    assert r_viejo.json()["code"] == "SESSION_REPLACED"

    r_nuevo = cliente.get("/api/ocr/confusions", headers={
        "Authorization": f"Bearer {token2}", "X-Device-UUID": device,
    })
    assert r_nuevo.status_code == 200


def test_login_token_vencido(cliente):
    _crear_usuario("Admin Cuatro", "Admin", "admin.cuatro")
    device = _registrar_y_autorizar(cliente, "device-vencido")
    token = _login(cliente, "admin.cuatro", device).json()["token"]

    vencido = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE nexcam_tokens SET expira_en = ? WHERE token_hash = ?",
            (vencido, nexcam_db._hash_token(token)),
        )

    r = cliente.get("/api/ocr/confusions", headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device})
    assert r.status_code == 401
    assert r.json()["code"] == "TOKEN_EXPIRED"


def test_login_token_usado_desde_otro_dispositivo(cliente):
    _crear_usuario("Admin Cinco", "Admin", "admin.cinco")
    device = _registrar_y_autorizar(cliente, "device-propio")
    token = _login(cliente, "admin.cinco", device).json()["token"]

    r = cliente.get("/api/ocr/confusions", headers={
        "Authorization": f"Bearer {token}", "X-Device-UUID": "device-ajeno",
    })
    assert r.status_code == 401
    assert r.json()["code"] == "DEVICE_MISMATCH"


def test_errores_quedan_planos_como_espera_app_src_services_api_js(cliente):
    """
    app/src/services/api.js lee `data.code`/`data.message` en la RAÍZ de la
    respuesta (no bajo data.detail.code): `const data = await res.json();
    ... data.code ... data.message`. Si el servidor anida el detalle bajo
    {"detail": {...}} (comportamiento por defecto de FastAPI con
    HTTPException), la app nunca ve el code real y cae siempre al
    UNAUTHORIZED genérico de la línea `data.code || 'UNAUTHORIZED'`, sin
    poder distinguir TOKEN_EXPIRED/DEVICE_REVOKED/SESSION_REPLACED (de lo
    que depende App.js para decidir si borra los datos locales del
    teléfono). Esta prueba repite, uno por uno, los errores que arma
    NEXCAM/mock-api/server.js y confirma que no quedan anidados.
    """
    _crear_usuario("Admin Doce", "Admin", "admin.doce")

    r = cliente.post("/api/device/register", json={"brand": "sin-uuid"})
    assert r.status_code == 422
    assert "detail" not in r.json()
    assert r.json()["message"] == "device_uuid requerido"

    device = _registrar_y_autorizar(cliente, "device-doce")
    r = _login(cliente, "admin.doce", device, password="mala")
    assert r.status_code == 401
    cuerpo = r.json()
    assert "detail" not in cuerpo
    assert cuerpo["code"] == "BAD_CREDENTIALS"
    assert isinstance(cuerpo["message"], str) and cuerpo["message"]

    token = _login(cliente, "admin.doce", device).json()["token"]
    vencido = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE nexcam_tokens SET expira_en = ? WHERE token_hash = ?",
            (vencido, nexcam_db._hash_token(token)),
        )
    r = cliente.get("/api/ocr/confusions", headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device})
    assert r.status_code == 401
    cuerpo = r.json()
    assert "detail" not in cuerpo
    assert cuerpo["code"] == "TOKEN_EXPIRED"
    # Esto es justo lo que App.js usa para decidir "no borrar datos" (no es
    # DEVICE_REVOKED/DEVICE_MISMATCH) y mostrar "Tu sesión expiró.".
    assert cuerpo["code"] not in ("DEVICE_REVOKED", "DEVICE_MISMATCH")

    # El caso que reportó el bug: revocar el teléfono y que la siguiente
    # llamada autenticada traiga DEVICE_REVOKED plano en la raíz (de eso
    # depende que App.js SÍ borre los datos locales: `revocado = code ===
    # 'DEVICE_REVOKED' || code === 'DEVICE_MISMATCH'`).
    _crear_usuario("Oper Doce", "Operador", "oper.doce", grado="Grado 1")
    device_oper = _registrar_y_autorizar(cliente, "device-revocar")
    token_oper = _login(cliente, "oper.doce", device_oper).json()["token"]
    nexcam_db.revocar_dispositivo(device_oper)
    r = cliente.get("/api/ocr/confusions", headers={
        "Authorization": f"Bearer {token_oper}", "X-Device-UUID": device_oper,
    })
    assert r.status_code == 401
    cuerpo = r.json()
    assert "detail" not in cuerpo
    assert cuerpo["code"] == "DEVICE_REVOKED"


# --------------------------------------------------------------------- sync de placas + grado

def test_sync_completa_filtra_por_grado_operativo(cliente):
    admin = _crear_usuario("Admin Seis", "Admin", "admin.seis")
    oper = _crear_usuario("Oper Grado2", "Operador", "oper.grado2", grado="Grado 2")

    _crear_placa("ABC123", admin["id"], dias_antiguedad=0)   # Grado 1 (recién ingresada)
    _crear_placa("XYZ999", admin["id"], dias_antiguedad=20)  # Grado 2 (15-29 días)

    usuario_admin = db.obtener_usuario_por_id(admin["id"])
    usuario_oper = db.obtener_usuario_por_id(oper["id"])

    placas_admin = {p["placa"] for p in nexcam_db.placas_activas_para_app(usuario_admin)}
    placas_oper = {p["placa"] for p in nexcam_db.placas_activas_para_app(usuario_oper)}

    assert placas_admin == {"ABC123", "XYZ999"}
    assert placas_oper == {"XYZ999"}  # el Operador Grado 2 NO ve la de 0 días (Grado 1)


def test_sync_incremental_detecta_cambio_y_cruce_de_grado(cliente):
    admin = _crear_usuario("Admin Siete", "Admin", "admin.siete")
    oper = _crear_usuario("Oper Grado2b", "Operador", "oper.grado2b", grado="Grado 2")
    usuario_oper = db.obtener_usuario_por_id(oper["id"])

    # Placa con 14 días: todavía Grado 1, el Operador Grado 2 no la ve.
    _crear_placa("AAA111", admin["id"], dias_antiguedad=14)
    desde_ms = int(datetime.now().timestamp() * 1000)

    cambiadas_antes = {p["placa"] for p in nexcam_db.placas_cambiadas_desde(desde_ms - 1000, usuario_oper)}
    assert "AAA111" not in cambiadas_antes  # todavía no cruzó el umbral de 15 días

    # Retrocede la fila 1 día más (simula que pasó el tiempo y ya tiene 15 días):
    # cruza a Grado 2, el Operador Grado 2 ahora SÍ debería verla aunque la fila
    # en sí no haya cambiado ningún valor.
    with db.get_connection() as conn:
        nueva = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE vehiculos SET fecha_ingreso = ? WHERE placa = 'AAA111'", (nueva,))

    cambiadas_despues = {p["placa"] for p in nexcam_db.placas_cambiadas_desde(desde_ms - 1000, usuario_oper)}
    assert "AAA111" in cambiadas_despues


# --------------------------------------------------------------------- avistamientos (con fotos)

def _foto_b64() -> str:
    return base64.b64encode(b"contenido-jpg-de-prueba").decode("ascii")


def test_subida_avistamiento_con_fotos(cliente):
    admin = _crear_usuario("Admin Ocho", "Admin", "admin.ocho")
    device = _registrar_y_autorizar(cliente, "device-camara")
    token = _login(cliente, "admin.ocho", device).json()["token"]
    _crear_placa("BCD234", admin["id"])

    r = cliente.post(
        "/api/detections/upload",
        headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
        json={
            "placa": "BCD234", "leida": "BCD234", "matchType": "exact", "confidence": 0.97,
            "source": "prueba", "ts": int(datetime.now().timestamp() * 1000),
            "snapshot_b64": _foto_b64(), "vehicle_b64": _foto_b64(),
        },
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert cuerpo["enLista"] is True
    assert cuerpo["motivo"] is None or isinstance(cuerpo["motivo"], str)

    avistamiento = nexcam_db.obtener_avistamiento(cuerpo["id"])
    assert avistamiento["snapshot"] is not None
    assert avistamiento["vehiculo"] is not None
    assert (nexcam_db.NEXCAM_FOTOS_DIR / avistamiento["snapshot"].replace("/fotos/", "")).is_file()


def test_subida_avistamiento_placa_no_en_lista(cliente):
    _crear_usuario("Admin Nueve", "Admin", "admin.nueve")
    device = _registrar_y_autorizar(cliente, "device-camara2")
    token = _login(cliente, "admin.nueve", device).json()["token"]

    r = cliente.post(
        "/api/detections/upload",
        headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
        json={"placa": "ZZZ999", "ts": int(datetime.now().timestamp() * 1000)},
    )
    assert r.status_code == 200
    assert r.json()["enLista"] is False
    assert r.json()["motivo"] is None


# --------------------------------------------------------------------- retro-cruce

def test_retro_cruce_exacto_y_probable(cliente):
    admin = _crear_usuario("Admin Diez", "Admin", "admin.diez")
    device = _registrar_y_autorizar(cliente, "device-retro")
    sesion = {"usuario_id": admin["id"], "nombre": "Admin Diez", "device_uuid": device}

    nexcam_db.registrar_avistamiento(
        sesion, "CDE345", "CDE345", "exact", 0.9, None, None, "prueba",
        None, None, int(datetime.now().timestamp() * 1000), None, None,
    )
    # Lectura con gemelo O/0 -> variante probable de la misma placa.
    nexcam_db.registrar_avistamiento(
        sesion, "CDE345", "CDE34O" if False else "CDE345", "exact", 0.9, None, None, "prueba",
        None, None, int(datetime.now().timestamp() * 1000), None, None,
    )

    vistas = nexcam_db.vistas_de(["CDE345"])
    assert vistas["CDE345"]["tipo"] == "exacta"
    assert len(vistas["CDE345"]["vistas"]) == 2


# --------------------------------------------------------------------- SSE

def test_sse_recibe_detection_hit(cliente):
    import asyncio

    import nexcam_api

    admin = _crear_usuario("Admin Once", "Admin", "admin.once")
    device = _registrar_y_autorizar(cliente, "device-sse")
    token = _login(cliente, "admin.once", device).json()["token"]
    _crear_placa("EFG456", admin["id"])

    cola = asyncio.Queue()
    nexcam_api._suscriptores_sse.append(cola)
    try:
        r = cliente.post(
            "/api/detections/upload",
            headers={"Authorization": f"Bearer {token}", "X-Device-UUID": device},
            json={"placa": "EFG456", "ts": int(datetime.now().timestamp() * 1000)},
        )
        assert r.status_code == 200
        mensaje = cola.get_nowait()
        assert mensaje.startswith("event: detection.hit\n")
        assert "EFG456" in mensaje
    finally:
        nexcam_api._suscriptores_sse.remove(cola)
