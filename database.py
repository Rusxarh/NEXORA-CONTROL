"""
database.py
Módulo de acceso a datos para MasterPlacas.db

Responsabilidad única: inicializar el esquema y exponer funciones CRUD
básicas sobre las tablas vehiculos, usuarios y logs_auditoria.

No contiene lógica de scraping ni de consulta a portales externos.
Toda la información de vehículos se ingresa manualmente por usuarios
autorizados (Admin/Operador) a través del bot/dashboard.
"""

import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "MasterPlacas.db"

# Valores permitidos (se validan en Python porque SQLite no tiene ENUM nativo)
TIPOS_VEHICULO = ("carro", "moto")
ESTADOS_VEHICULO = ("activo", "baja")
ROLES_USUARIO = ("Admin", "Operador")
ESTADOS_USUARIO = ("activo", "inactivo")

# Etapa 4.6: el Grado Operativo pertenece al Operador (nunca a la placa) y
# el estado del servicio CAMARA es independiente del estado del usuario
# (ver camara.py para la lógica de cálculo de ambos).
GRADOS_OPERATIVOS = ("Grado 1", "Grado 2", "Grado 3")
ESTADOS_CAMARA = ("ACTIVA", "EN_ALERTA", "SUSPENDIDA")

# Tipos de evento que camara.py/usuarios.py encolan en `eventos_notificacion`
# para que el futuro bot de Telegram los consuma. No se implementa ningún
# consumidor todavía (Etapa 4.6 §27): esto es solo la cola/registro.
TIPOS_EVENTO_NOTIFICACION = (
    "USER_DEACTIVATED",
    "USER_REACTIVATED",
    "CAMERA_WARNING",
    "CAMERA_SUSPENDED",
    "CAMERA_REACTIVATED",
    "CAMERA_ACTIVITY_REGISTERED",
)

# Etapa 5: Gestión Masiva. Ver gestion_masiva.py para la lógica de
# períodos, estados de información y cola de trabajo; estas tuplas solo
# fijan los valores permitidos (mismo patrón que el resto del archivo).
ESTADOS_INFORMACION_PLACA = ("ACTUALIZADA", "PENDIENTE", "SIN_DATOS")
ESTADOS_ESTUDIO = ("EN_PROGRESO", "PAUSADO", "CANCELADO", "COMPLETADO")
ESTADOS_ITEM_COLA = ("PENDIENTE", "PROCESANDO", "COMPLETADO", "ERROR")

# FASE 6/7 (NEXCAM): cola de consultas RGM/Judicial reales a través del
# Connector. Ver rgm_cola.py para la lógica (reserva de a una, sin
# duplicados, reintentos, encadenar Judicial tras RGM). Estas tuplas solo
# fijan los valores permitidos, mismo patrón que el resto de este archivo.
# "alcance" (antes "tipo"): qué parte del flujo real se pide al Connector
# -- RGM sola, Judicial sola (con el nombre de un snapshot RGM reciente) o
# COMPLETO (RGM + Judicial encadenado, comportamiento histórico único).
ALCANCES_CONSULTA_RGM = ("RGM", "JUDICIAL", "COMPLETO")
ORIGENES_CONSULTA_RGM = ("camara", "telegram", "panel")
ESTADOS_ITEM_RGM = ("PENDIENTE", "EN_CURSO", "OK", "ERROR")
MAX_INTENTOS_RGM = 2
VIGENCIA_SNAPSHOT_RGM_HORAS = 24
# Si la página Consulta se recarga a mitad de un ítem EN_CURSO, o el hilo
# de Judicial directo se cae a mitad de una consulta, ese ítem queda
# huérfano (nadie va a completarlo ni a reportar error): se libera solo
# pasado este tiempo (Prompt 8d punto 5: "sin latido ni resultado por más
# de 3 min" -- ver rgm_cola._hilo_vigilante_colgados, que corre solo,
# sin depender de que alguien vuelva a pedir trabajo de la cola).
MINUTOS_ABANDONO_ITEM_RGM = 3

# Prompt 7 bloque A: prioridad explícita de rgm_cola (antes el orden era
# un FIFO puro por `id`, sin distinguir quién la pidió -- un ítem de
# cámara/estudio (automático) encolado antes dejaba esperando detrás a un
# pedido de Telegram/panel). Número más bajo = se atiende primero.
PRIORIDAD_TELEGRAM = 0
PRIORIDAD_PANEL = 1
PRIORIDAD_AUTOMATICA = 2  # camara/estudio
PRIORIDAD_POR_ORIGEN = {"telegram": PRIORIDAD_TELEGRAM, "panel": PRIORIDAD_PANEL,
                         "camara": PRIORIDAD_AUTOMATICA, "estudio": PRIORIDAD_AUTOMATICA}
# A los 3 min de pedirla sin resultado, se avisa una vez "sigue en cola".
MINUTOS_AVISO_ESPERA_RGM = 3

# Un snapshot se considera "vigente" (ACTUALIZADA) si tiene menos de este
# número de días; más viejo que esto es PENDIENTE de actualizar. La Etapa
# 4.6 no definió ningún umbral de vigencia, así que se documenta aquí como
# la interpretación adoptada: reutiliza el mismo umbral de 30 días que ya
# se usa para el Grado Operativo (Etapa 4.6 §12), por consistencia y para
# no inventar un número arbitrario nuevo. Es un valor puro de lectura (no
# se persiste), así que cambiarlo no requiere ninguna migración.
VIGENCIA_INFORMACION_DIAS = 30

# Duración de una sesión de login (horas) antes de requerir volver a iniciar sesión.
DURACION_SESION_HORAS = 12


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

# Prompt 8b punto 3: "database is locked" venía del hilo de Judicial directo
# (rgm_cola._hilo_judicial_directo) escribiendo cada 0.5-3 s mientras el
# servidor web también escribe -- SQLite, sin WAL ni busy_timeout, falla
# al instante con "locked" en vez de esperar a que el otro termine (las
# escrituras de este proyecto son cortas, casi nunca más de unos ms).
# BUSY_TIMEOUT_MS: cuánto espera SQLite antes de levantar "locked" de
# verdad. REINTENTOS_SI_BLOQUEADA/ESPERA_REINTENTO_SEGUNDOS: red de
# seguridad por si, aun así, sigue bloqueada (ver get_connection).
BUSY_TIMEOUT_MS = 10_000
REINTENTOS_SI_BLOQUEADA = 3
ESPERA_REINTENTO_SEGUNDOS = 0.3


@contextmanager
def get_connection():
    """
    Context manager para obtener una conexión SQLite con:
    - row_factory como sqlite3.Row (acceso a columnas por nombre)
    - claves foráneas habilitadas (SQLite las trae desactivadas por defecto)
    - busy_timeout de BUSY_TIMEOUT_MS (Prompt 8b punto 3): si otra conexión
      tiene la base ocupada escribiendo, espera en vez de fallar al instante
    - commit automático al salir sin errores, rollback si hay excepción;
      si el commit sigue topando con "database is locked" después del
      busy_timeout (muy raro: significaría una escritura larga de verdad),
      reintenta unas pocas veces con una espera corta antes de rendirse
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    try:
        yield conn
        for intento in range(REINTENTOS_SI_BLOQUEADA + 1):
            try:
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or intento == REINTENTOS_SI_BLOQUEADA:
                    raise
                time.sleep(ESPERA_REINTENTO_SEGUNDOS * (intento + 1))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    nombre      TEXT NOT NULL,
    rol         TEXT NOT NULL CHECK (rol IN ('Admin', 'Operador')),
    estado      TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'inactivo')),
    fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS vehiculos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    placa          TEXT NOT NULL UNIQUE,
    tipo_vehiculo  TEXT NOT NULL CHECK (tipo_vehiculo IN ('carro', 'moto')),
    estado         TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'baja')),
    fecha_ingreso  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_baja     TEXT,
    observaciones  TEXT
);

CREATE TABLE IF NOT EXISTS logs_auditoria (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id     INTEGER NOT NULL,
    accion         TEXT NOT NULL,
    placa_afectada TEXT,
    fecha_hora     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

CREATE TABLE IF NOT EXISTS sesiones (
    token       TEXT PRIMARY KEY,
    usuario_id  INTEGER NOT NULL,
    creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expira_en   TEXT NOT NULL,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

-- Etapa 4.6: cola de eventos para el futuro bot de Telegram (no hay
-- ningún consumidor todavía). Cada fila es un hecho ya ocurrido
-- (desactivación, alerta/suspensión de CAMARA, etc.) pendiente de
-- notificar; `entregado` lo marcará el consumidor cuando exista.
CREATE TABLE IF NOT EXISTS eventos_notificacion (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL,
    tipo_evento TEXT NOT NULL,
    datos       TEXT,
    creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    entregado   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);

-- Etapa 5: snapshots de información externa por placa. Cada consulta
-- (futura: RGM/Judicial; hoy: solo lo que un Admin registre manualmente,
-- ya que ninguna fuente externa real está implementada todavía) es un
-- INSERT nuevo, nunca un UPDATE: el historial anterior nunca se destruye
-- (Etapa 5 §30). El estado "vigente" de una placa se calcula a partir del
-- snapshot más reciente (ver gestion_masiva.calcular_estado_informacion),
-- nunca se guarda como columna aparte para no desincronizarse.
-- Prompt 8b punto 2: SIN FOREIGN KEY a vehiculos.placa a propósito -- una
-- consulta RGM/Judicial a pedido (Telegram /buscar, Consulta, estudio) de
-- una placa que todavía no está en la lista debe poder guardar su
-- snapshot igual (ver _migrar_snapshots_sin_fk para bases ya existentes).
CREATE TABLE IF NOT EXISTS snapshots_placa (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    placa       TEXT NOT NULL,
    fuente      TEXT NOT NULL,
    datos       TEXT,
    creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Etapa 5: Job Manager de estudios masivos. Un estudio agrupa una cola de
-- placas a estudiar (cola_estudio) armada a partir de un período+filtro
-- concretos. NO hay ningún proceso en segundo plano: el progreso avanza
-- por lotes acotados vía peticiones HTTP explícitas (ver
-- gestion_masiva.procesar_siguiente_lote), nunca un loop gigante dentro
-- de una sola petición.
CREATE TABLE IF NOT EXISTS estudios_masivos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id       INTEGER NOT NULL,
    periodo        TEXT NOT NULL,
    filtro_estado  TEXT,
    busqueda       TEXT,
    total_placas   INTEGER NOT NULL DEFAULT 0,
    procesadas     INTEGER NOT NULL DEFAULT 0,
    con_error      INTEGER NOT NULL DEFAULT 0,
    estado         TEXT NOT NULL DEFAULT 'EN_PROGRESO',
    creado_en      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (admin_id) REFERENCES usuarios (id)
);

CREATE TABLE IF NOT EXISTS cola_estudio (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    estudio_id     INTEGER NOT NULL,
    placa          TEXT NOT NULL,
    prioridad      INTEGER NOT NULL DEFAULT 0,
    orden          INTEGER NOT NULL,
    estado         TEXT NOT NULL DEFAULT 'PENDIENTE',
    intentos       INTEGER NOT NULL DEFAULT 0,
    procesado_en   TEXT,
    error_detalle  TEXT,
    FOREIGN KEY (estudio_id) REFERENCES estudios_masivos (id)
);

-- FASE 6/7 (NEXCAM): cola de consultas RGM/Judicial reales a través del
-- Connector (ver rgm_cola.py). avistamiento_id es NULL cuando el origen
-- es 'telegram'/'panel' (una placa suelta, no ligada a una lectura de
-- cámara). nombre_judicial solo aplica a alcance='JUDICIAL' (lo que se
-- le manda al Connector en vez de la placa). encadenar_judicial: cuando
-- un alcance='RGM' terminó con este flag en 1, al completarse se encola
-- automáticamente un 'JUDICIAL' con el nombre recién obtenido (pedido
-- "Judicial sola: si no hay snapshot RGM reciente, hace RGM primero").
CREATE TABLE IF NOT EXISTS rgm_cola (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    placa          TEXT NOT NULL,
    alcance        TEXT NOT NULL CHECK (alcance IN ('RGM', 'JUDICIAL', 'COMPLETO')),
    origen         TEXT NOT NULL CHECK (origen IN ('camara', 'telegram', 'panel')),
    avistamiento_id INTEGER,
    solicitado_por INTEGER NOT NULL,
    estado         TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'EN_CURSO', 'OK', 'ERROR')),
    intentos       INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    nombre_judicial     TEXT,
    encadenar_judicial  INTEGER NOT NULL DEFAULT 0,
    creado         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    reservado_en   TEXT,
    terminado      TEXT,
    FOREIGN KEY (solicitado_por) REFERENCES usuarios (id)
);
CREATE INDEX IF NOT EXISTS idx_rgm_cola_estado ON rgm_cola (placa, alcance, estado);

-- Prompt 7 bloque A: quién pidió cada consulta (reemplaza el diccionario
-- en memoria _chat_por_item_rgm de nexcam_telegram.py -- si NEXORA se
-- reinicia entre el pedido y el resultado, esto sigue en la base). Un
-- mismo item_id puede tener VARIOS avisos (cámara lo encoló, Telegram
-- también lo pidió). alerta_chat_id/alerta_message_id identifican el
-- mensaje de una alerta EN VIVO para poder editarla al terminar (Prompt 6
-- punto 1); si están vacíos, o ya no existen en memoria (reinicio de
-- por medio), se manda un mensaje nuevo. mensaje_espera_id es el "🔎
-- Consultando…" que se edita a los MINUTOS_AVISO_ESPERA_RGM si no hay
-- resultado.
CREATE TABLE IF NOT EXISTS rgm_avisos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id            INTEGER NOT NULL,
    chat_id            INTEGER NOT NULL,
    usuario_id         INTEGER NOT NULL,
    alerta_chat_id     INTEGER,
    alerta_message_id  INTEGER,
    mensaje_espera_id  INTEGER,
    avisado_espera     INTEGER NOT NULL DEFAULT 0,
    creado_en          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (item_id) REFERENCES rgm_cola (id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
);
CREATE INDEX IF NOT EXISTS idx_rgm_avisos_item ON rgm_avisos (item_id);

-- Prompt 7 bloque A: diagnóstico de cada paso de una consulta (pedido,
-- reservado, resultado, aviso) con su hora -- página pequeña "Consulta
-- -> Diagnóstico" (últimos 50), nunca Telegram.
CREATE TABLE IF NOT EXISTS rgm_diagnostico (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    INTEGER,
    placa      TEXT,
    paso       TEXT NOT NULL,
    detalle    TEXT,
    creado_en  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_rgm_diagnostico_creado ON rgm_diagnostico (creado_en);

-- Prompt 8d punto 3: "Proceso completo (PDF)" generado por NEXORA (detalle +
-- TODAS las actuaciones) -- se guarda en disco (judicial_pdf.CARPETA_PDF) y
-- aquí queda el registro para no regenerarlo cada vez y mostrar el enlace
-- en la ficha del vehículo (sección Procesos).
CREATE TABLE IF NOT EXISTS procesos_pdf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    placa       TEXT NOT NULL,
    id_proceso  INTEGER,
    radicado    TEXT,
    ruta        TEXT NOT NULL,
    creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_procesos_pdf_placa ON procesos_pdf (placa, id_proceso);

-- Índices para las búsquedas más frecuentes
CREATE INDEX IF NOT EXISTS idx_vehiculos_placa  ON vehiculos (placa);
CREATE INDEX IF NOT EXISTS idx_vehiculos_estado ON vehiculos (estado);
CREATE INDEX IF NOT EXISTS idx_logs_usuario     ON logs_auditoria (usuario_id);
CREATE INDEX IF NOT EXISTS idx_logs_placa       ON logs_auditoria (placa_afectada);
CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones (usuario_id);
CREATE INDEX IF NOT EXISTS idx_eventos_usuario   ON eventos_notificacion (usuario_id);
CREATE INDEX IF NOT EXISTS idx_eventos_entregado ON eventos_notificacion (entregado);
CREATE INDEX IF NOT EXISTS idx_snapshots_placa   ON snapshots_placa (placa);
CREATE INDEX IF NOT EXISTS idx_cola_estudio_id   ON cola_estudio (estudio_id, estado, prioridad, orden);
"""


def _migrar_columnas_login(conn):
    """
    Migración aditiva: agrega `username` y `password_hash` a `usuarios` si
    todavía no existen. No borra ni modifica ninguna fila existente; los
    usuarios creados antes de esta etapa simplemente quedan con estos dos
    campos en NULL (pueden seguir usándose para el bot, pero no podrán
    iniciar sesión en el dashboard web hasta que se les asignen credenciales
    con crear_admin.py o vía /usuarios/alta).
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(usuarios)")}

    if "username" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN username TEXT")
    if "password_hash" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN password_hash TEXT")

    # UNIQUE se crea como índice aparte: SQLite no permite agregar la
    # restricción UNIQUE directamente en un ALTER TABLE ADD COLUMN.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios (username)"
    )


def _migrar_columnas_logs(conn):
    """
    Migración aditiva: agrega `observaciones` a `logs_auditoria` si todavía
    no existe. Los eventos ya registrados quedan con este campo en NULL
    (no había forma de saber su observación original); los eventos nuevos
    de alta/baja sí la guardan, para que el Historial (Etapa 4) pueda
    mostrarla sin inventar una segunda tabla de historial.
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(logs_auditoria)")}
    if "observaciones" not in columnas:
        conn.execute("ALTER TABLE logs_auditoria ADD COLUMN observaciones TEXT")


def _migrar_rol_operador():
    """
    Migración Etapa 4.5: renombra el rol 'Colaborador' a 'Operador' en todo
    el sistema.

    SQLite no permite modificar un CHECK constraint con ALTER TABLE, así
    que hace falta el procedimiento oficial de SQLite para cambios de
    esquema: crear una tabla nueva con el CHECK actualizado, copiar todas
    las filas conservando exactamente su `id` (para no romper las
    referencias existentes desde sesiones.usuario_id ni
    logs_auditoria.usuario_id), eliminar la tabla vieja y renombrar la
    nueva. No se pierde ningún usuario, columna ni historial.

    Usa su propia conexión (en vez de reutilizar get_connection) porque
    necesita desactivar temporalmente `PRAGMA foreign_keys` durante la
    reconstrucción: SQLite exige que ese pragma se cambie fuera de una
    transacción, y get_connection lo deja siempre en ON.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    try:
        fila = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='usuarios'"
        ).fetchone()
        if fila is None or "'Colaborador'" not in fila["sql"]:
            # Tabla nueva (ya creada con el CHECK actual) o ya migrada
            # en una ejecución anterior: no hay nada que hacer.
            return

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE usuarios_nueva (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id    INTEGER NOT NULL UNIQUE,
                nombre         TEXT NOT NULL,
                rol            TEXT NOT NULL CHECK (rol IN ('Admin', 'Operador')),
                estado         TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'inactivo')),
                fecha_creacion TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                username       TEXT,
                password_hash  TEXT
            )
            """
        )
        # El rol se convierte aquí, dentro del SELECT que alimenta la
        # tabla nueva: un UPDATE previo sobre la tabla vieja fallaría,
        # porque su CHECK original todavía solo permite
        # ('Admin', 'Colaborador') y rechazaría el valor 'Operador'.
        conn.execute(
            """
            INSERT INTO usuarios_nueva
                (id, telegram_id, nombre, rol, estado, fecha_creacion, username, password_hash)
            SELECT
                id, telegram_id, nombre,
                CASE WHEN rol = 'Colaborador' THEN 'Operador' ELSE rol END,
                estado, fecha_creacion, username, password_hash
            FROM usuarios
            """
        )
        conn.execute("DROP TABLE usuarios")
        conn.execute("ALTER TABLE usuarios_nueva RENAME TO usuarios")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios (username)"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def _migrar_columnas_operativas(conn):
    """
    Migración aditiva (Etapa 4.6): agrega a `usuarios` las columnas de
    Grado Operativo y control de actividad CAMARA. Los usuarios existentes
    quedan con grado_operativo=NULL (no aplica a Admin; un Operador
    existente lo tendrá sin asignar hasta que el Admin lo asigne desde su
    ficha) y estado_camara='ACTIVA' por defecto: ningún usuario arranca
    en alerta o suspendido por el solo hecho de migrar.
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(usuarios)")}
    if "grado_operativo" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN grado_operativo TEXT")
    if "estado_camara" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN estado_camara TEXT NOT NULL DEFAULT 'ACTIVA'")
    if "ultima_actividad_camara" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN ultima_actividad_camara TEXT")
    if "ultimo_acceso" not in columnas:
        conn.execute("ALTER TABLE usuarios ADD COLUMN ultimo_acceso TEXT")


def _migrar_codigo_operativo(conn):
    """
    Migración aditiva (Etapa 5 §35): agrega `codigo_operativo`, un código
    de 4 dígitos independiente del id interno de SQLite, asignado
    secuencialmente y que la desactivación lógica NUNCA libera.

    Los usuarios existentes (creados antes de esta etapa) reciben su
    código en el mismo orden en que fueron creados (id ascendente), una
    sola vez, la primera vez que esta migración corre. `crear_usuario`
    asigna el siguiente código disponible a los usuarios nuevos.
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(usuarios)")}
    es_columna_nueva = "codigo_operativo" not in columnas
    if es_columna_nueva:
        conn.execute("ALTER TABLE usuarios ADD COLUMN codigo_operativo TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_codigo ON usuarios (codigo_operativo)"
    )
    if es_columna_nueva:
        filas = conn.execute("SELECT id FROM usuarios ORDER BY id ASC").fetchall()
        for i, fila in enumerate(filas, start=1):
            conn.execute(
                "UPDATE usuarios SET codigo_operativo = ? WHERE id = ?",
                (f"{i:04d}", fila["id"]),
            )


def _migrar_columnas_vehiculo_nexcam(conn):
    """
    Migración aditiva (integración NEXCAM, FASE 1): agrega marca/línea/
    color/modelo a `vehiculos`, TEXT NULL. La app NEXCAM nunca escribe
    estos campos (solo los lee); llegan por la lista del día o por el alta
    manual (FASE 2). Los vehículos existentes quedan con estos 4 campos en
    NULL hasta que alguna de esas dos vías los complete.
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(vehiculos)")}
    for columna in ("marca", "linea", "color", "modelo"):
        if columna not in columnas:
            conn.execute(f"ALTER TABLE vehiculos ADD COLUMN {columna} TEXT")


def _migrar_columnas_vehiculo_rgm(conn):
    """
    Prompt 2b/5 (NEXCAM-PROMPTS-Y-PRUEBAS.md): el RGM llena TODAS las
    partes del vehículo, no solo marca/modelo. tipo_servicio/numero_serie
    vienen directo del bloque `vehiculo` del RGM; chasis/motor se extraen
    de `vehiculo.descripcion_bien` (ver rgm_cola._datos_vehiculo_desde_rgm).
    rgm_actualizado_en guarda cuándo se completó por última vez con un RGM
    real, para que la ficha pueda mostrar "según RGM del <fecha>" (línea y
    color NUNCA se tocan aquí: línea ya existía desde FASE 1, color no
    viene del RGM).
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(vehiculos)")}
    for columna in ("tipo_servicio", "numero_serie", "chasis", "motor", "rgm_actualizado_en"):
        if columna not in columnas:
            conn.execute(f"ALTER TABLE vehiculos ADD COLUMN {columna} TEXT")


def _migrar_rgm_cola_alcance(conn):
    """
    FASE 6/7: `rgm_cola` nació con la columna `tipo` (solo RGM/JUDICIAL) en
    un primer intento de esta integración; el plan vigente la renombra a
    `alcance` y agrega COMPLETO/nombre_judicial/encadenar_judicial. SQLite
    no permite renombrar una columna con CHECK ni ampliar un CHECK con
    ALTER TABLE, así que se reconstruye la tabla -- pero SOLO si está
    vacía (nunca hubo una consulta real completada con el esquema viejo;
    confirmado antes de escribir esta migración). Si alguna vez tuviera
    filas, no se toca y se registra en el log para decidir a mano: esta
    migración jamás descarta datos reales en silencio.
    """
    fila = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='rgm_cola'"
    ).fetchone()
    if fila is None or "tipo" not in fila["sql"] or "alcance" in fila["sql"]:
        return  # tabla nueva (ya con el esquema actual) o no existe todavía
    total = conn.execute("SELECT COUNT(*) AS n FROM rgm_cola").fetchone()["n"]
    if total > 0:
        print(
            f"AVISO: rgm_cola tiene {total} fila(s) con el esquema viejo (columna 'tipo') "
            "y no se migró automáticamente para no perder datos. Revisar a mano."
        )
        return
    conn.execute("DROP TABLE rgm_cola")


def _migrar_prioridad_rgm_cola(conn):
    """
    Prompt 7 bloque A: agrega `prioridad` a rgm_cola (antes el orden era
    un FIFO puro por `id`). Los ítems ya existentes se backfillean según
    su `origen` (PENDIENTE o no: no importa para items ya terminados,
    pero se hace parejo igual).
    """
    columnas = {fila["name"] for fila in conn.execute("PRAGMA table_info(rgm_cola)")}
    if "prioridad" in columnas:
        return
    conn.execute(f"ALTER TABLE rgm_cola ADD COLUMN prioridad INTEGER NOT NULL DEFAULT {PRIORIDAD_AUTOMATICA}")
    for origen, prioridad in PRIORIDAD_POR_ORIGEN.items():
        conn.execute("UPDATE rgm_cola SET prioridad = ? WHERE origen = ?", (prioridad, origen))


def _migrar_backfill_rgm_a_vehiculos():
    """
    Prompt 6 punto 2 (NEXCAM-PROMPTS-Y-PRUEBAS.md): el Prompt 2b agregó la
    regla "el RGM sobrescribe `vehiculos`", pero solo se aplica cuando
    LLEGA un resultado nuevo -- los snapshots RGM guardados ANTES de esa
    regla nunca llegaron a escribir la placa (ej. GCT 953 se quedó en
    "CHEVROLET / —" aunque su snapshot ya traía línea TRACKER y modelo
    2021). Esto corre UNA SOLA VEZ (idempotente: se detecta por el log
    centinela RGM_BACKFILL_PROMPT6 en logs_auditoria, sin tabla nueva) y
    aplica a cada placa su último snapshot RGM con estado "OK", con la
    MISMA regla de rgm_cola._actualizar_vehiculo_desde_rgm (sobrescribe,
    deja "antes -> después" en auditoría por placa). Copia MasterPlacas.db
    antes de tocar nada (regla de CLAUDE.md para una migración que cambia
    datos reales en volumen).

    Se llama DESPUÉS de que _migrar_columnas_vehiculo_rgm ya agregó las
    columnas (mismo bloque de inicializar_db) y con su PROPIA conexión,
    nunca anidada dentro del `with get_connection()` de otra migración:
    rgm_cola._actualizar_vehiculo_desde_rgm abre su propia conexión de
    escritura, y dos conexiones escribiendo la misma transacción abierta
    de SQLite se pisan entre sí.
    """
    with get_connection() as conn:
        ya_corrido = conn.execute(
            "SELECT 1 FROM logs_auditoria WHERE accion = 'RGM_BACKFILL_PROMPT6' LIMIT 1"
        ).fetchone()
        if ya_corrido:
            return
        admin = conn.execute(
            "SELECT id FROM usuarios WHERE rol = 'Admin' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if admin is None:
            return  # base nueva, sin ningún Admin todavía: no hay nada real que migrar
        usuario_id = admin["id"]
        filas = conn.execute(
            """SELECT s.placa, s.datos FROM snapshots_placa s
               WHERE s.fuente = 'RGM' AND s.id = (
                   SELECT s2.id FROM snapshots_placa s2
                   WHERE s2.placa = s.placa AND s2.fuente = 'RGM'
                   ORDER BY s2.creado_en DESC, s2.id DESC LIMIT 1
               )"""
        ).fetchall()

    if filas:
        try:
            import shutil

            respaldo = DB_PATH.with_name(f"{DB_PATH.stem}.antes-backfill-rgm-prompt6.bak")
            shutil.copy2(DB_PATH, respaldo)
        except Exception as e:
            print(f"AVISO: no se pudo copiar {DB_PATH.name} antes del backfill de RGM: {e}")

    import json

    import rgm_cola  # import perezoso: evita el ciclo rgm_cola <-> database al importar el módulo

    total_actualizadas = 0
    for fila in filas:
        try:
            datos = json.loads(fila["datos"] or "{}")
        except Exception:
            continue
        if datos.get("estado") != "OK":
            continue
        cambios = rgm_cola._actualizar_vehiculo_desde_rgm(fila["placa"], datos.get("vehiculo") or {}, usuario_id)
        if cambios:
            total_actualizadas += 1

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO logs_auditoria (usuario_id, accion, observaciones) VALUES (?, 'RGM_BACKFILL_PROMPT6', ?)",
            (usuario_id, f"Arreglo de datos viejos del RGM (Prompt 6): {total_actualizadas} placa(s) de "
                         f"{len(filas)} con snapshot RGM actualizada(s) con su último snapshot OK."),
        )


def _migrar_journal_mode_wal():
    """
    Prompt 8b punto 3: cambia el journal mode de SQLite a WAL (el modo por
    defecto, "rollback journal", deja un único escritor que bloquea a
    cualquier otro de inmediato; WAL permite que lecturas sigan mientras
    alguien escribe y que escrituras cortas se encolen en vez de chocar).
    Solo agrega los archivos -wal/-shm junto a MasterPlacas.db: no reescribe
    ninguna fila, pero de todos modos se copia la base antes (regla de
    CLAUDE.md), por si el cambio de modo coincidiera con algo ya corrupto.
    Idempotente: PRAGMA journal_mode ya devuelve 'wal' si esta migración
    corrió antes, así que no vuelve a copiar nada.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        modo_actual = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(modo_actual).lower() == "wal":
            return
        try:
            import shutil

            shutil.copy2(DB_PATH, DB_PATH.with_name(f"{DB_PATH.stem}.antes-wal-prompt8b.bak"))
        except Exception as e:
            print(f"AVISO: no se pudo copiar {DB_PATH.name} antes de activar WAL: {e}")
        conn.execute("PRAGMA journal_mode = WAL")
    finally:
        conn.close()


def _migrar_snapshots_sin_fk():
    """
    Prompt 8b punto 2: quita la FOREIGN KEY de snapshots_placa.placa hacia
    vehiculos.placa. Antes, consultar RGM/Judicial de una placa que no
    está en la lista (por /buscar en Telegram, Consulta o el estudio
    masivo -- ej. NHP973 en la prueba real) hacía que crear_snapshot
    reventara con sqlite3.IntegrityError y el ítem se quedara sin
    completar ni marcar error (500 en /rgm/trabajador/resultado, y la cola
    mostraba "En curso" para siempre en vez de reflejar el resultado real).

    snapshots_placa ya no necesita esa referencia: es solo un historial de
    lo que se consultó, por diseño nunca se borra ni se actualiza (ver su
    comentario en SCHEMA_SQL), y una placa que no esté en vehiculos sigue
    siendo una placa válida para consultar a pedido. Mismo procedimiento
    oficial de SQLite para cambios de esquema que _migrar_rol_operador
    (tabla nueva sin la FK, copiar filas conservando id, DROP + RENAME),
    con copia de MasterPlacas.db antes (regla de CLAUDE.md).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    try:
        fila = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='snapshots_placa'"
        ).fetchone()
        if fila is None or "FOREIGN KEY" not in (fila["sql"] or ""):
            # Tabla nueva (ya sin FK) o ya migrada antes: nada que hacer.
            return

        hay_filas = conn.execute("SELECT 1 FROM snapshots_placa LIMIT 1").fetchone() is not None
        if hay_filas:
            try:
                import shutil

                shutil.copy2(DB_PATH, DB_PATH.with_name(f"{DB_PATH.stem}.antes-snapshots-sin-fk-prompt8b.bak"))
            except Exception as e:
                print(f"AVISO: no se pudo copiar {DB_PATH.name} antes de quitar la FK de snapshots_placa: {e}")

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE snapshots_placa_nueva (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                placa       TEXT NOT NULL,
                fuente      TEXT NOT NULL,
                datos       TEXT,
                creado_en   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO snapshots_placa_nueva (id, placa, fuente, datos, creado_en)
            SELECT id, placa, fuente, datos, creado_en FROM snapshots_placa
            """
        )
        conn.execute("DROP TABLE snapshots_placa")
        conn.execute("ALTER TABLE snapshots_placa_nueva RENAME TO snapshots_placa")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_placa ON snapshots_placa (placa)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def inicializar_db():
    """Crea las tablas e índices si no existen, y aplica migraciones aditivas
    sobre tablas ya existentes. Es seguro llamarla varias veces."""
    _migrar_journal_mode_wal()
    _migrar_snapshots_sin_fk()
    with get_connection() as conn:
        _migrar_rgm_cola_alcance(conn)  # ANTES del executescript: DROP si corresponde
        conn.executescript(SCHEMA_SQL)
        _migrar_columnas_login(conn)
        _migrar_columnas_logs(conn)
    # Se ejecuta con conexión propia (ver docstring) y después de que
    # username/password_hash ya existan, para que la tabla reconstruida
    # las conserve.
    _migrar_rol_operador()
    # Se ejecuta DESPUÉS de _migrar_rol_operador a propósito: si esa
    # migración de rol alguna vez tuviera que reconstruir la tabla
    # `usuarios` (hoy ya no aplica, la base real quedó migrada en la
    # Etapa 4.5), su copia de columnas es una lista fija que no incluye
    # estas nuevas columnas operativas; agregarlas antes se perdería en
    # esa reconstrucción. Corriendo esta migración después, nunca hay
    # nada que perder.
    with get_connection() as conn:
        _migrar_columnas_operativas(conn)
        _migrar_codigo_operativo(conn)
        _migrar_columnas_vehiculo_nexcam(conn)
        _migrar_columnas_vehiculo_rgm(conn)
        _migrar_prioridad_rgm_cola(conn)
    _migrar_backfill_rgm_a_vehiculos()
    print(f"Base de datos inicializada en: {DB_PATH}")


# ---------------------------------------------------------------------------
# Excepciones propias
# ---------------------------------------------------------------------------

class PlacaDuplicadaError(Exception):
    """Se lanza al intentar insertar una placa que ya existe."""


class RegistroNoEncontradoError(Exception):
    """Se lanza cuando se busca/actualiza un registro que no existe."""


class ValorInvalidoError(Exception):
    """Se lanza cuando un valor no cumple con los valores permitidos."""


# ---------------------------------------------------------------------------
# CRUD: usuarios
# ---------------------------------------------------------------------------

def _normalizar_username(username: str):
    """
    Normaliza el username a minúsculas para que el login sea insensible a
    mayúsculas/minúsculas ('Admin', 'ADMIN' y 'admin' son la misma cuenta).
    Se aplica tanto al guardar como al buscar, así el índice UNIQUE de la
    columna también impide crear dos cuentas que solo difieran en el case.
    """
    return username.strip().lower() if username else None


def crear_usuario(
    telegram_id: int,
    nombre: str,
    rol: str,
    username: str = None,
    password_hash: str = None,
) -> int:
    """
    Registra un nuevo usuario autorizado. Devuelve el id creado.

    `username`/`password_hash` son opcionales: un usuario creado solo para
    el bot de Telegram (por ejemplo, por un caller externo a este dashboard)
    puede omitirlos y simplemente no tendrá acceso de login web hasta que
    se le asignen con `establecer_credenciales`.

    También asigna el siguiente `codigo_operativo` disponible (Etapa 5
    §35): un código de 4 dígitos independiente del id de SQLite, que la
    desactivación lógica nunca libera.
    """
    if rol not in ROLES_USUARIO:
        raise ValorInvalidoError(f"Rol inválido: {rol}. Use uno de {ROLES_USUARIO}")

    with get_connection() as conn:
        fila = conn.execute(
            "SELECT COALESCE(MAX(CAST(codigo_operativo AS INTEGER)), 0) AS maximo FROM usuarios"
        ).fetchone()
        siguiente_codigo = f"{fila['maximo'] + 1:04d}"

        cur = conn.execute(
            """INSERT INTO usuarios (telegram_id, nombre, rol, username, password_hash, codigo_operativo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (telegram_id, nombre, rol, _normalizar_username(username), password_hash, siguiente_codigo),
        )
        return cur.lastrowid


def telegram_id_provisional() -> int:
    """
    Número provisional para `telegram_id` cuando el usuario se crea sin
    Telegram. La columna es NOT NULL UNIQUE, así que se usan números
    negativos (-1, -2, ...): Telegram nunca asigna IDs negativos a personas,
    así que no chocan con uno real. Se reemplaza cuando se vincule su Telegram.
    """
    with get_connection() as conn:
        fila = conn.execute("SELECT MIN(telegram_id) AS minimo FROM usuarios").fetchone()
    minimo = fila["minimo"] if fila and fila["minimo"] is not None else 0
    return min(minimo, 0) - 1


def obtener_usuario_por_telegram_id(telegram_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return dict(row) if row else None


def obtener_usuario_por_username(username: str):
    """
    Busca un usuario por su nombre de login web (no por telegram_id).
    Insensible a mayúsculas/minúsculas: compara con LOWER(username) en vez
    de solo confiar en que el valor guardado ya esté en minúsculas, para
    que también funcione con cuentas creadas antes de esta normalización.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE LOWER(username) = ?",
            (_normalizar_username(username),),
        ).fetchone()
        return dict(row) if row else None


def establecer_credenciales(usuario_id: int, username: str, password_hash: str):
    """
    Asigna o actualiza el username y el hash de contraseña de un usuario
    existente. Usada por crear_admin.py (ejecutado manualmente en la
    terminal) y, más adelante, por la gestión de usuarios del dashboard.
    Nunca recibe la contraseña en texto plano: siempre un hash ya calculado.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET username = ?, password_hash = ? WHERE id = ?",
            (_normalizar_username(username), password_hash, usuario_id),
        )
        if cur.rowcount == 0:
            raise RegistroNoEncontradoError(f"Usuario id={usuario_id} no existe")


def listar_usuarios(solo_activos: bool = True):
    query = "SELECT * FROM usuarios"
    if solo_activos:
        query += " WHERE estado = 'activo'"
    query += " ORDER BY nombre"

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def cambiar_estado_usuario(usuario_id: int, nuevo_estado: str):
    if nuevo_estado not in ESTADOS_USUARIO:
        raise ValorInvalidoError(f"Estado inválido: {nuevo_estado}")

    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET estado = ? WHERE id = ?", (nuevo_estado, usuario_id)
        )
        if cur.rowcount == 0:
            raise RegistroNoEncontradoError(f"Usuario id={usuario_id} no existe")


# ---------------------------------------------------------------------------
# CRUD: vehiculos
# ---------------------------------------------------------------------------

def insertar_placa(
    placa: str,
    tipo_vehiculo: str,
    usuario_id: int,
    observaciones: str = None,
) -> int:
    """
    Inserta una nueva placa en estado 'activo' y registra el log de auditoría.
    Lanza PlacaDuplicadaError si la placa ya existe.
    """
    placa = placa.strip().upper()

    if tipo_vehiculo not in TIPOS_VEHICULO:
        raise ValorInvalidoError(
            f"Tipo de vehículo inválido. Usa uno de: {', '.join(TIPOS_VEHICULO)}."
        )

    with get_connection() as conn:
        existente = conn.execute(
            "SELECT id FROM vehiculos WHERE placa = ?", (placa,)
        ).fetchone()
        if existente:
            raise PlacaDuplicadaError(f"La placa {placa} ya existe en el sistema.")

        cur = conn.execute(
            """INSERT INTO vehiculos (placa, tipo_vehiculo, observaciones)
               VALUES (?, ?, ?)""",
            (placa, tipo_vehiculo, observaciones),
        )
        vehiculo_id = cur.lastrowid

        _registrar_log(conn, usuario_id, "ALTA_PLACA", placa, observaciones)
        return vehiculo_id


def insertar_placas_masivo(placas: list, usuario_id: int) -> dict:
    """
    Inserta varias placas a la vez.
    `placas` es una lista de dicts: {"placa": ..., "tipo_vehiculo": ..., "observaciones": ...}
    Devuelve un resumen {"insertadas": [...], "duplicadas": [...], "invalidas": [...]}
    para que la carga masiva no se detenga por un solo error.
    """
    resumen = {"insertadas": [], "duplicadas": [], "invalidas": []}

    for item in placas:
        placa = item.get("placa", "").strip().upper()
        tipo = item.get("tipo_vehiculo")
        obs = item.get("observaciones")

        try:
            insertar_placa(placa, tipo, usuario_id, obs)
            resumen["insertadas"].append(placa)
        except PlacaDuplicadaError:
            resumen["duplicadas"].append(placa)
        except ValorInvalidoError:
            resumen["invalidas"].append(placa)

    return resumen


def listar_placas_activas():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM vehiculos WHERE estado = 'activo' ORDER BY fecha_ingreso DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def buscar_placa(placa: str):
    placa = placa.strip().upper()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM vehiculos WHERE placa = ?", (placa,)
        ).fetchone()
        return dict(row) if row else None


def obtener_ficha_vehiculo(placa: str):
    """
    Ficha completa de un vehículo para /consulta: sus propios datos más
    quién lo dio de alta. `vehiculos` no guarda el usuario_id de quien
    registró la placa (por diseño: cualquier usuario_id enviado por el
    cliente sería falsificable), así que ese dato se resuelve leyendo el
    evento ALTA_PLACA correspondiente en logs_auditoria (que sí guarda un
    usuario_id de servidor, nunca de formulario). None si la placa no existe.
    """
    vehiculo = buscar_placa(placa)
    if vehiculo is None:
        return None

    with get_connection() as conn:
        fila_alta = conn.execute(
            """SELECT u.nombre FROM logs_auditoria l
               JOIN usuarios u ON u.id = l.usuario_id
               WHERE l.placa_afectada = ? AND l.accion = 'ALTA_PLACA'
               ORDER BY l.fecha_hora ASC LIMIT 1""",
            (vehiculo["placa"],),
        ).fetchone()

    vehiculo["registrado_por"] = fila_alta["nombre"] if fila_alta else None
    return vehiculo


def listar_logs_de_placa(placa: str):
    """
    Historial de una placa para /historial/{placa}: reutiliza
    logs_auditoria (no crea una tabla de historial nueva), filtrando por
    placa directamente en SQL y resolviendo el nombre del usuario con un
    JOIN, para no mostrar solo un "Usuario ID" en la interfaz. Más
    reciente primero.
    """
    placa = placa.strip().upper()
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT l.*, u.nombre AS usuario_nombre, u.username AS usuario_username
               FROM logs_auditoria l
               LEFT JOIN usuarios u ON u.id = l.usuario_id
               WHERE l.placa_afectada = ?
               ORDER BY l.fecha_hora DESC""",
            (placa,),
        ).fetchall()
        return [dict(f) for f in filas]


def buscar_vehiculos(query: str = None, estado: str = None):
    """
    Lista vehículos combinando búsqueda por placa (insensible a
    mayúsculas/minúsculas, coincidencia parcial) y filtro por estado.
    Sin argumentos, equivale a listar todos ordenados por fecha de ingreso.
    Usada por el Inventario: mantiene el SQL de búsqueda/filtro en la capa
    de datos en vez de traer todas las filas y filtrar en Python o en la
    plantilla.
    """
    if estado is not None and estado not in ESTADOS_VEHICULO:
        raise ValorInvalidoError(f"Estado inválido: {estado}")

    sql = "SELECT * FROM vehiculos WHERE 1=1"
    params = []

    if query:
        sql += " AND UPPER(placa) LIKE ?"
        params.append(f"%{query.strip().upper()}%")
    if estado:
        sql += " AND estado = ?"
        params.append(estado)

    sql += " ORDER BY fecha_ingreso DESC"

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def buscar_vehiculos_vivo(q: str, estado: str = None, limite: int = 50):
    """
    Búsqueda "mientras se escribe" (Prompt 6c punto 2): por PREFIJO (la
    placa EMPIEZA por q) y por "contiene" (q en cualquier posición), con
    los prefijos primero -- usada tanto por el filtro en vivo de
    Vehículos (con `estado`, para respetar Todos/Activos/Dados de baja)
    como por las sugerencias de Consulta (sin `estado`). Reemplaza a la
    antigua buscar_placas_por_prefijo (solo prefijo, sin "contiene").
    """
    q = (q or "").strip().upper()
    if not q:
        return []
    if estado is not None and estado not in ESTADOS_VEHICULO:
        raise ValorInvalidoError(f"Estado inválido: {estado}")

    comodin = q.replace("%", "\\%").replace("_", "\\_")
    sql = """
        SELECT placa, tipo_vehiculo, estado, marca, linea, fecha_ingreso, fecha_baja,
               (UPPER(placa) LIKE ? ESCAPE '\\') AS es_prefijo
        FROM vehiculos
        WHERE UPPER(placa) LIKE ? ESCAPE '\\'
    """
    params = [comodin + "%", "%" + comodin + "%"]
    if estado:
        sql += " AND estado = ?"
        params.append(estado)
    sql += " ORDER BY es_prefijo DESC, placa ASC LIMIT ?"
    params.append(limite)

    with get_connection() as conn:
        return [dict(f) for f in conn.execute(sql, params).fetchall()]


def obtener_resumen_vehiculos():
    """
    Conteos agregados de vehículos para el Dashboard, en una sola consulta
    (evita traer todas las filas a Python solo para contarlas). Devuelve
    0 en cada campo cuando la tabla está vacía (COALESCE), nunca None.
    """
    with get_connection() as conn:
        fila = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN estado = 'activo' THEN 1 ELSE 0 END), 0) AS activos,
                COALESCE(SUM(CASE WHEN estado = 'baja' THEN 1 ELSE 0 END), 0) AS bajas,
                COALESCE(SUM(CASE WHEN estado = 'activo' AND tipo_vehiculo = 'carro' THEN 1 ELSE 0 END), 0) AS carros_activos,
                COALESCE(SUM(CASE WHEN estado = 'activo' AND tipo_vehiculo = 'moto' THEN 1 ELSE 0 END), 0) AS motos_activas
            FROM vehiculos
            """
        ).fetchone()
        return dict(fila)


def obtener_resumen_usuarios():
    """Conteos agregados de usuarios para el Dashboard, en una sola consulta."""
    with get_connection() as conn:
        fila = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN estado = 'activo' THEN 1 ELSE 0 END), 0) AS activos
            FROM usuarios
            """
        ).fetchone()
        return dict(fila)


def resumen_camara_operadores():
    """FASE 7 ('Hoy'): cuántos Operadores activos están en cada estado
    CAMARA guardado (lectura directa, perezosa como el resto del
    proyecto -- no recalcula aquí, cada usuario ya se recalculó la
    última vez que se mostró su ficha/lista)."""
    with get_connection() as conn:
        fila = conn.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN estado_camara = 'ACTIVA' THEN 1 ELSE 0 END), 0) AS activa,
                COALESCE(SUM(CASE WHEN estado_camara = 'EN_ALERTA' THEN 1 ELSE 0 END), 0) AS en_alerta,
                COALESCE(SUM(CASE WHEN estado_camara = 'SUSPENDIDA' THEN 1 ELSE 0 END), 0) AS suspendida
               FROM usuarios WHERE rol = 'Operador' AND estado = 'activo'"""
        ).fetchone()
        return dict(fila)


def cambiar_estado_a_baja(placa: str, usuario_id: int, observaciones: str = None):
    """
    Da de baja una placa. Requiere confirmación manual del usuario que llama
    a esta función (no se invoca automáticamente desde ningún proceso).
    """
    placa = placa.strip().upper()

    with get_connection() as conn:
        actual = conn.execute(
            "SELECT * FROM vehiculos WHERE placa = ?", (placa,)
        ).fetchone()
        if not actual:
            raise RegistroNoEncontradoError(f"La placa {placa} no existe")
        if actual["estado"] == "baja":
            raise ValorInvalidoError(f"La placa {placa} ya está dada de baja")

        campos = ["estado = 'baja'", "fecha_baja = datetime('now', 'localtime')"]
        params = []
        if observaciones is not None:
            campos.append("observaciones = ?")
            params.append(observaciones)
        params.append(placa)

        conn.execute(
            f"UPDATE vehiculos SET {', '.join(campos)} WHERE placa = ?", params
        )
        _registrar_log(conn, usuario_id, "BAJA_PLACA", placa, observaciones)


# ---------------------------------------------------------------------------
# CRUD: logs_auditoria
# ---------------------------------------------------------------------------

def _registrar_log(conn, usuario_id: int, accion: str, placa_afectada: str = None, observaciones: str = None):
    """Versión interna que reutiliza una conexión/transacción ya abierta."""
    conn.execute(
        """INSERT INTO logs_auditoria (usuario_id, accion, placa_afectada, observaciones)
           VALUES (?, ?, ?, ?)""",
        (usuario_id, accion, placa_afectada, observaciones),
    )


def registrar_log(usuario_id: int, accion: str, placa_afectada: str = None, observaciones: str = None) -> int:
    """Versión pública: abre su propia conexión/transacción."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO logs_auditoria (usuario_id, accion, placa_afectada, observaciones)
               VALUES (?, ?, ?, ?)""",
            (usuario_id, accion, placa_afectada, observaciones),
        )
        return cur.lastrowid


def listar_logs(placa: str = None, usuario_id: int = None, limite: int = 100):
    """Lista logs de auditoría, opcionalmente filtrados por placa o usuario."""
    query = "SELECT * FROM logs_auditoria WHERE 1=1"
    params = []

    if placa:
        query += " AND placa_afectada = ?"
        params.append(placa.strip().upper())
    if usuario_id:
        query += " AND usuario_id = ?"
        params.append(usuario_id)

    query += " ORDER BY fecha_hora DESC LIMIT ?"
    params.append(limite)

    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------------------------------------------------------------------------
# NUEVO (Paso 6 - Dashboard Web Admin): funciones añadidas a tu database.py
# original. No modifican nada existente, solo agregan lo que el dashboard
# necesita y que el bot de Telegram no requería.
# ---------------------------------------------------------------------------

def listar_todas_las_placas():
    """
    Lista TODOS los vehículos (activos y dados de baja), ordenados por
    fecha de ingreso descendente. El bot solo necesitaba las activas
    (listar_placas_activas); el inventario del dashboard debe poder
    mostrar también el historial de bajas.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM vehiculos ORDER BY fecha_ingreso DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def obtener_usuario_por_id(usuario_id: int):
    """Busca un usuario por su id interno (no por telegram_id)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Sesiones de login (Etapa 1 — autenticación real)
# ---------------------------------------------------------------------------
#
# El servidor genera un token aleatorio opaco (no el usuario_id) y lo guarda
# aquí junto con su fecha de expiración. El navegador solo recibe el token
# en una cookie httponly; el usuario_id real nunca viaja en el cliente, así
# que no se puede falsificar escribiendo otro id en un campo o cookie.

def crear_sesion(usuario_id: int) -> str:
    """Crea una sesión nueva para usuario_id y devuelve el token opaco."""
    token = secrets.token_urlsafe(32)
    expira_en = (
        datetime.now() + timedelta(hours=DURACION_SESION_HORAS)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        # Aprovecha para limpiar sesiones vencidas de este mismo usuario,
        # así la tabla no crece sin límite con cada login.
        conn.execute(
            "DELETE FROM sesiones WHERE usuario_id = ? AND expira_en <= datetime('now', 'localtime')",
            (usuario_id,),
        )
        conn.execute(
            "INSERT INTO sesiones (token, usuario_id, expira_en) VALUES (?, ?, ?)",
            (token, usuario_id, expira_en),
        )
    return token


def obtener_usuario_por_token(token: str):
    """
    Devuelve el usuario asociado a un token de sesión vigente (no vencido),
    o None si el token no existe, ya expiró, o el usuario fue borrado.
    Se resuelve con un JOIN "en vivo": si el usuario fue desactivado después
    de crear la sesión, su fila seguirá encontrándose (para poder revisar su
    estado), pero requerir_autenticacion() la rechazará por estado inactivo.
    """
    with get_connection() as conn:
        fila = conn.execute(
            """SELECT u.* FROM sesiones s
               JOIN usuarios u ON u.id = s.usuario_id
               WHERE s.token = ? AND s.expira_en > datetime('now', 'localtime')""",
            (token,),
        ).fetchone()
        return dict(fila) if fila else None


def eliminar_sesion(token: str):
    """Invalida un token de sesión (logout). No falla si el token no existe."""
    with get_connection() as conn:
        conn.execute("DELETE FROM sesiones WHERE token = ?", (token,))


def invalidar_sesiones_de_usuario(usuario_id: int):
    """
    Invalida TODAS las sesiones activas de un usuario (Etapa 4.6): usada al
    desactivarlo o al cambiarle la contraseña, para que una sesión web ya
    abierta no seguir funcionando tras cualquiera de esas dos acciones.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))


def actualizar_ultimo_acceso(usuario_id: int, momento: str):
    with get_connection() as conn:
        conn.execute("UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?", (momento, usuario_id))


# ---------------------------------------------------------------------------
# Grado Operativo y estado CAMARA (Etapa 4.6)
#
# El Grado Operativo y el estado_camara viven en `usuarios`, NUNCA en
# `vehiculos`: la antigüedad de una placa se calcula al vuelo a partir de
# su fecha_ingreso (que nunca se modifica), no se le asigna un grado
# guardado. Ver camara.py para el cálculo de ambos.
# ---------------------------------------------------------------------------

def establecer_grado_operativo(usuario_id: int, grado: str):
    if grado not in GRADOS_OPERATIVOS:
        raise ValorInvalidoError(f"Grado operativo inválido: {grado}. Usa uno de {GRADOS_OPERATIVOS}")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET grado_operativo = ? WHERE id = ?", (grado, usuario_id)
        )
        if cur.rowcount == 0:
            raise RegistroNoEncontradoError(f"Usuario id={usuario_id} no existe")


def actualizar_estado_camara(usuario_id: int, nuevo_estado: str):
    if nuevo_estado not in ESTADOS_CAMARA:
        raise ValorInvalidoError(f"Estado CAMARA inválido: {nuevo_estado}")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE usuarios SET estado_camara = ? WHERE id = ?", (nuevo_estado, usuario_id)
        )
        if cur.rowcount == 0:
            raise RegistroNoEncontradoError(f"Usuario id={usuario_id} no existe")


def registrar_actividad_camara_db(usuario_id: int, momento: str):
    """
    Escritura cruda de una actividad CAMARA real: siempre guarda el
    momento; solo restaura el estado a ACTIVA si NO estaba SUSPENDIDA
    (Etapa 5 §36: la actividad real ya no levanta una suspensión, solo un
    Admin puede hacerlo vía actualizar_estado_camara/reactivar_camara_manual).
    La orquestación (auditoría, eventos de notificación) vive en
    camara.registrar_actividad_camara, que es quien debe llamarse
    normalmente; esta función es el detalle de persistencia.
    """
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT estado_camara FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        if fila is None:
            raise RegistroNoEncontradoError(f"Usuario id={usuario_id} no existe")

        if fila["estado_camara"] == "SUSPENDIDA":
            conn.execute(
                "UPDATE usuarios SET ultima_actividad_camara = ? WHERE id = ?",
                (momento, usuario_id),
            )
        else:
            conn.execute(
                "UPDATE usuarios SET ultima_actividad_camara = ?, estado_camara = 'ACTIVA' WHERE id = ?",
                (momento, usuario_id),
            )


# ---------------------------------------------------------------------------
# Cola de eventos de notificación (Etapa 4.6) — preparación para Telegram
# ---------------------------------------------------------------------------

def crear_evento_notificacion(usuario_id: int, tipo_evento: str, datos: str = None) -> int:
    if tipo_evento not in TIPOS_EVENTO_NOTIFICACION:
        raise ValorInvalidoError(f"Tipo de evento inválido: {tipo_evento}")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO eventos_notificacion (usuario_id, tipo_evento, datos) VALUES (?, ?, ?)",
            (usuario_id, tipo_evento, datos),
        )
        return cur.lastrowid


def listar_eventos_pendientes(limite: int = 100):
    """Eventos aún no entregados. Consumidos por el bot de Telegram
    (nexcam_telegram.py, FASE 3)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM eventos_notificacion WHERE entregado = 0 ORDER BY creado_en ASC LIMIT ?",
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]


def marcar_evento_entregado(evento_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE eventos_notificacion SET entregado = 1 WHERE id = ?", (evento_id,))


# ---------------------------------------------------------------------------
# Snapshots de información externa por placa (Etapa 5)
#
# Cada fila es un hecho histórico inmutable: nunca se actualiza una fila
# existente, solo se inserta una nueva (Etapa 5 §30). "El snapshot vigente
# de una placa" es siempre el de creado_en más reciente para esa placa.
# ---------------------------------------------------------------------------

def crear_snapshot(placa: str, fuente: str, datos: str = None) -> int:
    """
    `datos` es un TEXT (JSON serializado por el caller) con lo que se haya
    obtenido: nunca se interpreta ni se convierte aquí, así que campos
    como el número de radicado viajan intactos como texto (Etapa 5 §20).
    """
    placa = placa.strip().upper()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots_placa (placa, fuente, datos) VALUES (?, ?, ?)",
            (placa, fuente, datos),
        )
        return cur.lastrowid


def obtener_ultimo_snapshot(placa: str):
    placa = placa.strip().upper()
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM snapshots_placa WHERE placa = ? ORDER BY creado_en DESC, id DESC LIMIT 1",
            (placa,),
        ).fetchone()
        return dict(fila) if fila else None


def obtener_ultimo_snapshot_por_fuente(placa: str, fuente: str):
    """Igual que obtener_ultimo_snapshot pero filtrado por fuente ('RGM' o
    'JUDICIAL'): necesario porque ambas conviven en la misma tabla."""
    placa = placa.strip().upper()
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM snapshots_placa WHERE placa = ? AND fuente = ? ORDER BY creado_en DESC, id DESC LIMIT 1",
            (placa, fuente),
        ).fetchone()
        return dict(fila) if fila else None


# ---------------------------------------------------------------------------
# Cola RGM/Judicial (FASE 6 NEXCAM) — ver rgm_cola.py para la lógica.
# ---------------------------------------------------------------------------

def crear_item_rgm_cola(placa: str, alcance: str, origen: str, solicitado_por: int, avistamiento_id: int = None,
                         nombre_judicial: str = None, encadenar_judicial: bool = False) -> int:
    if alcance not in ALCANCES_CONSULTA_RGM:
        raise ValorInvalidoError(f"Alcance de consulta RGM inválido: {alcance}")
    if origen not in ORIGENES_CONSULTA_RGM:
        raise ValorInvalidoError(f"Origen de consulta RGM inválido: {origen}")
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO rgm_cola (placa, alcance, origen, avistamiento_id, solicitado_por, nombre_judicial, encadenar_judicial, prioridad)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (placa.strip().upper(), alcance, origen, avistamiento_id, solicitado_por, nombre_judicial,
             int(encadenar_judicial), PRIORIDAD_POR_ORIGEN.get(origen, PRIORIDAD_AUTOMATICA)),
        )
        return cur.lastrowid


def subir_prioridad_rgm(item_id: int, prioridad: int):
    """Prompt 7 bloque A, punto 2: un pedido de Telegram/panel sobre un
    ítem YA encolado automáticamente (cámara/estudio) sube su prioridad,
    en vez de quedarse esperando detrás en el FIFO -- solo sube, nunca
    baja (si ya tenía mejor prioridad que la pedida, no se toca)."""
    with get_connection() as conn:
        conn.execute("UPDATE rgm_cola SET prioridad = ? WHERE id = ? AND prioridad > ?", (prioridad, item_id, prioridad))


def posicion_en_cola_rgm(item_id: int) -> int:
    """Puesto de `item_id` entre los PENDIENTE (1 = el próximo que se
    reserva). 0 si ya está EN_CURSO o ya no está pendiente."""
    with get_connection() as conn:
        item = conn.execute("SELECT prioridad, id, estado FROM rgm_cola WHERE id = ?", (item_id,)).fetchone()
        if item is None or item["estado"] != "PENDIENTE":
            return 0
        fila = conn.execute(
            """SELECT COUNT(*) AS n FROM rgm_cola
               WHERE estado = 'PENDIENTE' AND (prioridad < ? OR (prioridad = ? AND id <= ?))""",
            (item["prioridad"], item["prioridad"], item["id"]),
        ).fetchone()
        return fila["n"]


def item_rgm_pendiente_o_en_curso(placa: str, alcance: str):
    with get_connection() as conn:
        fila = conn.execute(
            """SELECT * FROM rgm_cola WHERE placa = ? AND alcance = ? AND estado IN ('PENDIENTE', 'EN_CURSO')
               ORDER BY id DESC LIMIT 1""",
            (placa.strip().upper(), alcance),
        ).fetchone()
        return dict(fila) if fila else None


def obtener_item_rgm(item_id: int):
    with get_connection() as conn:
        fila = conn.execute("SELECT * FROM rgm_cola WHERE id = ?", (item_id,)).fetchone()
        return dict(fila) if fila else None


def reservar_siguiente_item_rgm():
    """
    Toma UNA consulta a la vez: la más vieja en PENDIENTE, la marca
    EN_CURSO (con reservado_en) y la devuelve. Usa una sola transacción
    (UPDATE ... WHERE estado = 'PENDIENTE' seguido del SELECT de esa
    misma fila) para que dos llamadas simultáneas nunca reserven la
    misma fila dos veces.

    Un ítem EN_CURSO abandonado (recarga de la página Consulta a mitad de
    una consulta real) se libera solo, sin depender de esta función: ver
    rgm_cola._hilo_vigilante_colgados (Prompt 8d punto 5), que corre
    aparte y no necesita que nadie vuelva a pedir trabajo de la cola.
    """
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT id FROM rgm_cola WHERE estado = 'PENDIENTE' ORDER BY prioridad ASC, id ASC LIMIT 1"
        ).fetchone()
        if fila is None:
            return None
        cur = conn.execute(
            """UPDATE rgm_cola SET estado = 'EN_CURSO', intentos = intentos + 1, reservado_en = datetime('now', 'localtime')
               WHERE id = ? AND estado = 'PENDIENTE'""",
            (fila["id"],),
        )
        if cur.rowcount == 0:
            return None  # otra llamada la reservó primero
        return dict(conn.execute("SELECT * FROM rgm_cola WHERE id = ?", (fila["id"],)).fetchone())


def reservar_siguiente_item_rgm_judicial():
    """
    Prompt 8: Judicial ya NO depende del Connector/la página Consulta --
    lo atiende un hilo propio de NEXORA-CONTROL (judicial_directo.py)
    contra la API real. Misma mecánica que reservar_siguiente_item_rgm
    pero acotada a alcance='JUDICIAL' (nunca toca ni reserva ítems RGM,
    que siguen siendo del Connector). La liberación de ítems EN_CURSO
    abandonados corre aparte (ver reservar_siguiente_item_rgm arriba).
    """
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT id FROM rgm_cola WHERE alcance = 'JUDICIAL' AND estado = 'PENDIENTE' "
            "ORDER BY prioridad ASC, id ASC LIMIT 1"
        ).fetchone()
        if fila is None:
            return None
        cur = conn.execute(
            """UPDATE rgm_cola SET estado = 'EN_CURSO', intentos = intentos + 1, reservado_en = datetime('now', 'localtime')
               WHERE id = ? AND estado = 'PENDIENTE'""",
            (fila["id"],),
        )
        if cur.rowcount == 0:
            return None
        return dict(conn.execute("SELECT * FROM rgm_cola WHERE id = ?", (fila["id"],)).fetchone())


def marcar_item_rgm_completado(item_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE rgm_cola SET estado = 'OK', terminado = datetime('now', 'localtime') WHERE id = ?",
            (item_id,),
        )


def marcar_item_rgm_error(item_id: int, mensaje: str):
    """
    Si ya se intentó MAX_INTENTOS_RGM veces, queda ERROR (final). Si no,
    vuelve a PENDIENTE para que reservar_siguiente_item_rgm la retome.
    """
    with get_connection() as conn:
        fila = conn.execute("SELECT intentos FROM rgm_cola WHERE id = ?", (item_id,)).fetchone()
        if fila is None:
            return
        if fila["intentos"] >= MAX_INTENTOS_RGM:
            conn.execute(
                "UPDATE rgm_cola SET estado = 'ERROR', error = ?, terminado = datetime('now', 'localtime') WHERE id = ?",
                (mensaje, item_id),
            )
        else:
            conn.execute("UPDATE rgm_cola SET estado = 'PENDIENTE', error = ? WHERE id = ?", (mensaje, item_id))


def listar_items_rgm_en_curso_colgados() -> list:
    """Prompt 8d punto 5: ítems EN_CURSO sin resultado ni heartbeat desde
    hace más de MINUTOS_ABANDONO_ITEM_RGM minutos -- solo LEE, no toca
    nada (rgm_cola._hilo_vigilante_colgados decide qué hacer con cada
    uno, reusando marcar_error() para no duplicar la lógica de
    reintentos de marcar_item_rgm_error)."""
    with get_connection() as conn:
        limite = (datetime.now() - timedelta(minutes=MINUTOS_ABANDONO_ITEM_RGM)).strftime("%Y-%m-%d %H:%M:%S")
        filas = conn.execute(
            "SELECT id, placa, alcance FROM rgm_cola WHERE estado = 'EN_CURSO' AND reservado_en < ?", (limite,)
        ).fetchall()
        return [dict(f) for f in filas]


def listar_items_rgm_activos():
    """Pendientes + en curso (para mostrar la cola en vivo en 'Consulta')."""
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT c.*, u.nombre AS solicitado_por_nombre FROM rgm_cola c
               LEFT JOIN usuarios u ON u.id = c.solicitado_por
               WHERE c.estado IN ('PENDIENTE', 'EN_CURSO')
               ORDER BY c.prioridad ASC, c.id ASC"""
        ).fetchall()
        return [dict(f) for f in filas]


# ---------------------------------------------------------------------------
# Avisos RGM (Prompt 7 bloque A): quién pidió cada consulta por Telegram,
# para poder avisarle al terminar aunque NEXORA se reinicie de por medio.
# ---------------------------------------------------------------------------

def crear_aviso_rgm(item_id: int, chat_id: int, usuario_id: int, alerta_chat_id: int = None,
                     alerta_message_id: int = None, mensaje_espera_id: int = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO rgm_avisos (item_id, chat_id, usuario_id, alerta_chat_id, alerta_message_id, mensaje_espera_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (item_id, chat_id, usuario_id, alerta_chat_id, alerta_message_id, mensaje_espera_id),
        )
        return cur.lastrowid


def listar_avisos_rgm(item_id: int) -> list:
    with get_connection() as conn:
        return [dict(f) for f in conn.execute("SELECT * FROM rgm_avisos WHERE item_id = ?", (item_id,)).fetchall()]


def copiar_avisos_rgm(item_id_origen: int, item_id_destino: int):
    """"Judicial sola" sin snapshot RGM reciente encadena una RGM primero
    (ver rgm_cola.solicitar_consulta): los avisos de quien pidió el
    Judicial deben seguir hasta que el Judicial encadenado termine, no
    quedarse colgados en el ítem RGM intermedio."""
    with get_connection() as conn:
        for aviso in conn.execute("SELECT * FROM rgm_avisos WHERE item_id = ?", (item_id_origen,)).fetchall():
            conn.execute(
                """INSERT INTO rgm_avisos (item_id, chat_id, usuario_id, alerta_chat_id, alerta_message_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (item_id_destino, aviso["chat_id"], aviso["usuario_id"], aviso["alerta_chat_id"], aviso["alerta_message_id"]),
            )


def avisos_con_espera_vencida(minutos: int = MINUTOS_AVISO_ESPERA_RGM) -> list:
    """Avisos de ítems que SIGUEN pendientes/en curso, pedidos hace más de
    `minutos` y que todavía no recibieron el aviso único de "sigue en
    cola" (Prompt 7 bloque A, punto 5)."""
    limite = (datetime.now() - timedelta(minutes=minutos)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT a.* FROM rgm_avisos a
               JOIN rgm_cola c ON c.id = a.item_id
               WHERE a.avisado_espera = 0 AND a.creado_en < ? AND c.estado IN ('PENDIENTE', 'EN_CURSO')""",
            (limite,),
        ).fetchall()
        return [dict(f) for f in filas]


def marcar_aviso_espera_notificado(aviso_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE rgm_avisos SET avisado_espera = 1 WHERE id = ?", (aviso_id,))


def actualizar_mensaje_espera_rgm(aviso_id: int, mensaje_espera_id: int):
    """El "🔎 Consultando…" se manda DESPUÉS de crear_aviso_rgm (hace
    falta el id del ítem/puesto en la cola para su texto); esto guarda su
    message_id ya mandado, para poder editarlo a los 3 min si hace falta."""
    with get_connection() as conn:
        conn.execute("UPDATE rgm_avisos SET mensaje_espera_id = ? WHERE id = ?", (mensaje_espera_id, aviso_id))


# ---------------------------------------------------------------------------
# Diagnóstico RGM (Prompt 7 bloque A, punto 6): archivo-de-pasos en la
# base (nunca Telegram), para la página "Consulta -> Diagnóstico".
# ---------------------------------------------------------------------------

def registrar_diagnostico_rgm(paso: str, item_id: int = None, placa: str = None, detalle: str = None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO rgm_diagnostico (item_id, placa, paso, detalle) VALUES (?, ?, ?, ?)",
            (item_id, placa, paso, detalle),
        )


def listar_diagnostico_rgm(limite: int = 50) -> list:
    with get_connection() as conn:
        filas = conn.execute(
            "SELECT * FROM rgm_diagnostico ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
        return [dict(f) for f in filas]


def registrar_pdf_proceso(placa: str, id_proceso, radicado, ruta: str) -> int:
    """Prompt 8d punto 3: deja constancia del PDF generado para que la
    ficha del vehículo pueda mostrar "ya generado" sin regenerarlo."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO procesos_pdf (placa, id_proceso, radicado, ruta) VALUES (?, ?, ?, ?)",
            (placa.strip().upper(), id_proceso, radicado, ruta),
        )
        return cur.lastrowid


def obtener_ultimo_pdf_proceso(placa: str, id_proceso):
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM procesos_pdf WHERE placa = ? AND id_proceso = ? ORDER BY id DESC LIMIT 1",
            (placa.strip().upper(), id_proceso),
        ).fetchone()
        return dict(fila) if fila else None


def listar_ultimos_items_rgm(limite: int = 20):
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT c.*, u.nombre AS solicitado_por_nombre FROM rgm_cola c
               LEFT JOIN usuarios u ON u.id = c.solicitado_por
               ORDER BY c.id DESC LIMIT ?""",
            (limite,),
        ).fetchall()
        return [dict(f) for f in filas]


_EXPR_ESTADO_INFORMACION = f"""
    CASE
        WHEN MAX(s.creado_en) IS NULL THEN 'SIN_DATOS'
        WHEN julianday('now', 'localtime') - julianday(MAX(s.creado_en)) <= {VIGENCIA_INFORMACION_DIAS}
            THEN 'ACTUALIZADA'
        ELSE 'PENDIENTE'
    END
"""


def listar_placas_gestion_masiva(
    fecha_referencia: str,
    mayor_o_igual: bool,
    q: str = "",
    filtro_estado: str = "",
    pagina: int = 1,
    por_pagina: int = 10,
):
    """
    Placas cuya fecha_ingreso cumpla la condición de período ya resuelta
    por gestion_masiva.calcular_condicion_periodo: >= fecha_referencia
    para los períodos "recientes" (último día/semana/15 días) o <=
    fecha_referencia para los períodos "3+/6+ meses" (antigüedad mínima).
    `mayor_o_igual` es un booleano controlado internamente (nunca texto
    de usuario) que decide cuál de los dos operadores usar en el SQL.

    Además aplica búsqueda de placa (parcial, insensible a mayúsculas) y
    filtro por estado de información calculado (ACTUALIZADA/PENDIENTE/
    SIN_DATOS a partir del snapshot más reciente, ver
    VIGENCIA_INFORMACION_DIAS). Paginado en SQL: nunca trae más filas de
    las que la página necesita.

    Devuelve (lista_de_placas, total_sin_paginar).
    """
    if filtro_estado and filtro_estado not in ESTADOS_INFORMACION_PLACA:
        raise ValorInvalidoError(f"Estado de información inválido: {filtro_estado}")

    operador = ">=" if mayor_o_igual else "<="
    q_norm = q.strip().upper()
    like_param = f"%{q_norm}%"

    base_sql = f"""
        SELECT v.*, MAX(s.creado_en) AS ultimo_snapshot,
               {_EXPR_ESTADO_INFORMACION} AS estado_informacion
        FROM vehiculos v
        LEFT JOIN snapshots_placa s ON s.placa = v.placa
        WHERE v.fecha_ingreso {operador} ?
          AND (? = '' OR UPPER(v.placa) LIKE ?)
        GROUP BY v.placa
        HAVING (? = '' OR estado_informacion = ?)
    """
    params_base = [fecha_referencia, q_norm, like_param, filtro_estado, filtro_estado]

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM ({base_sql})", params_base
        ).fetchone()[0]

        offset = max(pagina - 1, 0) * por_pagina
        filas = conn.execute(
            base_sql + " ORDER BY v.fecha_ingreso DESC LIMIT ? OFFSET ?",
            params_base + [por_pagina, offset],
        ).fetchall()

        return [dict(f) for f in filas], total


def listar_todas_las_placas_periodo(
    fecha_referencia: str, mayor_o_igual: bool, q: str = "", filtro_estado: str = ""
):
    """
    Como listar_placas_gestion_masiva pero SIN paginar y devolviendo solo
    las placas (no el vehículo completo): usada exclusivamente para armar
    la cola completa de un estudio masivo al iniciarlo (Etapa 5 §25/§26),
    donde sí hace falta el conjunto completo, no una página.
    """
    if filtro_estado and filtro_estado not in ESTADOS_INFORMACION_PLACA:
        raise ValorInvalidoError(f"Estado de información inválido: {filtro_estado}")

    operador = ">=" if mayor_o_igual else "<="
    q_norm = q.strip().upper()
    like_param = f"%{q_norm}%"

    sql = f"""
        SELECT v.placa, MAX(s.creado_en) AS ultimo_snapshot,
               {_EXPR_ESTADO_INFORMACION} AS estado_informacion
        FROM vehiculos v
        LEFT JOIN snapshots_placa s ON s.placa = v.placa
        WHERE v.fecha_ingreso {operador} ?
          AND (? = '' OR UPPER(v.placa) LIKE ?)
        GROUP BY v.placa
        HAVING (? = '' OR estado_informacion = ?)
        ORDER BY v.fecha_ingreso DESC
    """
    with get_connection() as conn:
        filas = conn.execute(sql, [fecha_referencia, q_norm, like_param, filtro_estado, filtro_estado]).fetchall()
        return [f["placa"] for f in filas]


def obtener_metricas_gestion_masiva(fecha_referencia: str, mayor_o_igual: bool, q: str = ""):
    """
    Conteos agregados (total/actualizadas/pendientes/sin_datos) para las
    tarjetas de métricas: reflejan el período + búsqueda, pero NO el
    filtro de estado (para poder mostrar el desglose completo sin que se
    colapse al aplicar ese mismo filtro).
    """
    operador = ">=" if mayor_o_igual else "<="
    q_norm = q.strip().upper()
    like_param = f"%{q_norm}%"

    sql = f"""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN estado_informacion = 'ACTUALIZADA' THEN 1 ELSE 0 END), 0) AS actualizadas,
            COALESCE(SUM(CASE WHEN estado_informacion = 'PENDIENTE' THEN 1 ELSE 0 END), 0) AS pendientes,
            COALESCE(SUM(CASE WHEN estado_informacion = 'SIN_DATOS' THEN 1 ELSE 0 END), 0) AS sin_datos
        FROM (
            SELECT v.placa, {_EXPR_ESTADO_INFORMACION} AS estado_informacion
            FROM vehiculos v
            LEFT JOIN snapshots_placa s ON s.placa = v.placa
            WHERE v.fecha_ingreso {operador} ?
              AND (? = '' OR UPPER(v.placa) LIKE ?)
            GROUP BY v.placa
        )
    """
    with get_connection() as conn:
        fila = conn.execute(sql, [fecha_referencia, q_norm, like_param]).fetchone()
        return dict(fila)


# ---------------------------------------------------------------------------
# Job Manager de estudios masivos (Etapa 5)
#
# NO hay ningún proceso en segundo plano: el progreso avanza por lotes
# acotados vía peticiones HTTP explícitas (gestion_masiva.py), nunca un
# loop gigante dentro de una sola petición (Etapa 5 §25).
# ---------------------------------------------------------------------------

def crear_estudio(admin_id: int, periodo: str, filtro_estado: str, busqueda: str, placas: list) -> int:
    """Crea un estudio y encola las placas dadas en el orden recibido
    (prioridad 0 = normal). Devuelve el id del estudio."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO estudios_masivos (admin_id, periodo, filtro_estado, busqueda, total_placas)
               VALUES (?, ?, ?, ?, ?)""",
            (admin_id, periodo, filtro_estado or None, busqueda or None, len(placas)),
        )
        estudio_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO cola_estudio (estudio_id, placa, orden) VALUES (?, ?, ?)",
            [(estudio_id, placa, i) for i, placa in enumerate(placas)],
        )
        return estudio_id


def obtener_estudio(estudio_id: int):
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM estudios_masivos WHERE id = ?", (estudio_id,)
        ).fetchone()
        return dict(fila) if fila else None


def obtener_estudio_mas_reciente():
    """El estudio más reciente del sistema (Gestión Masiva es una
    herramienta administrativa compartida, no hay una cola por Admin)."""
    with get_connection() as conn:
        fila = conn.execute(
            "SELECT * FROM estudios_masivos ORDER BY creado_en DESC, id DESC LIMIT 1"
        ).fetchone()
        return dict(fila) if fila else None


def listar_cola_estudio(estudio_id: int, limite: int = 200):
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT * FROM cola_estudio WHERE estudio_id = ?
               ORDER BY prioridad DESC, orden ASC LIMIT ?""",
            (estudio_id, limite),
        ).fetchall()
        return [dict(f) for f in filas]


def cambiar_estado_estudio(estudio_id: int, nuevo_estado: str):
    if nuevo_estado not in ESTADOS_ESTUDIO:
        raise ValorInvalidoError(f"Estado de estudio inválido: {nuevo_estado}")
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE estudios_masivos SET estado = ?, actualizado_en = datetime('now', 'localtime')
               WHERE id = ?""",
            (nuevo_estado, estudio_id),
        )
        if cur.rowcount == 0:
            raise RegistroNoEncontradoError(f"Estudio id={estudio_id} no existe")


def priorizar_placa_en_cola(estudio_id: int, placa: str) -> str:
    """
    Da prioridad alta a una placa dentro de la cola de un estudio (Etapa 5
    §24): si ya estaba encolada (en cualquier estado), sube su prioridad;
    si no estaba, la agrega al frente. Nunca modifica otras filas.
    Devuelve 'actualizada' o 'agregada'.
    """
    placa = placa.strip().upper()
    with get_connection() as conn:
        existente = conn.execute(
            "SELECT id FROM cola_estudio WHERE estudio_id = ? AND placa = ? AND estado = 'PENDIENTE'",
            (estudio_id, placa),
        ).fetchone()
        if existente:
            conn.execute(
                "UPDATE cola_estudio SET prioridad = 100 WHERE id = ?", (existente["id"],)
            )
            return "actualizada"

        orden_min = conn.execute(
            "SELECT COALESCE(MIN(orden), 0) AS m FROM cola_estudio WHERE estudio_id = ?",
            (estudio_id,),
        ).fetchone()["m"]
        conn.execute(
            """INSERT INTO cola_estudio (estudio_id, placa, prioridad, orden)
               VALUES (?, ?, 100, ?)""",
            (estudio_id, placa, orden_min - 1),
        )
        conn.execute(
            "UPDATE estudios_masivos SET total_placas = total_placas + 1 WHERE id = ?",
            (estudio_id,),
        )
        return "agregada"


def tomar_siguiente_lote(estudio_id: int, tamano: int):
    """Toma (sin marcar todavía) hasta `tamano` items PENDIENTE de la cola,
    en orden de prioridad y luego de encolado."""
    with get_connection() as conn:
        filas = conn.execute(
            """SELECT * FROM cola_estudio WHERE estudio_id = ? AND estado = 'PENDIENTE'
               ORDER BY prioridad DESC, orden ASC LIMIT ?""",
            (estudio_id, tamano),
        ).fetchall()
        return [dict(f) for f in filas]


def marcar_item_cola(item_id: int, nuevo_estado: str, error_detalle: str = None):
    if nuevo_estado not in ESTADOS_ITEM_COLA:
        raise ValorInvalidoError(f"Estado de item de cola inválido: {nuevo_estado}")
    with get_connection() as conn:
        conn.execute(
            """UPDATE cola_estudio
               SET estado = ?, intentos = intentos + 1,
                   procesado_en = datetime('now', 'localtime'), error_detalle = ?
               WHERE id = ?""",
            (nuevo_estado, error_detalle, item_id),
        )


def incrementar_contadores_estudio(estudio_id: int, procesadas_delta: int = 0, con_error_delta: int = 0):
    with get_connection() as conn:
        conn.execute(
            """UPDATE estudios_masivos
               SET procesadas = procesadas + ?, con_error = con_error + ?,
                   actualizado_en = datetime('now', 'localtime')
               WHERE id = ?""",
            (procesadas_delta, con_error_delta, estudio_id),
        )


# ---------------------------------------------------------------------------
# Punto de entrada manual: inicializa la DB al ejecutar el archivo directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    inicializar_db()
