"""
judicial_pdf.py
Prompt 8d punto 3: botón "📑 Proceso completo (PDF)" -- NEXORA arma un PDF
con el detalle y TODAS las actuaciones (anotaciones completas, nunca
recortadas como en la tarjeta/Telegram) y lo guarda en disco. reportlab es
la única dependencia nueva (ver requirements.txt): pura Python, sin
binarios de sistema (a diferencia de weasyprint/wkhtmltopdf), igual que el
resto de este proyecto.

CARPETA_PDF sigue el mismo patrón que NEXCAM_FOTOS_DIR (nexcam_db.py):
configurable por variable de entorno, con un valor por defecto dentro del
propio proyecto.
"""

import os
import re
from pathlib import Path
from typing import Optional

import judicial_formato as jf

CARPETA_PDF = Path(
    os.environ.get("NEXORA_PROCESOS_PDF_DIR", str(Path(__file__).parent / "datos_nexcam" / "procesos_pdf"))
)

_RE_NOMBRE_INVALIDO = re.compile(r"[^A-Za-z0-9._-]+")


def nombre_archivo_pdf(radicado: Optional[str], placa: str) -> str:
    """<radicado>-<placa>.pdf, sin caracteres que rompan un nombre de
    archivo en Windows (el radicado y la placa son siempre alfanuméricos
    en la práctica, pero nunca se confía en eso a ciegas)."""
    base_radicado = _RE_NOMBRE_INVALIDO.sub("", radicado or "SIN_RADICADO") or "SIN_RADICADO"
    base_placa = _RE_NOMBRE_INVALIDO.sub("", placa or "SIN_PLACA") or "SIN_PLACA"
    return f"{base_radicado}-{base_placa}.pdf"


def generar_pdf_proceso(detalle: dict, actuaciones: list, placa: str, generado_en=None) -> bytes:
    """Devuelve los bytes del PDF (el llamador decide si lo guarda en
    disco, lo manda por Telegram, o ambos). `actuaciones` SIEMPRE
    completas (sin truncar por tarjeta_proceso) -- este es el único lugar
    donde se muestra la anotación entera."""
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    import datetime as _dt

    generado_en = generado_en or _dt.datetime.now()
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("TituloNexora", parent=estilos["Heading2"], spaceAfter=4)
    estilo_normal = estilos["BodyText"]
    estilo_actuacion = ParagraphStyle("Actuacion", parent=estilos["BodyText"], spaceBefore=8, spaceAfter=2)
    estilo_anotacion = ParagraphStyle("Anotacion", parent=estilos["BodyText"], leftIndent=14, textColor="#333333")

    def _p(texto, estilo=estilo_normal):
        return Paragraph(_escapar_html(texto), estilo)

    piezas = [
        _p(f"Consulta de procesos – Rama Judicial · generado por NEXORA el "
           f"{generado_en.strftime('%d/%m/%Y %H:%M')}", estilo_titulo),
        Spacer(1, 6),
        _p(f"Placa consultada: {placa}"),
        _p(f"Radicado: {detalle.get('radicado') or '—'}"),
        _p(f"Despacho: {jf.normalizar_nombre_visible(detalle.get('despacho'))}"),
    ]
    if detalle.get("ponente"):
        piezas.append(_p(f"Ponente: {jf.normalizar_nombre_visible(detalle.get('ponente'))}"))
    tipo = detalle.get("tipo_proceso")
    clase = detalle.get("clase_proceso")
    if tipo or clase:
        piezas.append(_p(f"Tipo de proceso: {tipo or '—'}{(' · ' + clase) if clase else ''}"))
    if detalle.get("ubicacion"):
        piezas.append(_p(f"Ubicación: {jf.normalizar_nombre_visible(detalle.get('ubicacion'))}"))
    piezas.append(_p(f"Fecha de radicación: {jf.formatear_fecha_corta(detalle.get('fecha_proceso'))}"))
    piezas.append(Spacer(1, 10))
    piezas.append(_p(f"Actuaciones ({len(actuaciones)}), más reciente arriba:", estilo_titulo))

    if not actuaciones:
        piezas.append(_p("Sin actuaciones registradas."))
    for a in actuaciones:
        linea = f"📌 {jf.formatear_fecha_corta(a.get('fecha_actuacion'))} · {a.get('actuacion') or '—'}"
        if a.get("con_documentos"):
            linea += " · con documentos"
        piezas.append(_p(linea, estilo_actuacion))
        if (a.get("anotacion") or "").strip():
            piezas.append(_p(a["anotacion"].strip(), estilo_anotacion))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm,
                             topMargin=1.5 * cm, bottomMargin=1.5 * cm, pageCompression=0)
    doc.build(piezas)
    return buffer.getvalue()


def _escapar_html(texto) -> str:
    """reportlab's Paragraph interpreta el texto como un XML/HTML
    reducido (<b>, <br/>...): el contenido viene de la API judicial real,
    nunca de HTML de confianza, así que se escapa antes de envolver."""
    return (str(texto if texto is not None else ""))\
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def guardar_pdf_proceso(contenido: bytes, radicado: Optional[str], placa: str) -> Path:
    CARPETA_PDF.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA_PDF / nombre_archivo_pdf(radicado, placa)
    ruta.write_bytes(contenido)
    return ruta
