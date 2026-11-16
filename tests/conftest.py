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
import nexcam_db


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_nexcam.db")
    monkeypatch.setattr(nexcam_db, "NEXCAM_FOTOS_DIR", tmp_path / "fotos")
    db.inicializar_db()
    nexcam_db.inicializar_nexcam_db()

    import main

    importlib.reload(main)
    return TestClient(main.app)
