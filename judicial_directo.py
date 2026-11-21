"""
judicial_directo.py
Prompt 8 (NEXCAM-PROMPTS-Y-PRUEBAS.md), sección "Hallazgos de las capturas
de red" (HAR de Gabriel, 20/11/2026): consultaprocesos.ramajudicial.gov.co
resultó ser una API JSON pública -- sin login, sin captcha, sin cookies,
con CORS "*" -- así que NEXORA-CONTROL la consulta DIRECTO por HTTP
(httpx), sin el Connector ni ninguna pestaña de Chrome. Funciona aunque
Brave esté cerrado, que es justo lo que pedía Gabriel ("ver todo en
NEXORA, sin que se abra, active ni cambie de pestaña nada").

Decisiones de esta fase:
- Límite de 1 petición cada 1,5 s (GLOBAL, no por placa: un lock + último
  momento usado), timeout de 20 s, 3 reintentos con espera creciente,
  User-Agent de navegador normal (nunca el de httpx por defecto, para no
  destacar como bot). El caché de 24 h ya existe (snapshots_placa +
  rgm_cola._snapshot_vigente): no se duplica aquí, vive en rgm_cola.py.
- `sujetosProcesales` viene como un solo texto separado por "|":
  "Demandante/accionante: X| Demandado/indiciado/causante: Y| Defensor
  Privado: Z" -- se parte por "|" y cada trozo por el PRIMER ":" (el rol
  puede traer su propio ":" más adentro en teoría, por eso "primero", no
  "único"). Nunca se inventa un sujeto: si no hay texto o no hay ":",
  ese trozo se descarta.
- `GET Proceso/Sujetos/{id}` no salió en la captura real de Gabriel: se
  intenta igual (podría existir) pero si falla (404/error) se cae sin
  problema al texto ya traído por el listado -- nunca rompe la consulta
  completa por esto.
- Nunca se guardan en el repo ni en los logs cookies/tokens: esta API no
  los usa (sin sesión), así que no aplica, pero se deja la nota por si
  algún día cambia.
"""

import re
import threading
import time
from typing import Optional

BASE_URL = "https://consultaprocesos.ramajudicial.gov.co:448/api/v2"
TIMEOUT_SEGUNDOS = 20.0
INTENTOS = 3
ESPERA_ENTRE_PETICIONES_SEGUNDOS = 1.5
ESPERA_REINTENTO_BASE_SEGUNDOS = 2.0
MAX_PAGINAS_ACTUACIONES = 10  # 40 por página (confirmado en la captura): 400 actuaciones, de sobra
MAX_PAGINAS_PROCESOS = 20     # tope defensivo: nunca recorrer sin límite una búsqueda con miles de homónimos

# Mismo User-Agent de navegador normal que ya usaba el Connector (no el
# de httpx por defecto, para no destacar como bot -- pedido explícito).
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_lock_ritmo = threading.Lock()
_ultima_peticion = 0.0


class ErrorJudicialDirecto(Exception):
    """Cualquier falla de red/portal al consultar la API judicial real --
    rgm_cola.marcar_error() la convierte en el aviso de "No se pudo
    consultar" que ya existe."""


def _esperar_turno():
    """Límite de 1 petición cada ESPERA_ENTRE_PETICIONES_SEGUNDOS, GLOBAL
    para todo el proceso (no por placa): varias consultas Judicial en
    curso a la vez se turnan, nunca llaman en paralelo."""
    global _ultima_peticion
    with _lock_ritmo:
        falta = ESPERA_ENTRE_PETICIONES_SEGUNDOS - (time.monotonic() - _ultima_peticion)
        if falta > 0:
            time.sleep(falta)
        _ultima_peticion = time.monotonic()


def _get(ruta: str, params: Optional[dict] = None) -> dict:
    import httpx

    url = f"{BASE_URL}/{ruta.lstrip('/')}"
    error_final = None
    for intento in range(1, INTENTOS + 1):
        _esperar_turno()
        try:
            with httpx.Client(timeout=TIMEOUT_SEGUNDOS, headers={"User-Agent": USER_AGENT}) as cliente:
                r = cliente.get(url, params=params or {})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            error_final = e
            if intento < INTENTOS:
                time.sleep(ESPERA_REINTENTO_BASE_SEGUNDOS * intento)
    raise ErrorJudicialDirecto(f"{ruta}: {error_final}")


def _parsear_sujetos(texto: Optional[str]) -> list:
    """"Demandante/accionante: X| Demandado/indiciado/causante: Y" ->
    [{"rol": "Demandante/accionante", "nombre": "X"}, ...]. Partir por
    "|" primero evita el bug real del parser viejo (basado en DOM, en
    judicial.js): ahí la frontera entre sujetos se adivinaba con un
    regex de mayúsculas que no reconocía roles con "/" ni minúsculas
    después de la barra ("Demandante/accionante:"), perdiendo ese sujeto
    por completo. Acá la frontera YA viene marcada por el portal, nada
    que adivinar."""
    if not texto:
        return []
    sujetos = []
    for trozo in texto.split("|"):
        trozo = trozo.strip()
        if not trozo:
            continue
        i = trozo.find(":")
        if i < 0:
            continue
        rol = trozo[:i].strip()
        nombre = trozo[i + 1:].strip()
        if rol and nombre:
            sujetos.append({"rol": rol, "nombre": nombre})
    return sujetos


def _normalizar_proceso(crudo: dict) -> dict:
    return {
        "radicado": crudo.get("llaveProceso"),
        "id_proceso": crudo.get("idProceso"),
        "id_conexion": crudo.get("idConexion"),
        "despacho": crudo.get("despacho"),
        "departamento": crudo.get("departamento"),
        "fecha_radicacion": crudo.get("fechaProceso"),
        "fecha_ultima_actuacion": crudo.get("fechaUltimaActuacion"),
        "privado": bool(crudo.get("esPrivado")),
        "sujetos": _parsear_sujetos(crudo.get("sujetosProcesales")),
    }


def buscar_procesos_por_nombre(nombre: str, tipo_persona: str = "nat") -> list:
    """GET Procesos/Consulta/NombreRazonSocial, recorriendo TODAS las
    páginas (Gabriel pidió "para todos los procesos: SoloActivos=false",
    a diferencia de la captura real que usó true). `tipo_persona`: "nat"
    (persona natural) o "jur" (persona jurídica) -- confirmado en los
    hallazgos que hace falta probar "jur" a mano antes de confiar en él
    para bancos/financieras; por ahora el llamador decide cuál usar."""
    procesos = []
    pagina = 1
    while True:
        datos = _get("Procesos/Consulta/NombreRazonSocial", {
            "nombre": nombre, "tipoPersona": tipo_persona, "SoloActivos": "false",
            "codificacionDespacho": "", "pagina": pagina,
        })
        for crudo in datos.get("procesos") or []:
            procesos.append(_normalizar_proceso(crudo))
        paginacion = datos.get("paginacion") or {}
        total_paginas = paginacion.get("cantidadPaginas") or 1
        if pagina >= total_paginas or pagina >= MAX_PAGINAS_PROCESOS:
            break
        pagina += 1
    return procesos


def obtener_detalle_proceso(id_proceso) -> dict:
    crudo = _get(f"Proceso/Detalle/{id_proceso}")
    return {
        "id_proceso": id_proceso,
        "radicado": crudo.get("llaveProceso"),
        "privado": bool(crudo.get("esPrivado")),
        "despacho": crudo.get("despacho"),
        "ponente": crudo.get("ponente"),
        "tipo_proceso": crudo.get("tipoProceso"),
        "clase_proceso": crudo.get("claseProceso"),
        "subclase_proceso": crudo.get("subclaseProceso"),
        "ubicacion": crudo.get("ubicacion"),
        "fecha_proceso": crudo.get("fechaProceso"),
        "fecha_consulta": crudo.get("fechaConsulta"),
        "ultima_actualizacion": crudo.get("ultimaActualizacion"),
    }


def obtener_actuaciones_proceso(id_proceso) -> list:
    actuaciones = []
    pagina = 1
    while True:
        datos = _get(f"Proceso/Actuaciones/{id_proceso}", {"pagina": pagina})
        for crudo in datos.get("actuaciones") or []:
            actuaciones.append({
                "fecha_actuacion": crudo.get("fechaActuacion"),
                "actuacion": crudo.get("actuacion"),
                "anotacion": crudo.get("anotacion"),
                "con_documentos": bool(crudo.get("conDocumentos")),
            })
        paginacion = datos.get("paginacion") or {}
        total_paginas = paginacion.get("cantidadPaginas") or 1
        if pagina >= total_paginas or pagina >= MAX_PAGINAS_ACTUACIONES:
            break
        pagina += 1
    # Más reciente arriba (el portal las entrega más vieja primero, ver
    # consActuacion ascendente en la captura).
    actuaciones.reverse()
    return actuaciones


def obtener_sujetos_proceso(id_proceso) -> Optional[list]:
    """GET Proceso/Sujetos/{id}: NO salió en la captura real de Gabriel
    (confirmar aún pendiente) -- se intenta igual porque el nombre del
    endpoint existe en la API v2 documentada por otros portales iguales;
    si no existe o falla, se devuelve None y el llamador usa el texto de
    sujetos que YA trajo el listado (nunca rompe la consulta por esto)."""
    try:
        crudo = _get(f"Proceso/Sujetos/{id_proceso}")
    except ErrorJudicialDirecto:
        return None
    lista = crudo if isinstance(crudo, list) else crudo.get("sujetos") or crudo.get("sujetosProcesales")
    if not lista:
        return None
    if isinstance(lista, str):
        return _parsear_sujetos(lista)
    return [{"rol": s.get("rol") or s.get("tipoSujeto"), "nombre": s.get("nombre") or s.get("razonSocial")}
            for s in lista if (s.get("rol") or s.get("tipoSujeto")) and (s.get("nombre") or s.get("razonSocial"))]


def consultar_judicial_por_nombre(nombre: str, tipo_persona: str = "nat") -> dict:
    """Punto de entrada único: arma el mismo shape que se guarda como
    snapshot `fuente='JUDICIAL'` y que consume nexcam_telegram.py/las
    plantillas de la ficha y Consulta (Prompt 8 punto 6)."""
    procesos = buscar_procesos_por_nombre(nombre, tipo_persona=tipo_persona)
    return {"nombre_consultado": nombre, "procesos": procesos}


# ---------------------------------------------------------------------------
# Prompt 8 punto 6: tarjetas legibles -- coincidencia con el acreedor del
# RGM y orden (financiera primero, luego más reciente arriba).
# ---------------------------------------------------------------------------

_SUFIJOS_FINANCIERO = (
    "COMPANIA DE FINANCIAMIENTO", "COMPAÑIA DE FINANCIAMIENTO", "SOCIEDAD ANONIMA",
    "S A S", "SAS", "S A", "SA", "BIC", "LTDA",
)


def normalizar_nombre_financiero(nombre: Optional[str]) -> str:
    """"BANCO FINANDINA SA BIC" y "BANCO FINANDINA S.A. BIC" deben dar la
    MISMA clave, para poder decidir si el demandante de un proceso
    judicial ES el acreedor del RGM de esa placa: sin tildes/puntos,
    mayúsculas, sin los sufijos societarios más comunes."""
    if not nombre:
        return ""
    txt = nombre.upper()
    equivalencias = str.maketrans("ÁÉÍÓÚÑ", "AEIOUN")
    txt = txt.translate(equivalencias).replace(".", "").replace(",", "")
    for sufijo in _SUFIJOS_FINANCIERO:
        txt = re.sub(rf"\b{re.escape(sufijo)}\b", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def ordenar_y_marcar_procesos(procesos: list, nombre_acreedor: Optional[str] = None) -> list:
    """Copia de `procesos` sin radicados repetidos, con "coincide_acreedor"
    (True si algún sujeto "...demandante..." normalizado coincide con el
    acreedor del RGM) y ordenada: esos primero, luego por fecha de
    última actuación más reciente arriba (dos sort() consecutivos y
    estables, para no tener que invertir una fecha-texto a mano)."""
    clave_acreedor = normalizar_nombre_financiero(nombre_acreedor) if nombre_acreedor else ""
    vistos = set()
    resultado = []
    for p in procesos:
        radicado = p.get("radicado")
        if radicado and radicado in vistos:
            continue
        if radicado:
            vistos.add(radicado)
        coincide = False
        if clave_acreedor:
            for s in p.get("sujetos") or []:
                rol = (s.get("rol") or "").lower()
                if "demandante" in rol and normalizar_nombre_financiero(s.get("nombre")) == clave_acreedor:
                    coincide = True
                    break
        resultado.append({**p, "coincide_acreedor": coincide})
    resultado.sort(key=lambda p: p.get("fecha_ultima_actuacion") or "", reverse=True)
    resultado.sort(key=lambda p: 0 if p["coincide_acreedor"] else 1)
    return resultado
