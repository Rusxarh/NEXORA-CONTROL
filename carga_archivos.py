"""
carga_archivos.py
Prompt 3 bloque A (antes del Prompt 10): botón "Subir archivo" en Carga
masiva de placas (.txt, .csv o .xlsx, y arrastrar y soltar). Convierte el
contenido del archivo en la misma lista de "items" que ya consume
database.aplicar_carga_masiva -- la función que también usa el texto
pegado (vehiculos.procesar_carga_masiva_texto): "la MISMA función de
carga se usa en la web y en Telegram".

Reconoce, por encabezado (sin importar mayúsculas, tildes ni orden), las
columnas del archivo real de Gabriel "CARGAR_base_consolidada.csv":
Placa, Fecha ingreso, Banco, Deudor, Documento, Marca, Linea, Modelo,
Color, Servicio, Clase, Direcciones ("Ultima vez en base" y "Fuentes" se
ignoran: no se pidió guardarlas). Un .txt simple (una placa por línea, o
"placa,tipo,observaciones") usa el MISMO parseo línea por línea que el
texto pegado (vehiculos._parsear_linea_carga_simple).

Nunca se procesa nada en memoria de más de TAMANO_LOTE_CARGA_ARCHIVO filas
a la vez (ver vehiculos_web.py / el hilo de trabajo que llama a
database.aplicar_carga_masiva en bloques): un archivo real de Gabriel
(CARGAR_base_consolidada.csv) trae más de 100.000 placas.
"""

import csv
import io
import re
import unicodedata
from typing import Optional

import nexcam_db

TAMANO_LOTE_CARGA_ARCHIVO = 1000
EXTENSIONES_ACEPTADAS = ("txt", "csv", "xlsx", "xlsm")


class FormatoArchivoNoReconocido(Exception):
    """Extensión distinta de .txt/.csv/.xlsx/.xlsm."""


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _normalizar_encabezado(texto) -> str:
    return _sin_tildes(str(texto or "").strip().lower())


# Encabezado normalizado (sin tildes/mayúsculas) -> campo interno que
# espera database.aplicar_carga_masiva. "ultima vez en base" y "fuentes"
# (columnas reales del CSV consolidado) se leen pero no se guardan: no se
# pidió conservarlas.
MAPA_ENCABEZADOS = {
    "placa": "placa",
    "fecha ingreso": "fecha_ingreso_original",
    "fecha de ingreso": "fecha_ingreso_original",
    "fecha ingreso original": "fecha_ingreso_original",
    "fecha de ingreso original": "fecha_ingreso_original",
    "banco": "banco",
    "deudor": "deudor_nombre",
    "documento": "deudor_documento",
    "marca": "marca",
    "linea": "linea",
    "modelo": "modelo",
    "color": "color",
    "servicio": "tipo_servicio",
    "clase": "clase",
    "direcciones": "direcciones",
    "tipo": "tipo_vehiculo",
    "tipo vehiculo": "tipo_vehiculo",
    "observaciones": "observaciones",
}

RE_FECHA_DD_MM_AAAA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _parsear_fecha_dd_mm_aaaa(texto: str) -> Optional[str]:
    """"07/05/2026" -> "2026-05-07 00:00:00" -- mismo formato "AAAA-MM-DD
    HH:MM:SS" que fecha_ingreso usa en TODA la base (texto naive, hora de
    Colombia; ver BOGOTA en nexcam_telegram.py). Sin la hora, camara.
    calcular_grado_placa (que exige ese formato exacto) revienta con
    ValueError apenas se abre la ficha de una placa cargada así -- bug
    real, encontrado con una captura de Playwright antes de este commit.
    None si no tiene ese formato exacto o la fecha no es válida -- nunca
    se inventa una fecha."""
    m = RE_FECHA_DD_MM_AAAA.match((texto or "").strip())
    if not m:
        return None
    dia, mes, anio = (int(x) for x in m.groups())
    try:
        from datetime import date

        return f"{date(anio, mes, dia).isoformat()} 00:00:00"
    except ValueError:
        return None


def normalizar_fila(fila: dict) -> Optional[dict]:
    """
    Convierte una fila cruda (dict encabezado -> valor, tal como vino del
    CSV/Excel) en un item para database.aplicar_carga_masiva, o
    {"placa_cruda": ..., "invalida": True} si trae una placa pero no tiene
    forma de placa de carro (LLLDDD -- el mismo criterio que ya usa
    nexcam_db.RE_PLACA_NEXCAM y que CARGAR_base_consolidada.csv dice haber
    aplicado: "se descartaron celdas que no son placas LLLDDD"). None si
    la fila no trae ninguna placa.
    """
    item = {}
    placa_cruda = None
    for encabezado, valor in fila.items():
        campo = MAPA_ENCABEZADOS.get(_normalizar_encabezado(encabezado))
        if not campo or valor is None:
            continue
        valor = str(valor).strip()
        if not valor:
            continue
        if campo == "placa":
            placa_cruda = valor
            continue
        if campo == "fecha_ingreso_original":
            valor = _parsear_fecha_dd_mm_aaaa(valor)
            if not valor:
                continue
        item[campo] = valor

    if not placa_cruda:
        return None

    placa = placa_cruda.strip().upper().replace("-", "").replace(" ", "")
    if not nexcam_db.RE_PLACA_NEXCAM.match(placa):
        return {"placa_cruda": placa_cruda, "invalida": True}

    item["placa"] = placa
    item.setdefault("tipo_vehiculo", "carro")
    return item


def leer_filas_csv(contenido: bytes) -> list:
    """CARGAR_base_consolidada.csv real: UTF-8 con BOM, separado por ";".
    Se detecta el separador por si algún día llega uno separado por ","."""
    texto = contenido.decode("utf-8-sig", errors="replace")
    primera_linea = texto.splitlines()[0] if texto.splitlines() else ""
    separador = ";" if primera_linea.count(";") >= primera_linea.count(",") else ","
    return list(csv.DictReader(io.StringIO(texto), delimiter=separador))


def leer_filas_xlsx(contenido: bytes) -> list:
    import openpyxl

    libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        hoja = libro.active
        filas = hoja.iter_rows(values_only=True)
        encabezados = [str(c).strip() if c is not None else "" for c in (next(filas, None) or [])]
        resultado = []
        for fila in filas:
            resultado.append({encabezados[i]: fila[i] for i in range(min(len(encabezados), len(fila)))})
        return resultado
    finally:
        libro.close()


def leer_items_de_archivo(nombre_archivo: str, contenido: bytes) -> dict:
    """
    Punto de entrada único. Devuelve {"items": [...], "invalidas": [...]}:
    `items` ya tiene la forma que espera database.aplicar_carga_masiva.
    Lanza FormatoArchivoNoReconocido si la extensión no es .txt/.csv/.xlsx/.xlsm.
    """
    extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
    if extension not in EXTENSIONES_ACEPTADAS:
        raise FormatoArchivoNoReconocido(
            f'Formato de archivo no reconocido: ".{extension}". Usa .txt, .csv o .xlsx.'
        )

    items, invalidas = [], []

    if extension == "txt":
        import vehiculos

        texto = contenido.decode("utf-8-sig", errors="replace")
        for linea in texto.splitlines():
            crudo = vehiculos._parsear_linea_carga_simple(linea)
            if crudo is None:
                continue
            placa = crudo["placa_cruda"].strip().upper().replace("-", "").replace(" ", "")
            if not nexcam_db.RE_PLACA_NEXCAM.match(placa):
                invalidas.append(crudo["placa_cruda"])
                continue
            items.append({"placa": placa, "tipo_vehiculo": crudo["tipo_vehiculo"], "observaciones": crudo["observaciones"]})
    else:
        filas = leer_filas_csv(contenido) if extension == "csv" else leer_filas_xlsx(contenido)
        for fila in filas:
            item = normalizar_fila(fila)
            if item is None:
                continue
            if item.get("invalida"):
                invalidas.append(item["placa_cruda"])
                continue
            items.append(item)

    return {"items": items, "invalidas": invalidas}


def lotes(items: list, tamano: int = TAMANO_LOTE_CARGA_ARCHIVO):
    """Bloques de `tamano` items, en orden -- generador simple (nunca
    carga todo dos veces en memoria)."""
    for i in range(0, len(items), tamano):
        yield items[i:i + tamano]
