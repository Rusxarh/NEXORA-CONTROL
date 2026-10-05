"""
puente.py
PRE-PUENTE WEB ↔ NEXORA-CONNECTOR.

Página de prueba AISLADA: demuestra que NEXORA-CONTROL puede enviarle un
mensaje a la extensión NEXORA-CONNECTOR (chrome.runtime.sendMessage) y
recibir una respuesta ("RECIBIDA"). No ejecuta RGM ni Judicial, no toca
la base de datos, no crea ninguna cola: es exclusivamente la demostración
mínima de comunicación bidireccional previa a construir el puente real.

Esta ruta se mantiene separada de vehiculos.py (Consulta real) a propósito,
para no mezclar el flujo de prueba con la pantalla de producción.
"""

from fastapi import APIRouter, Depends, Request

import usuarios
from templates import templates

router = APIRouter()


@router.get("/puente")
def puente_prueba(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
):
    return templates.TemplateResponse(
        request,
        "puente_prueba.html",
        {
            "request": request,
            "usuario_actual": usuario_actual,
            "ruta_activa": "puente",
        },
    )
