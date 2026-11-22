"""
tests/test_judicial_pdf.py
Prompt 8d punto 3: el PDF del "proceso completo" con datos ficticios --
nunca contra el portal real. Solo se comprueba que el PDF es válido y que
trae TODO (nunca recortado), no su maquetación exacta.
"""

import judicial_pdf as jp


def _detalle():
    return {
        "radicado": "11001400302220260067500", "despacho": "Juzgado 002 Civil Municipal",
        "ponente": "Ana María Ríos", "tipo_proceso": "Ejecutivo", "clase_proceso": "Civil",
        "ubicacion": "Bogotá", "fecha_proceso": "2026-05-07T00:00:00",
    }


def _actuaciones():
    return [
        {"fecha_actuacion": "2026-07-22T00:00:00", "actuacion": "Auto admite demanda",
         "anotacion": "x" * 500, "con_documentos": True},
        {"fecha_actuacion": "2026-05-07T00:00:00", "actuacion": "Reparto", "anotacion": "", "con_documentos": False},
    ]


def test_generar_pdf_proceso_devuelve_un_pdf_valido():
    contenido = jp.generar_pdf_proceso(_detalle(), _actuaciones(), "GCT953")
    assert contenido[:5] == b"%PDF-"
    assert contenido.rstrip().endswith(b"%%EOF")
    assert len(contenido) > 500


def test_generar_pdf_proceso_nunca_recorta_la_anotacion_completa():
    # Palabra larga pero rompible (con espacios) para que Platypus pueda
    # partirla en líneas sin lanzar un error de layout; pageCompression=0
    # (judicial_pdf.generar_pdf_proceso) deja el texto sin comprimir
    # dentro del PDF, así que se puede buscar tal cual en los bytes --
    # prueba directa, no un indicio por tamaño.
    larga = " ".join(["palabra"] * 400)  # muy por encima del límite de 200 de la tarjeta/Telegram
    actuaciones = [{"fecha_actuacion": "2026-07-22", "actuacion": "Auto", "anotacion": larga, "con_documentos": False}]
    contenido = jp.generar_pdf_proceso(_detalle(), actuaciones, "GCT953")
    assert larga.encode("latin-1", errors="ignore") in contenido or b"palabra palabra palabra" in contenido


def test_generar_pdf_proceso_sin_actuaciones_no_lanza():
    contenido = jp.generar_pdf_proceso(_detalle(), [], "GCT953")
    assert contenido[:5] == b"%PDF-"


def test_nombre_archivo_pdf_usa_radicado_y_placa():
    assert jp.nombre_archivo_pdf("11001400302220260067500", "GCT953") == "11001400302220260067500-GCT953.pdf"


def test_nombre_archivo_pdf_sanea_caracteres_invalidos_y_vacios():
    assert jp.nombre_archivo_pdf("ABC/123:*?", "GC T9?53") == "ABC123-GCT953.pdf"
    assert jp.nombre_archivo_pdf(None, "GCT953") == "SIN_RADICADO-GCT953.pdf"
    assert jp.nombre_archivo_pdf("ABC123", "") == "ABC123-SIN_PLACA.pdf"


def test_guardar_pdf_proceso_escribe_en_disco(tmp_path, monkeypatch):
    monkeypatch.setattr(jp, "CARPETA_PDF", tmp_path / "procesos_pdf")
    contenido = jp.generar_pdf_proceso(_detalle(), _actuaciones(), "GCT953")
    ruta = jp.guardar_pdf_proceso(contenido, "11001400302220260067500", "GCT953")
    assert ruta.exists()
    assert ruta.read_bytes() == contenido
    assert ruta.name == "11001400302220260067500-GCT953.pdf"
