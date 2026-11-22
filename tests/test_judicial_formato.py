"""
tests/test_judicial_formato.py
Prompt 8d punto 2/3: formato legible de los procesos judiciales (fechas,
nombres, grupos, partición en varios mensajes, paginado de actuaciones).
Todo con datos ficticios escritos a mano -- nunca contra el portal real.
"""

from datetime import datetime

import judicial_formato as jf


def test_formatear_fecha_corta_quita_la_hora_y_usa_dd_mm_aaaa():
    assert jf.formatear_fecha_corta("2026-07-22T00:00:00") == "22/07/2026"
    assert jf.formatear_fecha_corta("2026-07-22") == "22/07/2026"
    assert jf.formatear_fecha_corta("2026-07-22 00:00:00") == "22/07/2026"
    assert jf.formatear_fecha_corta(None) == "—"
    assert jf.formatear_fecha_corta("") == "—"
    assert jf.formatear_fecha_corta("basura") == "—"


def test_normalizar_nombre_visible_mayuscula_inicial_y_siglas_tal_cual():
    assert jf.normalizar_nombre_visible("PEDRO NEL HERNÁNDEZ PEÑALOZA") == "Pedro Nel Hernández Peñaloza"
    assert jf.normalizar_nombre_visible("BANCO FINANDINA S.A. BIC") == "Banco Finandina S.A. BIC"
    assert jf.normalizar_nombre_visible("gm financial colombia s.a.s") == "Gm Financial Colombia S.A.S"
    assert jf.normalizar_nombre_visible(None) == "—"
    assert jf.normalizar_nombre_visible("   ") == "—"


def test_normalizar_nombre_visible_quita_compania_de_financiamiento_y_dobles_espacios():
    assert jf.normalizar_nombre_visible("GM FINANCIAL COLOMBIA COMPAÑIA DE FINANCIAMIENTO S.A.") == "Gm Financial Colombia S.A."
    assert jf.normalizar_nombre_visible("BANCO  FINANDINA   S.A.") == "Banco Finandina S.A."


def test_rol_corto_agrupa_demandante_y_demandado_deja_el_resto_sin_mapear():
    assert jf.rol_corto("Demandante/accionante") == "Demandante"
    assert jf.rol_corto("Demandado/indiciado/causante") == "Demandado"
    assert jf.rol_corto("Defensor Privado") is None
    assert jf.rol_corto("Apoderado") is None
    assert jf.rol_corto(None) is None


def test_recortar_anotacion_respeta_el_limite_y_agrega_puntos_suspensivos():
    corta = "Auto admite demanda."
    assert jf.recortar_anotacion(corta) == corta
    larga = "x" * 500
    recortada = jf.recortar_anotacion(larga, limite=200)
    assert len(recortada) == 201  # 200 + "…"
    assert recortada.endswith("…")


# --------------------------------------------------------------------------
# Tarjetas y partición en varios mensajes
# --------------------------------------------------------------------------

def _proceso(radicado, despacho="Juzgado 002 Civil Municipal", departamento="Bogotá",
             fecha_radicacion="2026-05-07", fecha_ultima_actuacion="2026-07-22T00:00:00",
             demandante="GM Financial Colombia S.A.", demandado="Pedro Nel Hernández Peñaloza",
             coincide_acreedor=False, privado=False, id_proceso=1):
    return {
        "radicado": radicado, "despacho": despacho, "departamento": departamento,
        "fecha_radicacion": fecha_radicacion, "fecha_ultima_actuacion": fecha_ultima_actuacion,
        "sujetos": [] if privado else [
            {"rol": "Demandante/accionante", "nombre": demandante},
            {"rol": "Demandado/indiciado/causante", "nombre": demandado},
        ],
        "coincide_acreedor": coincide_acreedor, "privado": privado, "id_proceso": id_proceso,
    }


def test_construir_tarjetas_judicial_arma_grupos_y_tarjetas_legibles():
    procesos = [
        _proceso("11001400302220260067500", coincide_acreedor=True, id_proceso=10),
        _proceso("11001400302220260067501", coincide_acreedor=False, id_proceso=11),
    ]
    armado = jf.construir_tarjetas_judicial("GCT953", procesos, fecha_consulta=datetime(2026, 11, 21))
    texto = "\n\n".join(armado["piezas"])
    assert "Procesos de GCT953 · 2 encontrados · consulta del 21/11/2026" in texto
    assert "🏦 De la financiera (1)" in texto
    assert "📁 Otros (1)" in texto
    assert "<b>1.</b> <code>11001400302220260067500</code>" in texto
    assert "<b>2.</b> <code>11001400302220260067501</code>" in texto
    assert "👤 Demandante: Gm Financial Colombia S.A." in texto
    assert "👤 Demandado: Pedro Nel Hernández Peñaloza" in texto
    assert "22/07/2026" in texto and "T00:00:00" not in texto
    assert armado["numero_por_id_proceso"] == {10: 1, 11: 2}


def test_construir_tarjetas_judicial_privados_sin_radicado_ni_numero():
    procesos = [_proceso("R1", id_proceso=1), _proceso(None, privado=True, id_proceso=2)]
    armado = jf.construir_tarjetas_judicial("GCT953", procesos, fecha_consulta=datetime(2026, 11, 21))
    texto = "\n\n".join(armado["piezas"])
    assert "🔒 Proceso privado" in texto
    assert 2 not in armado["numero_por_id_proceso"]


def test_construir_tarjetas_judicial_escapa_html_de_datos_dinamicos():
    procesos = [_proceso("R1&2", demandante="Banco <Prueba> & Cía", id_proceso=1)]
    armado = jf.construir_tarjetas_judicial("GCT953", procesos, fecha_consulta=datetime(2026, 11, 21))
    texto = "\n\n".join(armado["piezas"])
    assert "&amp;" in texto
    assert "<Prueba>" not in texto
    assert "R1&2" not in texto  # el "&" crudo del radicado tambien se escapa


def test_particionar_mensajes_telegram_nunca_corta_una_tarjeta_a_la_mitad():
    piezas = ["cabecera"] + [f"tarjeta {i}: " + ("x" * 2000) for i in range(5)]
    mensajes = jf.particionar_mensajes_telegram(piezas, limite=4096)
    assert len(mensajes) > 1
    for m in mensajes:
        assert len(m) <= 4096
    # cada tarjeta aparece completa en ALGUN mensaje, nunca partida
    texto_junto = "\n\n".join(mensajes)
    for i in range(5):
        assert f"tarjeta {i}: " + ("x" * 2000) in texto_junto


def test_particionar_mensajes_telegram_una_pieza_que_cabe_sola_no_se_junta_de_mas():
    mensajes = jf.particionar_mensajes_telegram(["a", "b", "c"], limite=4096)
    assert mensajes == ["a\n\nb\n\nc"]


def test_particionar_mensajes_telegram_pieza_mas_grande_que_el_limite_no_lanza():
    piezas = ["x" * 5000]
    mensajes = jf.particionar_mensajes_telegram(piezas, limite=4096)
    assert mensajes == ["x" * 5000]


# --------------------------------------------------------------------------
# Paginado de actuaciones
# --------------------------------------------------------------------------

def test_paginar_actuaciones_10_por_pagina():
    actuaciones = [{"n": i} for i in range(25)]
    p1 = jf.paginar(actuaciones, 1, por_pagina=10)
    assert len(p1["items"]) == 10 and p1["items"][0]["n"] == 0
    assert p1["total_paginas"] == 3
    p3 = jf.paginar(actuaciones, 3, por_pagina=10)
    assert len(p3["items"]) == 5 and p3["items"][0]["n"] == 20


def test_paginar_recorta_pagina_fuera_de_rango_sin_lanzar():
    actuaciones = [{"n": i} for i in range(5)]
    p = jf.paginar(actuaciones, 99, por_pagina=10)
    assert p["pagina"] == 1 and len(p["items"]) == 5
    p0 = jf.paginar(actuaciones, 0, por_pagina=10)
    assert p0["pagina"] == 1


def test_paginar_lista_vacia_no_lanza():
    p = jf.paginar([], 1)
    assert p["items"] == [] and p["total_paginas"] == 1 and p["total"] == 0


def test_texto_actuacion_incluye_fecha_corta_anotacion_recortada_y_documentos():
    a = {"fecha_actuacion": "2026-07-22T00:00:00", "actuacion": "Auto admite demanda",
         "anotacion": "x" * 300, "con_documentos": True}
    t = jf.texto_actuacion(a)
    assert "22/07/2026" in t and "T00:00:00" not in t
    assert "Auto admite demanda" in t
    assert "📎 con documentos" in t
    assert t.count("…") == 1
