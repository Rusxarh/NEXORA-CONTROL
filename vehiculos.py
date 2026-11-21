"""
vehiculos.py
Módulo de vehículos (placas).

Responsabilidades:
- Alta individual y alta masiva de placas: normaliza a mayúsculas y
  valida el formato (regex) ANTES de tocar la base de datos. La capa de
  datos (database.py) se mantiene "tonta": solo persiste y valida
  duplicados/tipo_vehiculo, la regla de formato vive aquí.
- Baja manual de una placa: procesa la confirmación del modal de
  index.html y exige que quien confirma sea un Admin autenticado.
- Inventario con búsqueda y filtro por estado (todos/activo/baja).
- Carga masiva de placas (solo Admin).
- Consulta de una placa (ficha, solo lectura) e Historial de esa placa
  (reutiliza logs_auditoria) — abiertas a cualquier usuario autenticado.

REGLA INVIOLABLE (heredada de la versión original): ninguna baja de
placa se ejecuta automáticamente. El backend rechaza cualquier
solicitud de baja que no incluya el campo oculto `confirmar` con el
valor exacto "SI" (ese campo únicamente lo produce el modal, tras un
clic explícito del usuario en "Confirmar baja"), sin importar el rol.

REGLA DE SEGURIDAD (Etapa 1): quién da de alta o de baja una placa ya
NO se toma de un campo de formulario `usuario_id` (ese campo se
eliminó de los templates): se toma siempre de `usuario_actual`, resuelto
por `usuarios.requerir_autenticacion` / `usuarios.requerir_admin` a
partir de la sesión de servidor.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request

import database as db
import usuarios
from templates import templates

router = APIRouter()

# Mismo patrón que el atributo `pattern` del <input name="placa"> en
# index.html: letras, números y guiones, 5 a 10 caracteres. Repetirlo
# aquí evita que una petición que no pase por el navegador (curl, un
# bot, etc.) se salte la validación del formulario.
PATRON_PLACA = re.compile(r"^[A-Za-z0-9\-]{5,10}$")


def _avisar_si_ya_vista(placas: list, origen: str):
    """
    Prompt 6 punto 7: "Ya la viste" completo -- cuando entra a la lista
    una placa que la cámara ya vio antes (también en lecturas "sin
    novedad": nexcam_db.vistas_de busca en TODAS, sin filtrar por
    en_lista), se avisa por el mismo camino que ya usaba la carga masiva
    (evento retro.hits -> nexcam_telegram, formato único del punto 1,
    solo a chats de Admin). Reutilizada por la alta individual (web y
    Telegram); la carga masiva usa su propio guardar_cruce_lista_dia
    porque además deja la fila para la pestaña "Ya vistas" del panel.
    Nunca debe romper el alta en sí.
    """
    try:
        import nexcam_api
        import nexcam_db

        vistas = nexcam_db.vistas_de(placas)
        if vistas:
            nexcam_api.broadcast("retro.hits", {"origen": origen, "hits": list(vistas.values())})
    except Exception:
        pass


def normalizar_y_validar_placa(placa: str) -> str:
    """Pone la placa en mayúsculas y valida su formato. Lanza
    db.ValorInvalidoError si no cumple el patrón esperado."""
    placa = placa.strip().upper()
    if not PATRON_PLACA.match(placa):
        raise db.ValorInvalidoError(
            "El formato de la placa no es válido. Usa solo letras, números "
            "y guiones (5 a 10 caracteres)."
        )
    return placa


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------

@router.get("/")
def index(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
    filtro: str = "todos",
    q: str = "",
    mensaje: Optional[str] = None,
    tipo: str = "info",
):
    # "todos" no es un estado real de la tabla: solo activo/baja filtran en SQL.
    estado_filtro = filtro if filtro in db.ESTADOS_VEHICULO else None
    vehiculos = db.buscar_vehiculos(query=q.strip() or None, estado=estado_filtro)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "vehiculos": vehiculos,
            "filtro": filtro,
            "q": q,
            "tipos_vehiculo": db.TIPOS_VEHICULO,
            "usuario_actual": usuario_actual,
            "mensaje": mensaje,
            "tipo_mensaje": tipo,
            "ruta_activa": "vehiculos",
        },
    )


# ---------------------------------------------------------------------------
# Consulta de placas (solo lectura) — Etapa 4
# ---------------------------------------------------------------------------

@router.get("/consulta")
def consulta_vista(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
    q: str = "",
    mensaje: Optional[str] = None,
    tipo: str = "info",
):
    buscado = bool(q.strip())
    placa_normalizada = q.strip().upper() if buscado else ""
    vehiculo = db.obtener_ficha_vehiculo(q) if buscado else None

    # FASE 6 (RGM/Judicial reales vía NEXORA-CONNECTOR): import perezoso
    # para no crear un ciclo de imports con rgm_cola/nexcam_api al cargar
    # el módulo.
    import rgm_cola

    snapshot_rgm = db.obtener_ultimo_snapshot_por_fuente(placa_normalizada, "RGM") if buscado else None
    snapshot_judicial = db.obtener_ultimo_snapshot_por_fuente(placa_normalizada, "JUDICIAL") if buscado else None
    return templates.TemplateResponse(
        request,
        "consulta.html",
        {
            "request": request,
            "usuario_actual": usuario_actual,
            "q": q,
            "buscado": buscado,
            "vehiculo": vehiculo,
            "ruta_activa": "consulta",
            "mensaje": mensaje,
            "tipo_mensaje": tipo,
            "snapshot_rgm": snapshot_rgm,
            "rgm_datos": _datos_rgm_para_ficha(snapshot_rgm),
            "snapshot_judicial": snapshot_judicial,
            "judicial_datos": _datos_judicial_para_vista(snapshot_judicial, snapshot_rgm),
            "cola_activa": db.listar_items_rgm_activos(),
            "ultimos_items_rgm": db.listar_ultimos_items_rgm(20),
            "trabajador_rgm_conectado": rgm_cola.trabajador_conectado(),
        },
    )


@router.get("/consulta/autocompletar")
def consulta_autocompletar(
    q: str = "",
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
):
    """
    Autocompletado de la pantalla de Consulta: devuelve JSON (no HTML) con
    las placas que EMPIEZAN por `q`, buscando en la base real (sin crear
    ninguna tabla/base nueva, sin tocar RGM ni Judicial). Misma sesión de
    autenticación que el resto de la app -- sin mecanismo nuevo.
    """
    coincidencias = db.buscar_placas_por_prefijo(q, limite=10)
    return {"coincidencias": coincidencias}


# ---------------------------------------------------------------------------
# Historial de una placa (solo lectura) — Etapa 4
#
# Reutiliza logs_auditoria vía db.listar_logs_de_placa: NO es lo mismo que
# la Auditoría general (esa responde "¿qué pasó en el sistema?"; esto
# responde "¿qué pasó con ESTA placa?"), pero ambas leen la misma tabla,
# sin duplicar datos ni crear una tabla de historial separada.
# ---------------------------------------------------------------------------

@router.get("/historial/{placa}")
def historial_vista(
    request: Request,
    placa: str,
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
):
    vehiculo = db.buscar_placa(placa)
    logs = db.listar_logs_de_placa(placa) if vehiculo else []

    return templates.TemplateResponse(
        request,
        "historial.html",
        {
            "request": request,
            "usuario_actual": usuario_actual,
            "placa_consultada": placa.strip().upper(),
            "vehiculo": vehiculo,
            "logs": logs,
            "ruta_activa": "historial",
        },
    )


@router.post("/vehiculos/alta")
def alta_vehiculo(
    placa: str = Form(...),
    tipo_vehiculo: str = Form(...),
    observaciones: str = Form(""),
    # Etapa 4.5: el alta individual queda restringida a Admin ("por ahora
    # NO conceder Alta individual al Operador", spec Etapa 4.5 §8). Antes
    # de esta etapa cualquier usuario autenticado podía registrar placas;
    # es un cambio de comportamiento deliberado, no un descuido.
    usuario_actual: dict = Depends(usuarios.requerir_admin),
):
    observaciones = observaciones.strip() or None

    try:
        placa = normalizar_y_validar_placa(placa)
        db.insertar_placa(placa, tipo_vehiculo, usuario_actual["id"], observaciones)
        mensaje = f"La placa {placa} fue registrada correctamente."
        tipo = "exito"
        _avisar_si_ya_vista([placa], origen="panel")
    except db.PlacaDuplicadaError as e:
        mensaje, tipo = str(e), "error"
    except db.ValorInvalidoError as e:
        mensaje, tipo = str(e), "error"

    return usuarios.redirigir_con_mensaje("/", mensaje, tipo)


@router.post("/vehiculos/baja")
def dar_de_baja(
    placa: str = Form(...),
    observaciones: str = Form(""),
    confirmar: str = Form(""),
    usuario_actual: dict = Depends(usuarios.requerir_admin),
):
    observaciones = observaciones.strip() or None

    # Prompt 6 punto 5: el botón "Dar de baja" ahora vive en la ficha del
    # vehículo (ya no en la fila de la tabla de Vehículos), así que tras
    # confirmar se vuelve a esa misma ficha, no al listado general.
    placa = placa.strip().upper()
    volver = f"/vehiculos/{placa}"

    if confirmar != "SI":
        # Nunca se ejecuta cambiar_estado_a_baja sin esta confirmación
        # explícita, ni siquiera para un Admin.
        mensaje = "Baja rechazada: no se recibió confirmación manual explícita."
        return usuarios.redirigir_con_mensaje(volver, mensaje, "error")

    try:
        db.cambiar_estado_a_baja(placa, usuario_actual["id"], observaciones)
        mensaje = f"La placa {placa} fue dada de baja correctamente."
        tipo = "exito"
    except db.RegistroNoEncontradoError as e:
        mensaje, tipo = str(e), "error"
    except db.ValorInvalidoError as e:
        mensaje, tipo = str(e), "error"

    return usuarios.redirigir_con_mensaje(volver, mensaje, tipo)


# ---------------------------------------------------------------------------
# Carga masiva de placas (solo Admin)
# ---------------------------------------------------------------------------

@router.get("/vehiculos/carga-masiva")
def carga_masiva_vista(
    request: Request,
    usuario_actual: dict = Depends(usuarios.requerir_admin),
    mensaje: Optional[str] = None,
    tipo: str = "info",
):
    return templates.TemplateResponse(
        request,
        "carga_masiva.html",
        {
            "request": request,
            "tipos_vehiculo": db.TIPOS_VEHICULO,
            "usuario_actual": usuario_actual,
            "mensaje": mensaje,
            "tipo_mensaje": tipo,
            # "gestion_masiva" (no "carga_masiva"): Carga masiva ahora vive
            # dentro del módulo único "Gestión masiva de placas" en el
            # sidebar (ver base.html/_tabs_gestion_masiva.html); no tiene
            # su propia entrada de sidebar independiente.
            "ruta_activa": "gestion_masiva",
        },
    )


def procesar_carga_masiva_texto(lineas: str, usuario_id: int) -> dict:
    """
    Lógica pura de la carga masiva (sin HTTP): usada por la ruta web de
    abajo y por el comando /cargar del bot de Telegram (nexcam_telegram.py,
    FASE 3), para no duplicar el parseo ni el cruce con NEXCAM en dos
    lugares. Devuelve {"insertadas", "duplicadas", "invalidas"} igual que
    antes de esta extracción (mismo formato, mismo comportamiento).
    """
    placas_a_insertar = []
    formato_invalido = []

    for linea in lineas.splitlines():
        linea = linea.strip()
        if not linea:
            continue

        partes = [p.strip() for p in linea.split(",")]
        placa_cruda = partes[0] if partes else ""
        if not placa_cruda:
            continue

        tipo_vehiculo = partes[1].lower() if len(partes) > 1 and partes[1] else "carro"
        observaciones = partes[2] if len(partes) > 2 and partes[2] else None

        try:
            placa = normalizar_y_validar_placa(placa_cruda)
        except db.ValorInvalidoError:
            formato_invalido.append(placa_cruda.strip().upper())
            continue

        placas_a_insertar.append(
            {
                "placa": placa,
                "tipo_vehiculo": tipo_vehiculo,
                "observaciones": observaciones,
            }
        )

    if placas_a_insertar:
        resumen = db.insertar_placas_masivo(placas_a_insertar, usuario_id)
    else:
        resumen = {"insertadas": [], "duplicadas": [], "invalidas": []}

    resumen["invalidas"] = resumen["invalidas"] + formato_invalido

    # Integración NEXCAM (FASE 2/3): cruza esta carga con el historial de
    # avistamientos de la cámara ("¿ya la vimos?"), lo guarda para la
    # pestaña "Ya vistas" del panel y avisa por SSE/Telegram. Nunca debe
    # romper la carga masiva en sí.
    try:
        import nexcam_api
        import nexcam_db

        placas_de_esta_carga = [item["placa"] for item in placas_a_insertar]
        if placas_de_esta_carga:
            cruce = nexcam_db.guardar_cruce_lista_dia(placas_de_esta_carga)
            resumen["ya_vistas"] = cruce["ya_vistas"]
            if cruce["ya_vistas"]:
                nexcam_api.broadcast("retro.hits", {"origen": "panel", "hits": list(cruce["ya_vistas"].values())})
    except Exception:
        resumen["ya_vistas"] = {}

    return resumen


@router.post("/vehiculos/carga-masiva")
def carga_masiva_procesar(
    lineas: str = Form(...),
    usuario_actual: dict = Depends(usuarios.requerir_admin),
):
    if not lineas.strip():
        return usuarios.redirigir_con_mensaje(
            "/vehiculos/carga-masiva",
            "No se encontró ninguna placa válida para procesar.",
            "error",
        )

    resumen = procesar_carga_masiva_texto(lineas, usuario_actual["id"])

    if not resumen["insertadas"] and not resumen["invalidas"] and not resumen["duplicadas"]:
        return usuarios.redirigir_con_mensaje(
            "/vehiculos/carga-masiva",
            "No se encontró ninguna placa válida para procesar.",
            "error",
        )

    partes_mensaje = [f"{len(resumen['insertadas'])} insertada(s)"]
    if resumen["duplicadas"]:
        partes_mensaje.append(
            f"{len(resumen['duplicadas'])} duplicada(s): {', '.join(resumen['duplicadas'])}"
        )
    if resumen["invalidas"]:
        partes_mensaje.append(
            f"{len(resumen['invalidas'])} inválida(s): {', '.join(resumen['invalidas'])}"
        )

    if resumen["insertadas"] and not resumen["duplicadas"] and not resumen["invalidas"]:
        tipo_mensaje = "exito"
    elif not resumen["insertadas"]:
        tipo_mensaje = "error"
    else:
        tipo_mensaje = "info"

    mensaje = "Carga masiva: " + " · ".join(partes_mensaje)

    return usuarios.redirigir_con_mensaje("/vehiculos/carga-masiva", mensaje, tipo_mensaje)


# ---------------------------------------------------------------------------
# Ficha de Vehículos (FASE 7): consolida ficha + avistamientos de cámara +
# RGM/Judicial + auditoría de ESA placa. Registrada AL FINAL del archivo
# a propósito: "/vehiculos/{placa}" es una ruta comodín que, si se
# registrara antes de "/vehiculos/alta"/"baja"/"carga-masiva", las
# "atraparía" a ellas también (FastAPI empareja por orden de registro).
# ---------------------------------------------------------------------------

def _datos_rgm_para_ficha(snapshot_rgm: Optional[dict]) -> Optional[dict]:
    """
    Prompt 5 punto 3 + Prompt 6 punto 2: la caja "Datos RGM" ya NO repite
    el vehículo (eso ahora vive en el bloque principal, leído directo de
    `vehiculos` -- la misma tabla que el RGM sobrescribe): esta caja solo
    trae acreedor/deudor (sensibles, Etapa 5 §2/§37: la plantilla decide
    si mostrarlos según el rol) y el JSON crudo detrás de "Ver datos
    originales". Si el JSON no se puede leer (snapshot viejo/corrupto),
    devuelve None y la plantilla cae al texto crudo tal cual.
    """
    import json

    if not snapshot_rgm:
        return None
    try:
        crudo = json.loads(snapshot_rgm["datos"])
    except Exception:
        return None

    deudor = crudo.get("deudor_garante") or {}
    documento = " ".join(x for x in (deudor.get("tipo_identificacion"), deudor.get("numero_identificacion")) if x)
    return {
        "acreedor": (crudo.get("acreedor") or {}).get("razon_social"),
        "deudor_nombre": deudor.get("nombre"),
        "deudor_documento": documento or None,
        "deudor_ciudad": deudor.get("ciudad"),
        "crudo": snapshot_rgm["datos"],
    }


def _datos_judicial_para_vista(snapshot_judicial: Optional[dict], snapshot_rgm: Optional[dict]) -> Optional[dict]:
    """
    Prompt 8 punto 6: "Último resultado guardado"/ficha ya NO muestran el
    JSON crudo de Judicial -- tarjetas legibles (judicial_tarjetas en
    _macros.html), ordenadas y marcadas con judicial_directo.ordenar_y_marcar_procesos
    (financiera primero, según el acreedor del RGM de esa misma placa).
    Si el JSON no se puede leer, devuelve None y la plantilla cae al
    texto crudo tal cual.
    """
    import json

    import judicial_directo

    if not snapshot_judicial:
        return None
    try:
        crudo = json.loads(snapshot_judicial["datos"])
    except Exception:
        return None

    acreedor = None
    if snapshot_rgm:
        try:
            acreedor = (json.loads(snapshot_rgm["datos"]).get("acreedor") or {}).get("razon_social")
        except Exception:
            acreedor = None

    return {
        "procesos": judicial_directo.ordenar_y_marcar_procesos(crudo.get("procesos") or [], acreedor),
        "crudo": snapshot_judicial["datos"],
    }


@router.get("/vehiculos/{placa}")
def vehiculo_ficha_vista(
    request: Request,
    placa: str,
    usuario_actual: dict = Depends(usuarios.requerir_autenticacion),
    mensaje: Optional[str] = None,
    tipo: str = "info",
):
    import camara
    import nexcam_db

    placa_normalizada = placa.strip().upper()
    vehiculo = db.obtener_ficha_vehiculo(placa_normalizada)
    grado_placa = camara.calcular_grado_placa(vehiculo["fecha_ingreso"]) if vehiculo else None
    vistas = nexcam_db.vistas_de([placa_normalizada]).get(placa_normalizada)
    snapshot_rgm = db.obtener_ultimo_snapshot_por_fuente(placa_normalizada, "RGM")
    snapshot_judicial = db.obtener_ultimo_snapshot_por_fuente(placa_normalizada, "JUDICIAL")

    return templates.TemplateResponse(
        request,
        "vehiculo_ficha.html",
        {
            "request": request,
            "usuario_actual": usuario_actual,
            "ruta_activa": "vehiculos",
            "placa": placa_normalizada,
            "vehiculo": vehiculo,
            "grado_placa": grado_placa,
            "avistamientos": (vistas or {}).get("vistas", []),
            "snapshot_rgm": snapshot_rgm,
            "rgm_datos": _datos_rgm_para_ficha(snapshot_rgm),
            "snapshot_judicial": snapshot_judicial,
            "judicial_datos": _datos_judicial_para_vista(snapshot_judicial, snapshot_rgm),
            "logs": db.listar_logs_de_placa(placa_normalizada) if vehiculo else [],
            "mensaje": mensaje,
            "tipo_mensaje": tipo,
        },
    )
