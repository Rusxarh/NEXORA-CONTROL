"""
tests/test_consulta_dos_columnas.py
Prompt 8d punto 4: /consulta en dos columnas (izquierda: buscar/
consultar/cola/últimas 20; derecha: la placa elegida, con los procesos
judiciales en tarjetas con filtro/buscador/PDF) y el endpoint AJAX
/consulta/panel-derecho que carga esa columna sin recargar la página.
"""

import json

import database as db
import rgm_cola
import usuarios

CLAVE = "clave-de-prueba-123"


def _crear_admin(nombre, username):
    uid = db.crear_usuario(db.telegram_id_provisional(), nombre, "Admin",
                            username=username, password_hash=usuarios.hash_password(CLAVE))
    return db.obtener_usuario_por_id(uid)


def _login_web(cliente, username):
    r = cliente.post("/login", data={"username": username, "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303


def _cargar_placa_con_judicial(admin, placa):
    db.insertar_placa(placa, "carro", admin["id"], None)
    item_rgm = rgm_cola.solicitar_consulta(placa, "RGM", "panel", admin["id"])["item"]
    rgm_cola.completar_item(item_rgm["id"], {
        "rgm": {"estado": "OK", "acreedor": {"razon_social": "BANCO FINANDINA S.A. BIC"}}, "judicial": None,
    })
    db.crear_snapshot(placa, "JUDICIAL", json.dumps({
        "nombre_consultado": "BANCO FINANDINA SA BIC",
        "procesos": [
            {"radicado": "R-COL-1", "despacho": "JUZGADO 002 CIVIL MUNICIPAL", "departamento": "BOGOTA",
             "fecha_radicacion": "2026-05-07", "fecha_ultima_actuacion": "2026-07-22T00:00:00", "privado": False,
             "id_proceso": 201, "sujetos": [{"rol": "Demandante/accionante", "nombre": "BANCO FINANDINA SA BIC"},
                                             {"rol": "Demandado", "nombre": "PEDRO NEL HERNANDEZ"}]},
        ],
    }))


def test_consulta_tiene_dos_columnas_y_tarjetas_con_filtro_y_pdf(cliente):
    admin = _crear_admin("Admin Dos Columnas", "admin.doscolumnas")
    _cargar_placa_con_judicial(admin, "COL100")
    _login_web(cliente, "admin.doscolumnas")

    r = cliente.get("/consulta", params={"q": "COL100"})
    assert r.status_code == 200
    assert "grid-consulta" in r.text
    assert 'id="panelDerechoConsulta"' in r.text
    assert "judicial-panel" in r.text
    assert "boton-filtro-judicial" in r.text
    assert 'data-buscador-judicial' in r.text
    assert "/rgm/judicial/pdf-proceso?placa=COL100&id_proceso=201" in r.text
    # Fecha corta en la tarjeta (el JSON crudo, con T00:00:00, solo va
    # detras de "Ver datos originales" -- eso sigue permitido).
    assert "Última actuación 22/07/2026" in r.text
    assert 'data-placa-fila="COL100"' in r.text  # filas de Últimas 20 clicables


def test_panel_derecho_ajax_devuelve_solo_el_fragmento(cliente):
    admin = _crear_admin("Admin Panel AJAX", "admin.panelajax")
    _cargar_placa_con_judicial(admin, "COL200")
    _login_web(cliente, "admin.panelajax")

    r = cliente.get("/consulta/panel-derecho", params={"q": "COL200"})
    assert r.status_code == 200
    assert "COL200" in r.text
    assert "judicial-panel" in r.text
    # Es un fragmento, no la pagina completa (sin el layout de base.html).
    assert "<nav" not in r.text and "drawerProceso" not in r.text


def test_panel_derecho_ajax_sin_placa_muestra_el_aviso(cliente):
    admin = _crear_admin("Admin Panel Vacio", "admin.panelvacio")
    _login_web(cliente, "admin.panelvacio")

    r = cliente.get("/consulta/panel-derecho")
    assert r.status_code == 200
    assert "Busca una placa" in r.text


def test_panel_derecho_ajax_placa_inexistente(cliente):
    admin = _crear_admin("Admin Panel Noexiste", "admin.panelnoexiste")
    _login_web(cliente, "admin.panelnoexiste")

    r = cliente.get("/consulta/panel-derecho", params={"q": "ZZZ999"})
    assert r.status_code == 200
    assert "No se encontró ningún vehículo" in r.text


def test_consulta_sin_judicial_no_muestra_el_panel_de_tarjetas(cliente):
    admin = _crear_admin("Admin Sin Judicial", "admin.sinjudicial")
    db.insertar_placa("COL300", "carro", admin["id"], None)
    _login_web(cliente, "admin.sinjudicial")

    r = cliente.get("/consulta", params={"q": "COL300"})
    assert r.status_code == 200
    assert "COL300" in r.text
    assert "judicial-panel" not in r.text
