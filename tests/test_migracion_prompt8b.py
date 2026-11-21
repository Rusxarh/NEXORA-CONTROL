"""
tests/test_migracion_prompt8b.py
Prompt 8b, puntos 2 y 3: migraciones de esquema (quitar la FK de
snapshots_placa, activar WAL) y que dos hilos escribiendo a la vez no
revienten con "database is locked".
"""

import sqlite3
import threading

import database as db


def _crear_base_vieja_con_fk(ruta):
    """Esquema de snapshots_placa ANTES del Prompt 8b: con la FK a
    vehiculos.placa que _migrar_snapshots_sin_fk debe quitar."""
    conn = sqlite3.connect(ruta)
    conn.executescript(
        """
        CREATE TABLE vehiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa TEXT NOT NULL UNIQUE
        );
        CREATE TABLE snapshots_placa (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            placa       TEXT NOT NULL,
            fuente      TEXT NOT NULL,
            datos       TEXT,
            creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (placa) REFERENCES vehiculos (placa)
        );
        """
    )
    conn.execute("INSERT INTO vehiculos (placa) VALUES ('ABC123')")
    conn.execute("INSERT INTO snapshots_placa (placa, fuente, datos) VALUES ('ABC123', 'RGM', '{}')")
    conn.commit()
    conn.close()


def test_migrar_snapshots_sin_fk_quita_la_fk_y_conserva_los_datos(tmp_path, monkeypatch):
    ruta = tmp_path / "vieja.db"
    _crear_base_vieja_con_fk(ruta)
    monkeypatch.setattr(db, "DB_PATH", ruta)

    db._migrar_snapshots_sin_fk()

    conn = sqlite3.connect(ruta)
    sql_tabla = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='snapshots_placa'"
    ).fetchone()[0]
    assert "FOREIGN KEY" not in sql_tabla

    fila = conn.execute("SELECT placa, fuente, datos FROM snapshots_placa").fetchone()
    assert fila == ("ABC123", "RGM", "{}")
    conn.close()


def test_migrar_snapshots_sin_fk_permite_snapshot_de_placa_que_no_existe(tmp_path, monkeypatch):
    ruta = tmp_path / "vieja2.db"
    _crear_base_vieja_con_fk(ruta)
    monkeypatch.setattr(db, "DB_PATH", ruta)
    db._migrar_snapshots_sin_fk()

    # Antes del Prompt 8b esto reventaba con sqlite3.IntegrityError
    # (NHP973 nunca se insertó en vehiculos).
    db.crear_snapshot("NHP973", "RGM", "{}")
    assert db.obtener_ultimo_snapshot_por_fuente("NHP973", "RGM") is not None


def test_migrar_snapshots_sin_fk_es_idempotente(tmp_path, monkeypatch):
    ruta = tmp_path / "vieja3.db"
    _crear_base_vieja_con_fk(ruta)
    monkeypatch.setattr(db, "DB_PATH", ruta)

    db._migrar_snapshots_sin_fk()
    db._migrar_snapshots_sin_fk()  # segunda vez: no debe fallar ni duplicar nada

    conn = sqlite3.connect(ruta)
    assert conn.execute("SELECT COUNT(*) FROM snapshots_placa").fetchone()[0] == 1
    conn.close()


def test_migrar_journal_mode_wal_activa_wal(tmp_path, monkeypatch):
    ruta = tmp_path / "nueva.db"
    sqlite3.connect(ruta).close()
    monkeypatch.setattr(db, "DB_PATH", ruta)

    db._migrar_journal_mode_wal()

    conn = sqlite3.connect(ruta)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_dos_hilos_escribiendo_a_la_vez_no_revientan_con_locked(cliente):
    """Prompt 8b punto 3: antes, el hilo de Judicial directo escribiendo a
    la vez que el servidor web producía 'database is locked'. Con WAL +
    busy_timeout, dos hilos escribiendo en paralelo deben terminar los
    dos sin excepciones."""
    usuario_id = db.crear_usuario(db.telegram_id_provisional(), "Admin Lock", "Admin")
    errores = []

    def escribir(sufijo):
        try:
            for i in range(25):
                with db.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO logs_auditoria (usuario_id, accion, observaciones) VALUES (?, ?, ?)",
                        (usuario_id, f"PRUEBA_LOCK_{sufijo}", f"intento {i}"),
                    )
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=escribir, args=(s,)) for s in ("A", "B")]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)

    assert not errores, f"Hilos concurrentes fallaron: {errores}"
    with db.get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM logs_auditoria WHERE accion IN ('PRUEBA_LOCK_A', 'PRUEBA_LOCK_B')"
        ).fetchone()["n"]
    assert total == 50
