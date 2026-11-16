"""
tests/test_migrar_nexcam.py
Pruebas de la FASE 4 (migrar_nexcam.py) contra una base de datos de origen
FALSA (nunca la real de NEXCAM/mock-api): confirma que es idempotente
(correrlo dos veces no duplica nada) y que --simular no cambia nada.
"""

import sqlite3

import migrar_nexcam
import nexcam_db


def _crear_origen_falso(tmp_path):
    carpeta = tmp_path / "mock_nexcam"
    (carpeta / "datos" / "fotos" / "2026-11-16").mkdir(parents=True)
    ruta_db = carpeta / "datos" / "nexcam.db"
    conn = sqlite3.connect(ruta_db)
    conn.executescript(
        """
        CREATE TABLE devices (device_uuid TEXT PRIMARY KEY, android_id TEXT, brand TEXT, model TEXT,
            os_version TEXT, app_version TEXT, authorized INTEGER, revoked INTEGER, last_seen INTEGER,
            pending INTEGER, user_name TEXT);
        CREATE TABLE sightings (id INTEGER PRIMARY KEY, placa TEXT, leida TEXT, match_type TEXT,
            en_lista INTEGER, motivo TEXT, confidence REAL, sharpness REAL, frames INTEGER, source TEXT,
            lat REAL, lng REAL, ts INTEGER, received_at INTEGER, device_uuid TEXT, operador TEXT,
            foto_placa TEXT, foto_vehiculo TEXT);
        CREATE TABLE confusions (leido TEXT, real TEXT, n INTEGER);
        """
    )
    conn.execute(
        "INSERT INTO devices VALUES ('dev-origen-1', 'and-1', 'Motorola', 'G9', '13', '1.0', 1, 0, 1700000000000, 0, 'Pedro')"
    )
    conn.execute(
        """INSERT INTO sightings (id, placa, leida, match_type, en_lista, motivo, confidence, sharpness,
           frames, source, lat, lng, ts, received_at, device_uuid, operador, foto_placa, foto_vehiculo)
           VALUES (1, 'ABC123', 'ABC123', 'exact', 1, 'Prueba migrada', 0.9, 0.8, 3, 'origen',
           4.6, -74.1, 1700000001000, 1700000001000, 'dev-origen-1', 'Pedro',
           '2026-11-16/1_ABC123_placa.jpg', '2026-11-16/1_ABC123_vehiculo.jpg')"""
    )
    conn.execute("INSERT INTO confusions VALUES ('0', 'O', 3)")
    conn.commit()
    conn.close()

    (carpeta / "datos" / "fotos" / "2026-11-16" / "1_ABC123_placa.jpg").write_bytes(b"foto-placa-falsa")
    (carpeta / "datos" / "fotos" / "2026-11-16" / "1_ABC123_vehiculo.jpg").write_bytes(b"foto-vehiculo-falsa")
    (carpeta / "telegram.json").write_text('{"token": "falso", "chats": []}')
    return carpeta


def test_migracion_completa_y_segunda_corrida_no_duplica(cliente, tmp_path, monkeypatch):
    origen = _crear_origen_falso(tmp_path)
    monkeypatch.setattr(migrar_nexcam, "RUTA_NEXCAM", origen)
    monkeypatch.setattr(migrar_nexcam, "RUTA_DB_ORIGEN", origen / "datos" / "nexcam.db")
    monkeypatch.setattr(migrar_nexcam, "RUTA_FOTOS_ORIGEN", origen / "datos" / "fotos")
    monkeypatch.setattr(migrar_nexcam, "RUTA_TELEGRAM_ORIGEN", origen / "telegram.json")
    monkeypatch.setattr(migrar_nexcam, "RUTA_TELEGRAM_DESTINO", tmp_path / "telegram_destino.json")

    conn_origen = migrar_nexcam._conectar_origen()
    try:
        r1 = migrar_nexcam.migrar_dispositivos(conn_origen, simular=False)
        r2 = migrar_nexcam.migrar_avistamientos(conn_origen, simular=False)
        r3 = migrar_nexcam.migrar_confusiones(conn_origen, simular=False)
    finally:
        conn_origen.close()
    t = migrar_nexcam.migrar_telegram_json(simular=False)

    assert r1 == {"migrados": 1, "ya_existian": 0}
    assert r2["migrados"] == 1 and r2["fotos_copiadas"] == 2
    assert r3 == {"migradas": 1, "ya_existian": 0}
    assert t == "COPIADO"

    assert nexcam_db.obtener_dispositivo("dev-origen-1")["brand"] == "Motorola"
    assert len(nexcam_db.avistamientos_recientes(10)) == 1
    assert nexcam_db.listar_confusiones() == [{"leido": "0", "real": "O", "n": 3}]
    assert (tmp_path / "telegram_destino.json").is_file()
    assert (nexcam_db.NEXCAM_FOTOS_DIR / "2026-11-16" / "1_ABC123_placa.jpg").is_file()

    # Segunda corrida: nada nuevo, nada duplicado.
    conn_origen = migrar_nexcam._conectar_origen()
    try:
        r1b = migrar_nexcam.migrar_dispositivos(conn_origen, simular=False)
        r2b = migrar_nexcam.migrar_avistamientos(conn_origen, simular=False)
        r3b = migrar_nexcam.migrar_confusiones(conn_origen, simular=False)
    finally:
        conn_origen.close()
    tb = migrar_nexcam.migrar_telegram_json(simular=False)

    assert r1b == {"migrados": 0, "ya_existian": 1}
    assert r2b["migrados"] == 0 and r2b["ya_existian"] == 1
    assert r3b == {"migradas": 0, "ya_existian": 1}
    assert tb.startswith("YA_EXISTIA")
    assert len(nexcam_db.avistamientos_recientes(10)) == 1  # sigue siendo 1, no 2


def test_simular_no_cambia_nada(cliente, tmp_path, monkeypatch):
    origen = _crear_origen_falso(tmp_path)
    monkeypatch.setattr(migrar_nexcam, "RUTA_NEXCAM", origen)
    monkeypatch.setattr(migrar_nexcam, "RUTA_DB_ORIGEN", origen / "datos" / "nexcam.db")
    monkeypatch.setattr(migrar_nexcam, "RUTA_FOTOS_ORIGEN", origen / "datos" / "fotos")
    monkeypatch.setattr(migrar_nexcam, "RUTA_TELEGRAM_ORIGEN", origen / "telegram.json")
    monkeypatch.setattr(migrar_nexcam, "RUTA_TELEGRAM_DESTINO", tmp_path / "telegram_destino.json")

    conn_origen = migrar_nexcam._conectar_origen()
    try:
        r1 = migrar_nexcam.migrar_dispositivos(conn_origen, simular=True)
        r2 = migrar_nexcam.migrar_avistamientos(conn_origen, simular=True)
    finally:
        conn_origen.close()
    t = migrar_nexcam.migrar_telegram_json(simular=True)

    assert r1 == {"migrados": 1, "ya_existian": 0}
    assert r2["migrados"] == 1
    assert t == "SE_COPIARIA"
    assert nexcam_db.obtener_dispositivo("dev-origen-1") is None
    assert nexcam_db.avistamientos_recientes(10) == []
    assert not (tmp_path / "telegram_destino.json").exists()
