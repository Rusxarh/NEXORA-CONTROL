"""
migrar_nexcam.py
FASE 4 de la integración NEXCAM: importa, UNA SOLA VEZ (re-ejecutable sin
duplicar), lo que haya quedado en el servidor de prueba (mock-api) hacia
NEXORA-CONTROL:

- Teléfonos autorizados (nexcam_dispositivos).
- Avistamientos con sus fotos (nexcam_avistamientos + datos_nexcam/fotos/).
- Confusiones del OCR ya aprendidas (nexcam_confusiones).
- telegram.json (token, chats vinculados).

Deliberadamente NO migra `plates` (la lista de prueba del mock, que son
solo datos de ejemplo) ni `tokens` (sesiones de prueba, sin sentido fuera
de ese servidor): el inventario real de NEXORA-CONTROL es `vehiculos`.

USO:
    python migrar_nexcam.py              (migra de verdad)
    python migrar_nexcam.py --simular    (solo muestra qué haría, no cambia nada)

Advertencia sobre telegram.json: si el mock-api tiene un token real
configurado, copiarlo hace que la PRÓXIMA vez que arranque NEXORA-CONTROL
el bot quede activo con ese mismo token (puede escribirle a chats reales
ya vinculados). Este script lo copia si se lo pides sin --simular, pero
no reinicia ni toca el servidor -- la activación real ocurre recién en el
siguiente arranque, y es una decisión aparte de "migrar los datos".
"""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import database as db
import nexcam_db

RUTA_NEXCAM = Path(r"C:\Users\Rus\Documents\NEXORA\NEXCAM\mock-api")
RUTA_DB_ORIGEN = RUTA_NEXCAM / "datos" / "nexcam.db"
RUTA_FOTOS_ORIGEN = RUTA_NEXCAM / "datos" / "fotos"
RUTA_TELEGRAM_ORIGEN = RUTA_NEXCAM / "telegram.json"
RUTA_TELEGRAM_DESTINO = Path(__file__).parent / "telegram.json"


def _conectar_origen() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{RUTA_DB_ORIGEN.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def migrar_dispositivos(conn_origen, simular: bool) -> dict:
    resumen = {"migrados": 0, "ya_existian": 0}
    for fila in conn_origen.execute("SELECT * FROM devices"):
        d = dict(fila)
        if nexcam_db.obtener_dispositivo(d["device_uuid"]) is not None:
            resumen["ya_existian"] += 1
            continue
        resumen["migrados"] += 1
        if simular:
            continue
        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO nexcam_dispositivos
                   (device_uuid, android_id, brand, model, os_version, app_version,
                    authorized, revoked, last_seen, pending, user_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d["device_uuid"], d.get("android_id"), d.get("brand"), d.get("model"),
                 d.get("os_version"), d.get("app_version"), d.get("authorized", 0),
                 d.get("revoked", 0),
                 _ms_a_texto(d.get("last_seen")), d.get("pending", 0), d.get("user_name")),
            )
    return resumen


def _ms_a_texto(ms) -> str:
    """El mock guarda fechas en epoch-ms (JS Date.now()); NEXORA las guarda
    como texto local 'AAAA-MM-DD HH:MM:SS'."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def migrar_avistamientos(conn_origen, simular: bool) -> dict:
    resumen = {"migrados": 0, "ya_existian": 0, "fotos_copiadas": 0}
    for fila in conn_origen.execute("SELECT * FROM sightings ORDER BY id ASC"):
        s = dict(fila)
        with db.get_connection() as conn:
            existe = conn.execute(
                "SELECT id FROM nexcam_avistamientos WHERE placa = ? AND device_uuid = ? AND ts = ?",
                (s["placa"], s.get("device_uuid"), s.get("ts")),
            ).fetchone()
        if existe:
            resumen["ya_existian"] += 1
            continue
        resumen["migrados"] += 1
        if simular:
            if s.get("foto_placa") or s.get("foto_vehiculo"):
                resumen["fotos_copiadas"] += sum(1 for f in (s.get("foto_placa"), s.get("foto_vehiculo")) if f)
            continue

        with db.get_connection() as conn:
            conn.execute(
                """INSERT INTO nexcam_avistamientos
                   (placa, leida, match_type, en_lista, motivo, confidence, sharpness, frames,
                    source, lat, lng, ts, recibido_en, device_uuid, operador, foto_placa, foto_vehiculo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (s["placa"], s.get("leida"), s.get("match_type"), s.get("en_lista", 0), s.get("motivo"),
                 s.get("confidence"), s.get("sharpness"), s.get("frames"), s.get("source"),
                 s.get("lat"), s.get("lng"), s.get("ts"), _ms_a_texto(s.get("received_at")) or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 s.get("device_uuid"), s.get("operador"), s.get("foto_placa"), s.get("foto_vehiculo")),
            )
        for relativa in (s.get("foto_placa"), s.get("foto_vehiculo")):
            if relativa and _copiar_foto(relativa):
                resumen["fotos_copiadas"] += 1
    return resumen


def _copiar_foto(relativa: str) -> bool:
    origen = RUTA_FOTOS_ORIGEN / relativa
    if not origen.is_file():
        return False
    destino = nexcam_db.NEXCAM_FOTOS_DIR / relativa
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        return False
    shutil.copy2(origen, destino)
    return True


def migrar_confusiones(conn_origen, simular: bool) -> dict:
    resumen = {"migradas": 0, "ya_existian": 0}
    for fila in conn_origen.execute("SELECT leido, real, n FROM confusions"):
        c = dict(fila)
        with db.get_connection() as conn:
            existe = conn.execute(
                "SELECT 1 FROM nexcam_confusiones WHERE leido = ? AND real = ?", (c["leido"], c["real"])
            ).fetchone()
        if existe:
            resumen["ya_existian"] += 1
            continue
        resumen["migradas"] += 1
        if simular:
            continue
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO nexcam_confusiones (leido, real, n) VALUES (?, ?, ?)",
                (c["leido"], c["real"], c["n"]),
            )
    return resumen


def migrar_telegram_json(simular: bool) -> str:
    if not RUTA_TELEGRAM_ORIGEN.is_file():
        return "NO_ENCONTRADO"
    if RUTA_TELEGRAM_DESTINO.is_file():
        return "YA_EXISTIA (no se sobrescribe)"
    if simular:
        return "SE_COPIARIA"
    shutil.copy2(RUTA_TELEGRAM_ORIGEN, RUTA_TELEGRAM_DESTINO)
    return "COPIADO"


def main():
    parser = argparse.ArgumentParser(description="Migra datos del mock-api de NEXCAM hacia NEXORA-CONTROL.")
    parser.add_argument("--simular", action="store_true", help="Muestra qué haría, sin cambiar nada.")
    args = parser.parse_args()

    if not RUTA_DB_ORIGEN.is_file():
        print(f"No se encontró {RUTA_DB_ORIGEN}. Nada que migrar.")
        return

    db.inicializar_db()
    nexcam_db.inicializar_nexcam_db()

    conn_origen = _conectar_origen()
    try:
        dispositivos = migrar_dispositivos(conn_origen, args.simular)
        avistamientos = migrar_avistamientos(conn_origen, args.simular)
        confusiones = migrar_confusiones(conn_origen, args.simular)
    finally:
        conn_origen.close()
    telegram = migrar_telegram_json(args.simular)

    print("=== Migración NEXCAM -> NEXORA-CONTROL ===")
    print(f"Modo: {'SIMULACIÓN (nada se cambió)' if args.simular else 'REAL'}")
    print(f"Teléfonos:      {dispositivos['migrados']} migrados, {dispositivos['ya_existian']} ya existían.")
    print(f"Avistamientos:  {avistamientos['migrados']} migrados, {avistamientos['ya_existian']} ya existían, {avistamientos['fotos_copiadas']} foto(s).")
    print(f"Confusiones OCR:{confusiones['migradas']} migradas, {confusiones['ya_existian']} ya existían.")
    print(f"telegram.json:  {telegram}")
    if telegram == "SE_COPIARIA" or telegram == "COPIADO":
        print(
            "  Aviso: si ese telegram.json tiene un token real, el bot quedará "
            "ACTIVO en el próximo arranque de NEXORA-CONTROL y podrá escribirle "
            "a los chats ya vinculados ahí. Revisa su contenido antes de reiniciar el servidor."
        )


if __name__ == "__main__":
    main()
