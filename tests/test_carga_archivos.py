"""
tests/test_carga_archivos.py
Antes del Prompt 10 (bloque A del Prompt 3): "Subir archivo" en Carga
masiva de placas. Todo con datos FICTICIOS escritos a mano -- nunca con
el archivo real de Gabriel (CARGAR_base_consolidada.csv trae datos
sensibles reales: bancos, deudores, documentos, direcciones).
"""

import io

import carga_archivos as ca


def test_normalizar_fila_reconoce_las_columnas_del_csv_consolidado():
    fila = {
        "Placa": "ABC123", "Fecha ingreso": "07/05/2026", "Ultima vez en base": "09/11/2026",
        "Banco": "BANCO FICTICIO", "Deudor": "JUAN FICTICIO PEREZ", "Documento": "CC 1234567",
        "Marca": "CHEVROLET", "Linea": "TRACKER", "Modelo": "2021", "Color": "ROJO",
        "Servicio": "Particular", "Clase": "AUTOMOVIL",
        "Direcciones": "CL 1 # 2 - 3 BOGOTA",
        "Fuentes": "Excel PLACAS_ACTUSLIZADA",
    }
    item = ca.normalizar_fila(fila)
    assert item == {
        "fecha_ingreso_original": "2026-05-07 00:00:00", "banco": "BANCO FICTICIO",
        "deudor_nombre": "JUAN FICTICIO PEREZ", "deudor_documento": "CC 1234567",
        "marca": "CHEVROLET", "linea": "TRACKER", "modelo": "2021", "color": "ROJO",
        "tipo_servicio": "Particular", "clase": "AUTOMOVIL", "direcciones": "CL 1 # 2 - 3 BOGOTA",
        "placa": "ABC123", "tipo_vehiculo": "carro",
    }


def test_normalizar_fila_sin_importar_mayusculas_ni_tildes_ni_orden():
    fila = {"DEUDOR": "ANA FICTICIA", "plaCA": "xyz987", "línea": "AVEO"}
    item = ca.normalizar_fila(fila)
    assert item["placa"] == "XYZ987"
    assert item["deudor_nombre"] == "ANA FICTICIA"
    assert item["linea"] == "AVEO"


def test_normalizar_fila_campos_vacios_no_se_guardan():
    fila = {"Placa": "AAA111", "Banco": "", "Deudor": "   ", "Fecha ingreso": ""}
    item = ca.normalizar_fila(fila)
    assert item == {"placa": "AAA111", "tipo_vehiculo": "carro"}


def test_normalizar_fila_sin_placa_devuelve_none():
    assert ca.normalizar_fila({"Banco": "FICTICIO"}) is None


def test_normalizar_fila_placa_invalida_queda_marcada():
    item = ca.normalizar_fila({"Placa": "NO-ES-PLACA"})
    assert item == {"placa_cruda": "NO-ES-PLACA", "invalida": True}


def test_normalizar_fila_fecha_con_formato_raro_se_ignora_sin_lanzar():
    item = ca.normalizar_fila({"Placa": "BBB222", "Fecha ingreso": "2026-05-07"})
    assert "fecha_ingreso_original" not in item
    item2 = ca.normalizar_fila({"Placa": "BBB222", "Fecha ingreso": "32/13/2026"})
    assert "fecha_ingreso_original" not in item2


def _csv_ficticio(filas_texto: str) -> bytes:
    return ("﻿" + filas_texto).encode("utf-8")


def test_leer_filas_csv_con_punto_y_coma_y_bom():
    contenido = _csv_ficticio(
        "Placa;Fecha ingreso;Banco;Deudor\n"
        "AAA111;01/06/2026;;\n"
        "BBB222;;BANCO FICTICIO;PERSONA FICTICIA\n"
    )
    filas = ca.leer_filas_csv(contenido)
    assert len(filas) == 2
    assert filas[0]["Placa"] == "AAA111"
    assert filas[1]["Banco"] == "BANCO FICTICIO"


def test_leer_filas_csv_con_coma_tambien_funciona():
    contenido = b"Placa,Banco\nCCC333,BANCO FICTICIO\n"
    filas = ca.leer_filas_csv(contenido)
    assert filas == [{"Placa": "CCC333", "Banco": "BANCO FICTICIO"}]


def test_leer_items_de_archivo_txt_una_placa_por_linea():
    contenido = "ABC123\nXYZ987,moto\nDEF456,carro,Observacion ficticia\n\n".encode("utf-8")
    resultado = ca.leer_items_de_archivo("CARGAR_solo_placas.txt", contenido)
    assert resultado["items"] == [
        {"placa": "ABC123", "tipo_vehiculo": "carro", "observaciones": None},
        {"placa": "XYZ987", "tipo_vehiculo": "moto", "observaciones": None},
        {"placa": "DEF456", "tipo_vehiculo": "carro", "observaciones": "Observacion ficticia"},
    ]
    assert resultado["invalidas"] == []


def test_leer_items_de_archivo_txt_marca_placas_invalidas():
    contenido = "ABC123\nNO-ES-PLACA\n".encode("utf-8")
    resultado = ca.leer_items_de_archivo("archivo.txt", contenido)
    assert len(resultado["items"]) == 1
    assert resultado["invalidas"] == ["NO-ES-PLACA"]


def test_leer_items_de_archivo_csv_consolidado_ficticio():
    contenido = _csv_ficticio(
        "Placa;Fecha ingreso;Ultima vez en base;Banco;Deudor;Documento;Marca;Linea;Modelo;Color;"
        "Servicio;Clase;Direcciones;Fuentes\n"
        "AAA111;01/06/2026;01/06/2026;;;;;;;;;;;Excel FICTICIO\n"
        "BBB222;;;BANCO FICTICIO;PERSONA FICTICIA;CC 999;CHEVROLET;TRACKER;2021;ROJO;Particular;"
        "AUTOMOVIL;CL FALSA 123;Excel FICTICIO\n"
        "NO6PLACA;;;;;;;;;;;;;\n"
    )
    resultado = ca.leer_items_de_archivo("CARGAR_base_consolidada.csv", contenido)
    assert len(resultado["items"]) == 2
    assert resultado["items"][0] == {"placa": "AAA111", "tipo_vehiculo": "carro", "fecha_ingreso_original": "2026-06-01 00:00:00"}
    assert resultado["items"][1]["banco"] == "BANCO FICTICIO"
    assert resultado["items"][1]["deudor_nombre"] == "PERSONA FICTICIA"
    assert resultado["items"][1]["direcciones"] == "CL FALSA 123"
    assert resultado["invalidas"] == ["NO6PLACA"]


def test_leer_items_de_archivo_extension_no_reconocida():
    import pytest

    with pytest.raises(ca.FormatoArchivoNoReconocido):
        ca.leer_items_de_archivo("archivo.pdf", b"lo que sea")


def test_leer_filas_xlsx_ficticio():
    import openpyxl

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(["Placa", "Banco", "Deudor"])
    hoja.append(["CCC333", "BANCO FICTICIO", "PERSONA FICTICIA"])
    buffer = io.BytesIO()
    libro.save(buffer)

    filas = ca.leer_filas_xlsx(buffer.getvalue())
    assert filas == [{"Placa": "CCC333", "Banco": "BANCO FICTICIO", "Deudor": "PERSONA FICTICIA"}]


def test_leer_items_de_archivo_xlsx_ficticio():
    import openpyxl

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(["Placa", "Marca", "Modelo"])
    hoja.append(["DDD444", "RENAULT", "2018"])
    buffer = io.BytesIO()
    libro.save(buffer)

    resultado = ca.leer_items_de_archivo("base.xlsx", buffer.getvalue())
    assert resultado["items"] == [{"placa": "DDD444", "marca": "RENAULT", "modelo": "2018", "tipo_vehiculo": "carro"}]


def test_lotes_parte_en_bloques_del_tamano_pedido():
    items = list(range(2500))
    bloques = list(ca.lotes(items, tamano=1000))
    assert [len(b) for b in bloques] == [1000, 1000, 500]
    assert bloques[0][0] == 0 and bloques[-1][-1] == 2499
