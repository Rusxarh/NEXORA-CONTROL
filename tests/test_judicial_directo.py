"""
tests/test_judicial_directo.py
Prompt 8: consultaprocesos.ramajudicial.gov.co es una API JSON pública
(hallazgos de la captura de red de Gabriel, 20/11/2026) -- estas pruebas
NUNCA llaman a la red real (regla de CLAUDE.md): judicial_directo._get se
reemplaza por una versión que devuelve respuestas grabadas (ficticias).
"""

import judicial_directo as jd


def test_parsear_sujetos_con_barra_y_minusculas():
    """El bug real del parser viejo (DOM, judicial.js): no reconocía
    roles con "/" ni palabras en minúscula después de la barra. Acá el
    portal ya separa con "|", así que no hace falta adivinar nada."""
    texto = "Demandante/accionante: BANCO FICTICIO S.A.| Demandado/indiciado/causante: Juan Pérez| Defensor Privado: Ana Gómez"
    sujetos = jd._parsear_sujetos(texto)
    assert sujetos == [
        {"rol": "Demandante/accionante", "nombre": "BANCO FICTICIO S.A."},
        {"rol": "Demandado/indiciado/causante", "nombre": "Juan Pérez"},
        {"rol": "Defensor Privado", "nombre": "Ana Gómez"},
    ]


def test_parsear_sujetos_vacio_o_sin_dos_puntos_no_rompe():
    assert jd._parsear_sujetos(None) == []
    assert jd._parsear_sujetos("") == []
    assert jd._parsear_sujetos("texto sin rotulo") == []
    assert jd._parsear_sujetos("Demandante: X| ") == [{"rol": "Demandante", "nombre": "X"}]


def test_normalizar_proceso_trae_los_campos_esperados():
    crudo = {
        "idProceso": 123, "idConexion": 1, "llaveProceso": "05001310300120250012300",
        "fechaProceso": "2025-01-10", "fechaUltimaActuacion": "2026-10-01",
        "despacho": "Juzgado 1 Civil del Circuito", "departamento": "ANTIOQUIA",
        "esPrivado": False, "sujetosProcesales": "Demandante/accionante: BANCO X| Demandado: Y",
    }
    proceso = jd._normalizar_proceso(crudo)
    assert proceso["radicado"] == "05001310300120250012300"
    assert proceso["id_proceso"] == 123
    assert proceso["privado"] is False
    assert proceso["sujetos"][0] == {"rol": "Demandante/accionante", "nombre": "BANCO X"}


def test_buscar_procesos_por_nombre_recorre_todas_las_paginas(monkeypatch):
    paginas = {
        1: {
            "procesos": [{"idProceso": 1, "llaveProceso": "R1", "sujetosProcesales": "Demandante: X| Demandado: Y",
                          "despacho": "D1", "departamento": "DEP1", "esPrivado": False}],
            "paginacion": {"cantidadPaginas": 2, "pagina": 1},
        },
        2: {
            "procesos": [{"idProceso": 2, "llaveProceso": "R2", "sujetosProcesales": "Demandante: X| Demandado: Z",
                          "despacho": "D2", "departamento": "DEP2", "esPrivado": True}],
            "paginacion": {"cantidadPaginas": 2, "pagina": 2},
        },
    }
    llamadas = []

    def _get_falso(ruta, params=None):
        llamadas.append((ruta, params))
        return paginas[params["pagina"]]

    monkeypatch.setattr(jd, "_get", _get_falso)
    monkeypatch.setattr(jd, "_esperar_turno", lambda: None)  # las pruebas no esperan 1.5 s de verdad

    procesos = jd.buscar_procesos_por_nombre("BANCO FICTICIO X")
    assert [p["radicado"] for p in procesos] == ["R1", "R2"]
    assert procesos[1]["privado"] is True
    assert len(llamadas) == 2
    assert llamadas[0][0] == "Procesos/Consulta/NombreRazonSocial"


def test_obtener_actuaciones_mas_reciente_primero(monkeypatch):
    def _get_falso(ruta, params=None):
        return {
            "actuaciones": [
                {"fechaActuacion": "2026-01-01", "actuacion": "Primera", "anotacion": None, "conDocumentos": False},
                {"fechaActuacion": "2026-06-01", "actuacion": "Segunda", "anotacion": None, "conDocumentos": True},
            ],
            "paginacion": {"cantidadPaginas": 1},
        }

    monkeypatch.setattr(jd, "_get", _get_falso)
    actuaciones = jd.obtener_actuaciones_proceso(123)
    assert [a["actuacion"] for a in actuaciones] == ["Segunda", "Primera"]  # más reciente arriba


def test_obtener_sujetos_proceso_cae_a_none_si_falla(monkeypatch):
    def _get_falso(ruta, params=None):
        raise jd.ErrorJudicialDirecto("404")

    monkeypatch.setattr(jd, "_get", _get_falso)
    assert jd.obtener_sujetos_proceso(999) is None


def test_normalizar_nombre_financiero_ignora_sufijos_y_puntuacion():
    assert jd.normalizar_nombre_financiero("BANCO FINANDINA SA BIC") == jd.normalizar_nombre_financiero(
        "BANCO FINANDINA S.A. BIC")
    assert jd.normalizar_nombre_financiero(None) == ""


def test_ordenar_y_marcar_procesos_financiera_primero_y_sin_repetidos():
    procesos = [
        {"radicado": "R1", "fecha_ultima_actuacion": "2026-01-01",
         "sujetos": [{"rol": "Demandante/accionante", "nombre": "OTRO BANCO S.A."}]},
        {"radicado": "R2", "fecha_ultima_actuacion": "2025-06-01",
         "sujetos": [{"rol": "Demandante/accionante", "nombre": "BANCO FINANDINA S.A. BIC"}]},
        {"radicado": "R2", "fecha_ultima_actuacion": "2025-06-01",  # repetido, debe quedar una sola vez
         "sujetos": [{"rol": "Demandante/accionante", "nombre": "BANCO FINANDINA S.A. BIC"}]},
        {"radicado": "R3", "fecha_ultima_actuacion": "2026-06-01",
         "sujetos": [{"rol": "Demandado", "nombre": "Juan Pérez"}]},
    ]
    ordenados = jd.ordenar_y_marcar_procesos(procesos, "BANCO FINANDINA SA BIC")
    assert [p["radicado"] for p in ordenados] == ["R2", "R3", "R1"]  # financiera primero, luego por fecha
    assert ordenados[0]["coincide_acreedor"] is True
    assert ordenados[1]["coincide_acreedor"] is False
    assert len(ordenados) == 3  # el R2 repetido no se duplica


def test_consultar_judicial_por_nombre_arma_el_shape_para_el_snapshot(monkeypatch):
    def _get_falso(ruta, params=None):
        return {
            "procesos": [{"idProceso": 1, "llaveProceso": "R1", "sujetosProcesales": "Demandante: X| Demandado: Y",
                          "despacho": "D1", "departamento": "DEP1", "esPrivado": False}],
            "paginacion": {"cantidadPaginas": 1},
        }

    monkeypatch.setattr(jd, "_get", _get_falso)
    resultado = jd.consultar_judicial_por_nombre("BANCO FICTICIO X")
    assert resultado["nombre_consultado"] == "BANCO FICTICIO X"
    assert len(resultado["procesos"]) == 1


# --------------------------------------------------------------------- Prompt 8d punto 3: cache de detalle/actuaciones

def test_obtener_detalle_y_actuaciones_cacheado_no_repite_la_peticion_dentro_de_6h(cliente, monkeypatch):
    llamadas = {"n": 0}

    def _get_falso(ruta, params=None):
        llamadas["n"] += 1
        if ruta.startswith("Proceso/Detalle"):
            return {"llaveProceso": "R1", "despacho": "D1"}
        return {"actuaciones": [], "paginacion": {"cantidadPaginas": 1}}

    monkeypatch.setattr(jd, "_get", _get_falso)

    r1 = jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 10)
    assert r1["detalle"]["radicado"] == "R1"
    llamadas_tras_primera = llamadas["n"]
    assert llamadas_tras_primera > 0

    r2 = jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 10)
    assert r2 == r1
    assert llamadas["n"] == llamadas_tras_primera  # no volvió a pedirlo


def test_obtener_detalle_y_actuaciones_cacheado_forzar_vuelve_a_pedirlo(cliente, monkeypatch):
    llamadas = {"n": 0}

    def _get_falso(ruta, params=None):
        llamadas["n"] += 1
        if ruta.startswith("Proceso/Detalle"):
            return {"llaveProceso": "R1"}
        return {"actuaciones": [], "paginacion": {"cantidadPaginas": 1}}

    monkeypatch.setattr(jd, "_get", _get_falso)
    jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 10)
    tras_primera = llamadas["n"]
    jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 10, forzar=True)
    assert llamadas["n"] > tras_primera


def test_obtener_detalle_y_actuaciones_cacheado_distintos_id_proceso_no_se_pisan(cliente, monkeypatch):
    def _get_falso(ruta, params=None):
        if ruta == "Proceso/Detalle/10":
            return {"llaveProceso": "R10"}
        if ruta == "Proceso/Detalle/20":
            return {"llaveProceso": "R20"}
        return {"actuaciones": [], "paginacion": {"cantidadPaginas": 1}}

    monkeypatch.setattr(jd, "_get", _get_falso)
    r10 = jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 10)
    r20 = jd.obtener_detalle_y_actuaciones_cacheado("GCT953", 20)
    assert r10["detalle"]["radicado"] == "R10"
    assert r20["detalle"]["radicado"] == "R20"
