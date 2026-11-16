"""
nexcam_db.py
Capa de datos para la integración de NEXCAM (cámara lectora de placas en
Android) con NEXORA-CONTROL — FASE 1.

Mismo patrón que database.py: tablas nuevas con prefijo `nexcam_`,
migraciones aditivas sobre MasterPlacas.db, nunca sobre `usuarios`/
`vehiculos` directamente. Especificación de referencia (solo lectura, no
se modifica): C:\\...\\NEXORA\\NEXCAM\\mock-api\\server.js + datos.js.

Decisiones de diseño registradas aquí (no silenciosas):
- `vehiculos` no tiene una columna "actualizado_en" genérica. En vez de
  agregar una y tocar los puntos de escritura existentes de vehiculos.py
  (fuera del alcance autorizado de esta fase), el "cambió desde `since`"
  se deriva de columnas/datos que YA existen: fecha_ingreso, fecha_baja y
  el último `logs_auditoria.fecha_hora` de esa placa. Toda futura escritura
  sobre una placa (incluida la que complete marca/línea/color/modelo desde
  la lista del día, FASE 2) debe seguir dejando su propio log de auditoría
  -- igual que ya hace insertar_placa/cambiar_estado_a_baja -- para que
  este cálculo siga siendo correcto sin cambios aquí.
- El Grado Operativo de un Operador también puede hacer que una placa
  entre o salga de su vista SOLO por el paso del calendario, sin que la
  fila cambie en absoluto (camara.calcular_grado_placa es función del
  tiempo). `placas_para_app` lo resuelve detectando cuándo el umbral de
  15/30 días de una placa activa cae dentro de (since, ahora].
"""

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import camara
import database as db

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Solo carros, 3 letras + 3 números exactos (igual que RE_PLACA en
# mock-api/datos.js). La app NO conoce motos ni el formato más amplio que
# acepta el resto de NEXORA (vehiculos.PATRON_PLACA): una placa que no
# cumpla este patrón estricto simplemente no se sincroniza a los teléfonos.
RE_PLACA_NEXCAM = re.compile(r"^[A-Z]{3}[0-9]{3}$")
GLOB_PLACA_NEXCAM = "[A-Z][A-Z][A-Z][0-9][0-9][0-9]"

# Letras que el OCR confunde aunque diga estar seguro (igual que GEMELOS
# en datos.js).
GEMELOS = (("O", "Q", "D"), ("0",))

TOKEN_TTL_HORAS = 12
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Carpeta de fotos, configurable por variable de entorno (igual patrón que
# NEXCAM_DATOS/DIAS_FOTOS en server.js). No se versiona (ver .gitignore).
import os  # noqa: E402  (después de las constantes por legibilidad)

NEXCAM_FOTOS_DIR = Path(
    os.environ.get("NEXCAM_FOTOS_DIR", str(Path(__file__).parent / "datos_nexcam" / "fotos"))
)
DIAS_RETENCION_FOTOS = int(os.environ.get("NEXCAM_DIAS_FOTOS", "90"))

_RE_RUTA_FOTO = re.compile(r"^(\d{4}-\d{2}-\d{2})/([\w.-]+\.jpg)$")


def normalizar_placa(valor: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (valor or "").upper())


# ---------------------------------------------------------------------------
# Errores (con `code`/`status`, el mismo vocabulario que el contrato HTTP)
# ---------------------------------------------------------------------------

class ErrorNexcamAPI(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

SCHEMA_NEXCAM_SQL = """
CREATE TABLE IF NOT EXISTS nexcam_dispositivos (
    device_uuid   TEXT PRIMARY KEY,
    android_id    TEXT,
    brand         TEXT,
    model         TEXT,
    os_version    TEXT,
    app_version   TEXT,
    authorized    INTEGER NOT NULL DEFAULT 0,
    revoked       INTEGER NOT NULL DEFAULT 0,
    last_seen     TEXT,
    pending       INTEGER NOT NULL DEFAULT 0,
    user_name     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS nexcam_tokens (
    token_hash    TEXT PRIMARY KEY,
    usuario_id    INTEGER NOT NULL,
    rol           TEXT NOT NULL,
    nombre        TEXT NOT NULL,
    device_uuid   TEXT,
    creado_en     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expira_en     TEXT NOT NULL,
    ended_code    TEXT,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE IF NOT EXISTS nexcam_avistamientos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    placa         TEXT NOT NULL,
    leida         TEXT,
    match_type    TEXT,
    en_lista      INTEGER NOT NULL DEFAULT 0,
    motivo        TEXT,
    confidence    REAL,
    sharpness     REAL,
    frames        INTEGER,
    source        TEXT,
    lat           REAL,
    lng           REAL,
    ts            INTEGER,
    recibido_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    device_uuid   TEXT,
    usuario_id    INTEGER,
    operador      TEXT,
    foto_placa    TEXT,
    foto_vehiculo TEXT,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);
CREATE INDEX IF NOT EXISTS idx_nexcam_avist_placa ON nexcam_avistamientos (placa);
CREATE INDEX IF NOT EXISTS idx_nexcam_avist_leida ON nexcam_avistamientos (leida);
CREATE INDEX IF NOT EXISTS idx_nexcam_avist_ts    ON nexcam_avistamientos (ts);

CREATE TABLE IF NOT EXISTS nexcam_confusiones (
    leido TEXT NOT NULL,
    real  TEXT NOT NULL,
    n     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (leido, real)
);

CREATE TABLE IF NOT EXISTS nexcam_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    leida       TEXT,
    real        TEXT,
    signo       INTEGER,
    ts          INTEGER,
    operador    TEXT,
    device_uuid TEXT,
    creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def inicializar_nexcam_db():
    """Crea las tablas nexcam_ si no existen. Seguro llamarla varias veces."""
    with db.get_connection() as conn:
        conn.executescript(SCHEMA_NEXCAM_SQL)


# ---------------------------------------------------------------------------
# Dispositivos
# ---------------------------------------------------------------------------

def _fila_dispositivo(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    ultimo = d.get("last_seen")
    d["online"] = bool(ultimo and (datetime.now() - datetime.strptime(ultimo, FORMATO_FECHA)) < timedelta(seconds=90))
    return d


def obtener_dispositivo(device_uuid: str) -> Optional[dict]:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM nexcam_dispositivos WHERE device_uuid = ?", (device_uuid,)
        ).fetchone()
        return _fila_dispositivo(row)


def listar_dispositivos() -> list:
    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM nexcam_dispositivos ORDER BY created_at DESC").fetchall()
        return [_fila_dispositivo(r) for r in rows]


def registrar_dispositivo(device_uuid: str, android_id=None, brand=None, model=None,
                           os_version=None, app_version=None) -> tuple:
    """
    Da de alta (o re-registra) un teléfono. Devuelve (dispositivo, es_nuevo).
    Nunca autoriza por sí mismo: authorized/revoked quedan intactos si el
    dispositivo ya existía (igual que devices.upsert en datos.js).
    """
    ahora = datetime.now().strftime(FORMATO_FECHA)
    with db.get_connection() as conn:
        existente = conn.execute(
            "SELECT device_uuid FROM nexcam_dispositivos WHERE device_uuid = ?", (device_uuid,)
        ).fetchone()
        es_nuevo = existente is None
        if es_nuevo:
            conn.execute(
                """INSERT INTO nexcam_dispositivos
                   (device_uuid, android_id, brand, model, os_version, app_version, last_seen, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (device_uuid, android_id, brand, model, os_version, app_version, ahora, ahora),
            )
        else:
            conn.execute(
                """UPDATE nexcam_dispositivos SET android_id = ?, brand = ?, model = ?,
                   os_version = ?, app_version = ?, last_seen = ? WHERE device_uuid = ?""",
                (android_id, brand, model, os_version, app_version, ahora, device_uuid),
            )
    return obtener_dispositivo(device_uuid), es_nuevo


def autorizar_dispositivo(device_uuid: str) -> Optional[dict]:
    """Autoriza un teléfono (acción de Admin; la ruta HTTP/UI es FASE 2)."""
    with db.get_connection() as conn:
        cur = conn.execute(
            "UPDATE nexcam_dispositivos SET authorized = 1, revoked = 0 WHERE device_uuid = ?",
            (device_uuid,),
        )
        if cur.rowcount == 0:
            return None
    return obtener_dispositivo(device_uuid)


def revocar_dispositivo(device_uuid: str) -> Optional[dict]:
    """
    Revoca un teléfono y cierra TODAS sus sesiones activas (DEVICE_REVOKED):
    la próxima llamada autenticada de ese teléfono recibe ese código y la
    app debe borrar sus datos locales (contrato HTTP, no se hace aquí).
    """
    with db.get_connection() as conn:
        cur = conn.execute(
            "UPDATE nexcam_dispositivos SET authorized = 0, revoked = 1 WHERE device_uuid = ?",
            (device_uuid,),
        )
        if cur.rowcount == 0:
            return None
        conn.execute(
            "UPDATE nexcam_tokens SET ended_code = 'DEVICE_REVOKED' WHERE device_uuid = ? AND ended_code IS NULL",
            (device_uuid,),
        )
    return obtener_dispositivo(device_uuid)


def marcar_pendientes(device_uuid: str, pending: int):
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE nexcam_dispositivos SET pending = ?, last_seen = ? WHERE device_uuid = ?",
            (pending, datetime.now().strftime(FORMATO_FECHA), device_uuid),
        )


# ---------------------------------------------------------------------------
# Autenticación: login / tokens
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def iniciar_sesion(username: str, password: str, device_uuid: Optional[str]) -> dict:
    """
    Login de la app NEXCAM contra la tabla `usuarios` real (independiente
    de la sesión-cookie del dashboard web: aquí siempre se emite un Bearer
    token nuevo, ligado al teléfono).

    Decisión explícita (distinta del mock-api, donde Admin se salta el
    chequeo de dispositivo): aquí TODO rol -- Admin incluido -- requiere un
    `device_uuid` ya registrado y autorizado. Es un cambio deliberado
    pedido para esta integración ("Admin también puede entrar a la app si
    el teléfono está autorizado"), documentado para poder revisarlo.
    """
    import usuarios  # import perezoso: evita el ciclo usuarios -> (nada) -> nexcam_db

    usuario = db.obtener_usuario_por_username(username)
    if usuario is None or usuario["estado"] != "activo" or not usuarios.verificar_password(password, usuario["password_hash"]):
        raise ErrorNexcamAPI("BAD_CREDENTIALS", "Usuario o contraseña incorrectos.", 401)

    if not device_uuid:
        raise ErrorNexcamAPI("DEVICE_UNKNOWN", "Dispositivo no registrado.", 403)
    dispositivo = obtener_dispositivo(device_uuid)
    if dispositivo is None:
        raise ErrorNexcamAPI("DEVICE_UNKNOWN", "Dispositivo no registrado.", 403)
    if not dispositivo["authorized"]:
        raise ErrorNexcamAPI("DEVICE_NOT_AUTHORIZED", "Dispositivo pendiente de autorización.", 403)

    if usuario["rol"] == "Operador":
        usuario = camara.sincronizar_estado_camara(usuario["id"]) or usuario
        if (usuario.get("estado_camara") or "ACTIVA") == "SUSPENDIDA":
            raise ErrorNexcamAPI("CAMERA_SUSPENDED", "CAMARA está suspendida para este usuario.", 403)

    # Una sola sesión activa por teléfono (igual que tokens.endAllOf en el mock).
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE nexcam_tokens SET ended_code = 'SESSION_REPLACED' WHERE device_uuid = ? AND ended_code IS NULL",
            (device_uuid,),
        )
        marcar_pendientes.__wrapped__ = None  # no-op: evita un lint de "unused" en algunos linters

    token = secrets.token_hex(32)
    expira_en = (datetime.now() + timedelta(hours=TOKEN_TTL_HORAS)).strftime(FORMATO_FECHA)
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO nexcam_tokens (token_hash, usuario_id, rol, nombre, device_uuid, expira_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_hash_token(token), usuario["id"], usuario["rol"], usuario["nombre"], device_uuid, expira_en),
        )
        conn.execute(
            "UPDATE nexcam_dispositivos SET user_name = ? WHERE device_uuid = ?",
            (usuario["nombre"], device_uuid),
        )

    return {
        "token": token,
        "expires_at": expira_en,
        "user": {"id": usuario["id"], "name": usuario["nombre"], "role": usuario["rol"]},
    }


def autenticar(token: Optional[str], device_uuid: Optional[str]) -> dict:
    """
    Valida un Bearer token. Devuelve {usuario_id, rol, nombre, device_uuid,
    token_hash}. Lanza ErrorNexcamAPI con el `code` exacto del contrato.
    """
    if not token:
        raise ErrorNexcamAPI("UNAUTHORIZED", "No autenticado.", 401)
    th = _hash_token(token)
    with db.get_connection() as conn:
        fila = conn.execute("SELECT * FROM nexcam_tokens WHERE token_hash = ?", (th,)).fetchone()
    if fila is None:
        raise ErrorNexcamAPI("UNAUTHORIZED", "No autenticado.", 401)
    sesion = dict(fila)

    if sesion["ended_code"]:
        _borrar_token(th)
        mensaje = "Dispositivo revocado." if sesion["ended_code"] == "DEVICE_REVOKED" else "La sesión fue cerrada."
        raise ErrorNexcamAPI(sesion["ended_code"], mensaje, 401)

    if datetime.now() > datetime.strptime(sesion["expira_en"], FORMATO_FECHA):
        _borrar_token(th)
        raise ErrorNexcamAPI("TOKEN_EXPIRED", "La sesión venció.", 401)

    if sesion["device_uuid"] and sesion["device_uuid"] != (device_uuid or sesion["device_uuid"]):
        pass  # ver chequeo real abajo (se deja explícito para no comparar contra sí mismo)

    if device_uuid is not None and sesion["device_uuid"] and device_uuid != sesion["device_uuid"]:
        raise ErrorNexcamAPI("DEVICE_MISMATCH", "El token pertenece a otro dispositivo.", 401)

    if sesion["device_uuid"]:
        dispositivo = obtener_dispositivo(sesion["device_uuid"])
        if dispositivo is None or not dispositivo["authorized"]:
            _borrar_token(th)
            raise ErrorNexcamAPI("DEVICE_REVOKED", "Dispositivo no autorizado.", 401)
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE nexcam_dispositivos SET last_seen = ? WHERE device_uuid = ?",
                (datetime.now().strftime(FORMATO_FECHA), sesion["device_uuid"]),
            )

    return {
        "usuario_id": sesion["usuario_id"],
        "rol": sesion["rol"],
        "nombre": sesion["nombre"],
        "device_uuid": sesion["device_uuid"],
        "token_hash": th,
    }


def _borrar_token(token_hash: str):
    with db.get_connection() as conn:
        conn.execute("DELETE FROM nexcam_tokens WHERE token_hash = ?", (token_hash,))


def cerrar_sesion(token_hash: str):
    _borrar_token(token_hash)


def purgar_tokens_vencidos():
    ahora = datetime.now().strftime(FORMATO_FECHA)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM nexcam_tokens WHERE expira_en < ?", (ahora,))


# ---------------------------------------------------------------------------
# Placas para la app (sync completa e incremental, filtrada por Grado Operativo)
# ---------------------------------------------------------------------------

def _ms_de_texto_fecha(valor: Optional[str]) -> Optional[int]:
    if not valor:
        return None
    return int(datetime.strptime(str(valor)[:19], FORMATO_FECHA).timestamp() * 1000)


def _vehiculo_para_app(row) -> dict:
    """
    `updated_at` es una APROXIMACIÓN (ver docstring del módulo): el máximo
    entre fecha_ingreso y fecha_baja. No reacciona a una edición posterior
    de marca/línea/color/modelo que no reactive/reingrese la placa -- esa
    cobertura queda pendiente para cuando la FASE 2 conecte la carga masiva
    con un log de auditoría propio (el filtro `changedSince` de este mismo
    módulo ya sí lo detecta vía logs_auditoria; solo este campo informativo
    no, por fidelidad al contrato JSON que lo exige).
    """
    actualizado = max(
        (t for t in (_ms_de_texto_fecha(row["fecha_ingreso"]), _ms_de_texto_fecha(row["fecha_baja"])) if t),
        default=None,
    )
    return {
        "placa": row["placa"],
        "motivo": row["observaciones"] or "",
        "activo": row["estado"] == "activo",
        "updated_at": actualizado,
        "marca": row["marca"] if "marca" in row.keys() else None,
        "linea": row["linea"] if "linea" in row.keys() else None,
        "color": row["color"] if "color" in row.keys() else None,
        "modelo": row["modelo"] if "modelo" in row.keys() else None,
    }


def _placas_candidatas(conn, solo_activas: bool):
    query = f"SELECT * FROM vehiculos WHERE tipo_vehiculo = 'carro' AND placa GLOB '{GLOB_PLACA_NEXCAM}'"
    if solo_activas:
        query += " AND estado = 'activo'"
    return conn.execute(query).fetchall()


def _visible_para(usuario: Optional[dict], fecha_ingreso) -> bool:
    if usuario is None or usuario["rol"] == "Admin":
        return True
    return camara.operador_puede_trabajar_placa(usuario.get("grado_operativo"), fecha_ingreso)


def placas_activas_para_app(usuario: Optional[dict]) -> list:
    """Primera sincronización (`since` ausente/0): solo activas, filtradas por grado."""
    with db.get_connection() as conn:
        filas = _placas_candidatas(conn, solo_activas=True)
    return [_vehiculo_para_app(f) for f in filas if _visible_para(usuario, f["fecha_ingreso"])]


def _umbral_cruzado_en_ventana(fecha_ingreso_str: str, desde: datetime, hasta: datetime) -> bool:
    """True si el Grado (1->2 a los 15 días, 2->3 a los 30) de esta placa
    cambió en algún instante dentro de (desde, hasta]."""
    ingreso = datetime.strptime(fecha_ingreso_str[:19], FORMATO_FECHA)
    for dias in (15, 30):
        umbral = ingreso + timedelta(days=dias)
        if desde < umbral <= hasta:
            return True
    return False


def placas_cambiadas_desde(since_ms: int, usuario: Optional[dict]) -> list:
    """
    Sincronización incremental: placas (activas o recién dadas de baja) cuya
    fila cambió desde `since_ms`, MÁS las que entraron o salieron del filtro
    de Grado Operativo del Operador solo por el paso del calendario (ver
    docstring del módulo). Admin: sin filtro de grado.
    """
    desde_dt = datetime.fromtimestamp(since_ms / 1000)
    ahora_dt = datetime.now()
    desde_str = desde_dt.strftime(FORMATO_FECHA)

    with db.get_connection() as conn:
        filas_cambiadas = conn.execute(
            f"""SELECT * FROM vehiculos
                WHERE tipo_vehiculo = 'carro' AND placa GLOB '{GLOB_PLACA_NEXCAM}'
                AND (
                    fecha_ingreso > ? OR fecha_baja > ? OR EXISTS (
                        SELECT 1 FROM logs_auditoria l
                        WHERE l.placa_afectada = vehiculos.placa AND l.fecha_hora > ?
                    )
                )""",
            (desde_str, desde_str, desde_str),
        ).fetchall()
        # Para el cruce de umbral de grado hace falta revisar TODAS las activas,
        # no solo las que ya cambiaron.
        filas_activas = _placas_candidatas(conn, solo_activas=True) if usuario and usuario["rol"] != "Admin" else []

    resultado = {f["placa"]: f for f in filas_cambiadas}

    if usuario and usuario["rol"] != "Admin":
        for f in filas_activas:
            if f["placa"] in resultado:
                continue
            if _umbral_cruzado_en_ventana(f["fecha_ingreso"], desde_dt, ahora_dt):
                resultado[f["placa"]] = f

    return [
        _vehiculo_para_app(f) for f in resultado.values()
        if _visible_para(usuario, f["fecha_ingreso"]) or f["estado"] != "activo"
    ]


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------

def _fecha_col(ts_ms: Optional[int]) -> str:
    momento = datetime.fromtimestamp((ts_ms or int(datetime.now().timestamp() * 1000)) / 1000)
    return momento.strftime("%Y-%m-%d")


def guardar_foto(b64: Optional[str], ts_ms: Optional[int], nombre: str) -> Optional[str]:
    """Guarda una foto en base64 en disco y devuelve su ruta relativa
    (AAAA-MM-DD/nombre.jpg), o None si no había foto. Igual límite de
    tamaño (6 MB en base64) que guardarFoto en datos.js."""
    if not isinstance(b64, str) or not b64 or len(b64) > 6_000_000:
        return None
    dia = _fecha_col(ts_ms)
    carpeta = NEXCAM_FOTOS_DIR / dia
    carpeta.mkdir(parents=True, exist_ok=True)
    relativa = f"{dia}/{nombre}.jpg"
    (carpeta / f"{nombre}.jpg").write_bytes(base64.b64decode(b64))
    return relativa


def ruta_foto_segura(url: str) -> Optional[Path]:
    """Valida `url` contra path traversal (igual que rutaFoto en datos.js):
    solo AAAA-MM-DD/archivo.jpg, nunca '..' ni rutas absolutas."""
    m = _RE_RUTA_FOTO.match((url or "").lstrip("/").removeprefix("fotos/"))
    if not m:
        return None
    ruta = NEXCAM_FOTOS_DIR / m.group(1) / m.group(2)
    return ruta if ruta.is_file() else None


def podar_fotos(dias: int = DIAS_RETENCION_FOTOS) -> int:
    """Borra carpetas de fotos más viejas que `dias` días. Los datos del
    avistamiento (fila en nexcam_avistamientos) se conservan siempre."""
    import shutil

    if not NEXCAM_FOTOS_DIR.is_dir():
        return 0
    limite = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    borradas = 0
    for carpeta in NEXCAM_FOTOS_DIR.iterdir():
        if carpeta.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", carpeta.name) and carpeta.name < limite:
            shutil.rmtree(carpeta, ignore_errors=True)
            borradas += 1
    return borradas


# ---------------------------------------------------------------------------
# Avistamientos
# ---------------------------------------------------------------------------

def registrar_avistamiento(sesion: dict, placa_raw: str, leida_raw: Optional[str], match_type, confidence,
                            sharpness, frames, source, lat, lng, ts_ms, snapshot_b64, vehicle_b64) -> dict:
    placa = normalizar_placa(placa_raw)
    if not RE_PLACA_NEXCAM.match(placa):
        raise ErrorNexcamAPI("INVALID_PLATE", "Placa inválida.", 422)

    vehiculo = db.buscar_placa(placa)
    en_lista = bool(vehiculo and vehiculo["estado"] == "activo")
    motivo = vehiculo["observaciones"] if en_lista else None
    ts = int(ts_ms) if ts_ms else int(datetime.now().timestamp() * 1000)

    with db.get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO nexcam_avistamientos
               (placa, leida, match_type, en_lista, motivo, confidence, sharpness, frames, source,
                lat, lng, ts, device_uuid, usuario_id, operador)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (placa, normalizar_placa(leida_raw) or placa, match_type, int(en_lista), motivo, confidence,
             sharpness, frames, source, lat, lng, ts, sesion["device_uuid"], sesion["usuario_id"], sesion["nombre"]),
        )
        avistamiento_id = cur.lastrowid

    foto_placa = guardar_foto(snapshot_b64, ts, f"{avistamiento_id}_{placa}_placa")
    foto_vehiculo = guardar_foto(vehicle_b64, ts, f"{avistamiento_id}_{placa}_vehiculo")
    if foto_placa or foto_vehiculo:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE nexcam_avistamientos SET foto_placa = ?, foto_vehiculo = ? WHERE id = ?",
                (foto_placa, foto_vehiculo, avistamiento_id),
            )

    return obtener_avistamiento(avistamiento_id)


def _fila_avistamiento(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    d["snapshot"] = f"/fotos/{d['foto_placa']}" if d.get("foto_placa") else None
    d["vehiculo"] = f"/fotos/{d['foto_vehiculo']}" if d.get("foto_vehiculo") else None
    return d


def obtener_avistamiento(avistamiento_id: int) -> Optional[dict]:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM nexcam_avistamientos WHERE id = ?", (avistamiento_id,)).fetchone()
        return _fila_avistamiento(row)


def avistamientos_recientes(n: int = 200) -> list:
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM nexcam_avistamientos ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [_fila_avistamiento(r) for r in rows]


# ---------------------------------------------------------------------------
# Confusiones del OCR / feedback
# ---------------------------------------------------------------------------

def _confusiones_aprendidas() -> list:
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT leido, real, n FROM nexcam_confusiones WHERE n >= 2"
        ).fetchall()]


def listar_confusiones() -> list:
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT leido, real, n FROM nexcam_confusiones").fetchall()]


def _ajustar_confusion(conn, leido: str, real: str, delta: int):
    conn.execute(
        """INSERT INTO nexcam_confusiones (leido, real, n) VALUES (?, ?, MAX(0, ?))
           ON CONFLICT(leido, real) DO UPDATE SET n = MAX(0, nexcam_confusiones.n + ?)""",
        (leido, real, delta, delta),
    )


def aprender_confusion(conn, leida: str, real: str, signo) -> bool:
    """Solo se aprende de errores de UN carácter (igual que aprender() en server.js)."""
    a, b = normalizar_placa(leida), normalizar_placa(real)
    if len(a) != len(b):
        return False
    difs = [(x, y) for x, y in zip(a, b) if x != y]
    if len(difs) != 1:
        return False
    _ajustar_confusion(conn, difs[0][0], difs[0][1], -1 if signo == -1 else 1)
    return True


def registrar_feedback_ocr(items: list, operador: str, device_uuid: Optional[str]) -> dict:
    aprendidas = 0
    with db.get_connection() as conn:
        for item in items[:200]:
            leida, real, signo = item.get("leida"), item.get("real"), item.get("signo")
            if aprender_confusion(conn, leida, real, signo):
                aprendidas += 1
                conn.execute(
                    """INSERT INTO nexcam_feedback (leida, real, signo, ts, operador, device_uuid)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (normalizar_placa(leida), normalizar_placa(real), -1 if signo == -1 else 1,
                     item.get("ts") or int(datetime.now().timestamp() * 1000), operador, device_uuid),
                )
    return {"aprendidas": aprendidas, "confusions": listar_confusiones()}


# ---------------------------------------------------------------------------
# Retro-cruce: "¿ya se vio esta placa?"
# ---------------------------------------------------------------------------

def _variantes(placa: str) -> set:
    extra = _confusiones_aprendidas()
    variantes = set()
    for i, c in enumerate(placa):
        alternativas = set()
        for grupo in GEMELOS:
            if c in grupo:
                alternativas.update(x for x in grupo if x != c)
        for fila in extra:
            if fila["real"] == c:
                alternativas.add(fila["leido"])
        for alt in alternativas:
            variante = placa[:i] + alt + placa[i + 1:]
            if RE_PLACA_NEXCAM.match(variante):
                variantes.add(variante)
    return variantes


def vistas_de(placas: list) -> dict:
    """
    Dónde se ha visto cada placa (más reciente primero). 'exacta' si alguna
    lectura coincide tal cual; 'probable' si solo coinciden variantes
    gemelas/aprendidas. Igual contrato que vistasDe en datos.js.
    """
    resultado = {}
    with db.get_connection() as conn:
        for cruda in placas:
            placa = normalizar_placa(cruda)
            if not RE_PLACA_NEXCAM.match(placa):
                continue
            lecturas = [placa, *sorted(_variantes(placa))]
            marcas = ",".join("?" for _ in lecturas)
            filas = conn.execute(
                f"""SELECT * FROM nexcam_avistamientos
                    WHERE placa IN ({marcas}) OR leida IN ({marcas})
                    ORDER BY ts DESC LIMIT 200""",
                (*lecturas, *lecturas),
            ).fetchall()
            if not filas:
                continue
            vistas = []
            for f in filas:
                v = _fila_avistamiento(f)
                v["coincidencia"] = "exacta" if f["placa"] == placa or f["leida"] == placa else "probable"
                vistas.append(v)
            resultado[placa] = {
                "placa": placa,
                "tipo": "exacta" if any(v["coincidencia"] == "exacta" for v in vistas) else "probable",
                "vistas": vistas,
            }
    return resultado
