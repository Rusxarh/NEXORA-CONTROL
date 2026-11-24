"""
tests/test_carga_masiva_subir_archivo.py
Antes del Prompt 10 (bloque A del Prompt 3): POST /vehiculos/carga-masiva/
subir-archivo + GET .../progreso/<job_id>. Todo con datos ficticios.
"""

import time

import database as db
import usuarios
import vehiculos

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def _esperar_trabajo_listo(cliente, job_id, timeout=5):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        r = cliente.get(f"/vehiculos/carga-masiva/progreso/{job_id}")
        datos = r.json()
        if datos["estado"] in ("listo", "error"):
            return datos
        time.sleep(0.05)
    raise AssertionError("El trabajo de carga no terminó a tiempo")


def test_subir_archivo_txt_inserta_placas(cliente):
    admin = _crear_admin("Admin Subir Uno", "admin.subiruno")
    _login_web(cliente, "admin.subiruno")

    contenido = b"AAA111\nBBB222,moto\nCCC333,carro,Observacion ficticia\n"
    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("placas.txt", contenido, "text/plain")})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    trabajo = _esperar_trabajo_listo(cliente, job_id)
    assert trabajo["estado"] == "listo"
    assert trabajo["total"] == 3
    assert trabajo["procesadas"] == 3
    assert trabajo["resumen"]["insertadas"] == 3

    assert db.buscar_placa("AAA111") is not None
    assert db.buscar_placa("BBB222")["tipo_vehiculo"] == "moto"
    assert db.buscar_placa("CCC333")["observaciones"] == "Observacion ficticia"


def test_subir_archivo_csv_consolidado_ficticio_guarda_los_campos(cliente):
    admin = _crear_admin("Admin Subir Dos", "admin.subirdos")
    _login_web(cliente, "admin.subirdos")

    contenido = (
        "﻿Placa;Fecha ingreso;Banco;Deudor;Documento;Marca;Linea;Modelo;Color;Servicio;Clase;Direcciones\n"
        "DDD444;01/06/2022;BANCO FICTICIO;PERSONA FICTICIA;CC 999999;CHEVROLET;TRACKER;2021;ROJO;"
        "Particular;AUTOMOVIL;CL FALSA 123 BOGOTA\n"
    ).encode("utf-8")
    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("CARGAR_base_consolidada.csv", contenido, "text/csv")})
    assert r.status_code == 200
    trabajo = _esperar_trabajo_listo(cliente, r.json()["job_id"])
    assert trabajo["resumen"]["insertadas"] == 1

    vehiculo = db.buscar_placa("DDD444")
    assert vehiculo["banco"] == "BANCO FICTICIO"
    assert vehiculo["deudor_nombre"] == "PERSONA FICTICIA"
    assert vehiculo["deudor_documento"] == "CC 999999"
    assert vehiculo["direcciones"] == "CL FALSA 123 BOGOTA"
    assert vehiculo["clase"] == "AUTOMOVIL"
    assert vehiculo["fecha_ingreso"].startswith("2022-06-01")
    # Un archivo grande no debe dejar un log por placa (ver docstring de
    # aplicar_carga_masiva); sí debe quedar UN resumen del trabajo.
    assert db.listar_logs_de_placa("DDD444") == []


def test_ficha_de_placa_con_fecha_de_ingreso_historica_no_revienta(cliente):
    """Bug real encontrado con una captura de Playwright: una fecha de
    ingreso histórica sin hora (p. ej. "2024-03-15", como la devolvía
    carga_archivos._parsear_fecha_dd_mm_aaaa antes de este commit) hacía
    que camara.calcular_grado_placa reventara con ValueError al abrir la
    ficha (exige "AAAA-MM-DD HH:MM:SS"). Ahora la fecha se guarda siempre
    con hora."""
    admin = _crear_admin("Admin Subir Siete", "admin.subirsiete")
    _login_web(cliente, "admin.subirsiete")

    contenido = (
        "﻿Placa;Fecha ingreso\nFFF777;15/03/2024\n"
    ).encode("utf-8")
    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("CARGAR_base_consolidada.csv", contenido, "text/csv")})
    _esperar_trabajo_listo(cliente, r.json()["job_id"])

    r_ficha = cliente.get("/vehiculos/FFF777")
    assert r_ficha.status_code == 200
    assert "2024" in r_ficha.text


def test_subir_archivo_deja_una_copia_de_seguridad_de_la_base_antes_de_cargar(cliente):
    """Regla de CLAUDE.md / bloque A del Prompt 3: un archivo puede traer
    cientos de miles de filas -- se respalda MasterPlacas.db ANTES."""
    admin = _crear_admin("Admin Subir Ocho", "admin.subirocho")
    _login_web(cliente, "admin.subirocho")

    archivos_antes = set(db.DB_PATH.parent.glob(f"{db.DB_PATH.stem}.antes-carga-*.bak"))
    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("placas.txt", b"MMM444\n", "text/plain")})
    _esperar_trabajo_listo(cliente, r.json()["job_id"])

    archivos_despues = set(db.DB_PATH.parent.glob(f"{db.DB_PATH.stem}.antes-carga-*.bak"))
    nuevos = archivos_despues - archivos_antes
    assert len(nuevos) == 1


def test_subir_archivo_deja_un_log_resumen_del_trabajo(cliente):
    admin = _crear_admin("Admin Subir Tres", "admin.subirtres")
    _login_web(cliente, "admin.subirtres")

    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("placas.txt", b"EEE555\n", "text/plain")})
    _esperar_trabajo_listo(cliente, r.json()["job_id"])

    logs = db.listar_ultimos_logs(10) if hasattr(db, "listar_ultimos_logs") else None
    if logs is None:
        with db.get_connection() as conn:
            logs = [dict(f) for f in conn.execute(
                "SELECT * FROM logs_auditoria WHERE accion = 'CARGA_MASIVA_ARCHIVO' ORDER BY id DESC LIMIT 1"
            ).fetchall()]
    assert len(logs) == 1
    assert "placas.txt" in logs[0]["observaciones"]


def test_subir_archivo_formato_no_reconocido_responde_400(cliente):
    admin = _crear_admin("Admin Subir Cuatro", "admin.subircuatro")
    _login_web(cliente, "admin.subircuatro")

    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("placas.pdf", b"lo que sea", "application/pdf")})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_progreso_de_job_inexistente_responde_404(cliente):
    admin = _crear_admin("Admin Subir Cinco", "admin.subircinco")
    _login_web(cliente, "admin.subircinco")

    r = cliente.get("/vehiculos/carga-masiva/progreso/no-existe")
    assert r.status_code == 404


def test_subir_archivo_exige_sesion_admin(cliente):
    r = cliente.post("/vehiculos/carga-masiva/subir-archivo",
                      files={"archivo": ("placas.txt", b"AAA111\n", "text/plain")},
                      follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_procesar_en_segundo_plano_respeta_el_tamano_del_lote(cliente, monkeypatch):
    """5 placas con lotes de 2 en 2 -> aplicar_carga_masiva se llama 3
    veces (2+2+1) y el avance termina exactamente en 5."""
    import carga_archivos

    admin = _crear_admin("Admin Subir Seis", "admin.subirseis")
    monkeypatch.setattr(carga_archivos, "TAMANO_LOTE_CARGA_ARCHIVO", 2)

    llamadas = []
    original = db.aplicar_carga_masiva

    def _contador(items, usuario_id, **kw):
        llamadas.append(len(items))
        return original(items, usuario_id, **kw)

    monkeypatch.setattr(db, "aplicar_carga_masiva", _contador)

    job_id = "job-de-prueba"
    vehiculos._TRABAJOS_CARGA_ARCHIVO[job_id] = {"total": 0, "procesadas": 0, "estado": "en_curso", "resumen": None, "error": None}
    contenido = b"AAA001\nAAA002\nAAA003\nAAA004\nAAA005\n"
    vehiculos._procesar_archivo_de_carga_en_segundo_plano(job_id, "placas.txt", contenido, admin["id"])

    assert llamadas == [2, 2, 1]
    trabajo = vehiculos._TRABAJOS_CARGA_ARCHIVO[job_id]
    assert trabajo["total"] == 5
    assert trabajo["procesadas"] == 5
    assert trabajo["estado"] == "listo"
    assert trabajo["resumen"]["insertadas"] == 5
