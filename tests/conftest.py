"""
tests/conftest.py
Fixture compartida por toda la suite NEXCAM: una base de datos SQLite
temporal (nunca MasterPlacas.db) y el `app` de FastAPI recargado para que
su inicialización corra contra esa base.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import database as db
import nexcam_api
import nexcam_db
import nexcam_telegram


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_nexcam.db")
    monkeypatch.setattr(nexcam_db, "NEXCAM_FOTOS_DIR", tmp_path / "fotos")
    db.inicializar_db()
    nexcam_db.inicializar_nexcam_db()

    # CRÍTICO: main.py llama a nexcam_telegram.iniciar() en cada import/
    # reload. Sin esto, si alguna vez existe un telegram.json real en la
    # raíz del proyecto (el real, no uno de prueba), CADA prueba que use
    # este fixture arrancaría un bot de Telegram REAL contra la API real
    # de Telegram -- ya pasó una vez en esta sesión (ver checkpoint/reporte
    # de la FASE 5). Se apunta siempre a una ruta que nunca existe.
    monkeypatch.setattr(nexcam_telegram, "CONFIG_PATH", tmp_path / "telegram_nunca_existe.json")
    # Evita que los oyentes (bot) de reloads anteriores se acumulen entre pruebas.
    nexcam_api._oyentes.clear()
    nexcam_api._suscriptores_sse.clear()

    import main

    importlib.reload(main)
    return TestClient(main.app)
