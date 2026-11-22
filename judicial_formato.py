"""
judicial_formato.py
Prompt 8d, punto 2/3: funciones PURAS (sin red, sin Telegram, sin base de
datos) para mostrar los procesos judiciales de forma legible -- probables
con datos ficticios escritos a mano, nunca contra el portal real.

Usa `judicial_directo.ordenar_y_marcar_procesos` (orden y "coincide_acreedor")
como entrada: aquí solo se formatea para mostrar (fechas, nombres, tarjetas,
partición en varios mensajes de Telegram, paginado de actuaciones y el PDF
del proceso completo).
"""

import re
from datetime import datetime
from typing import Optional

LIMITE_MENSAJE_TELEGRAM = 4096
ACTUACIONES_POR_PAGINA = 10
LIMITE_ANOTACION = 200

_SIGLAS_SOCIETARIAS = {"SA", "SAS", "BIC", "LTDA", "NIT", "EU", "SCS"}


def _esc(t) -> str:
    """Las piezas de este módulo se mandan directo como HTML de Telegram
    (<b>, <code>): todo dato que viene de la API judicial real (radicado,
    despacho, nombres...) se escapa antes de insertarlo, para no romper
    el parse_mode=HTML si alguna vez trae "&"/"<"/">"."""
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def formatear_fecha_corta(fecha) -> str:
    """"2026-07-22T00:00:00" / "2026-07-22" / "2026-07-22 00:00:00" ->
    "22/07/2026". Nunca "T00:00:00" en la vista (pedido de Gabriel).
    Vacío o formato no reconocido -> "—" (nunca lanza)."""
    if not fecha:
        return "—"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(fecha).strip())
    if not m:
        return "—"
    anio, mes, dia = m.groups()
    return f"{dia}/{mes}/{anio}"


def normalizar_nombre_visible(nombre: Optional[str]) -> str:
    """Nombre/juzgado con mayúscula inicial, siglas societarias (S.A.,
    S.A.S., BIC, LTDA...) tal cual, sin "Compañía de Financiamiento" ni
    dobles espacios -- el dato completo se guarda aparte (snapshots_placa),
    esto es solo la vista. "—" si no hay nombre."""
    if not nombre or not str(nombre).strip():
        return "—"
    txt = re.sub(r"\s+", " ", str(nombre).strip())
    txt = re.sub(r"(?i)\bcompa(?:ñ|n)[ií]a\s+de\s+financiamiento\b", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return "—"
    palabras = []
    for palabra in txt.split(" "):
        clave = palabra.upper().replace(".", "").rstrip(",")
        if clave in _SIGLAS_SOCIETARIAS:
            palabras.append(palabra.upper())
        elif len(palabra) <= 1:
            palabras.append(palabra.upper())
        else:
            palabras.append(palabra[0].upper() + palabra[1:].lower())
    return " ".join(palabras)


def rol_corto(rol: Optional[str]) -> Optional[str]:
    """"Demandante/accionante" -> "Demandante"; "Demandado/indiciado/
    causante" -> "Demandado"; cualquier otro rol (Defensor, Apoderado,
    Tercero...) -> None (se agrupa aparte en "Otros: …")."""
    r = (rol or "").lower()
    if "demandante" in r or "accionante" in r:
        return "Demandante"
    if "demandado" in r or "indiciado" in r or "causante" in r:
        return "Demandado"
    return None


def recortar_anotacion(texto: Optional[str], limite: int = LIMITE_ANOTACION) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + "…"


def _lugar(despacho: Optional[str], departamento: Optional[str]) -> str:
    despacho_v = _esc(normalizar_nombre_visible(despacho)) if despacho else "—"
    if departamento:
        return f"{despacho_v} · {_esc(normalizar_nombre_visible(departamento))}"
    return despacho_v


def _sujetos_por_rol_corto(sujetos: list) -> dict:
    """{"Demandante": "nombre1, nombre2", "Demandado": "...", "otros": [...]} --
    nombres ya escapados para HTML (ver _esc)."""
    agrupado = {"Demandante": [], "Demandado": [], "otros": []}
    for s in sujetos or []:
        corto = rol_corto(s.get("rol"))
        nombre = _esc(normalizar_nombre_visible(s.get("nombre")))
        if corto:
            agrupado[corto].append(nombre)
        else:
            rol_v = _esc(normalizar_nombre_visible(s.get("rol"))) if s.get("rol") else None
            agrupado["otros"].append(f"{rol_v}: {nombre}" if rol_v else nombre)
    return agrupado


def _tarjeta_proceso(numero: int, p: dict) -> str:
    if p.get("privado"):
        return f"🔒 Proceso privado · {_lugar(p.get('despacho'), p.get('departamento'))}"
    agrupado = _sujetos_por_rol_corto(p.get("sujetos") or [])
    lineas = [
        f"<b>{numero}.</b> <code>{_esc(p.get('radicado')) or '—'}</code>",
        f"🏛 {_lugar(p.get('despacho'), p.get('departamento'))}",
        f"📅 Radicado {formatear_fecha_corta(p.get('fecha_radicacion'))} · "
        f"última actuación {formatear_fecha_corta(p.get('fecha_ultima_actuacion'))}",
    ]
    if agrupado["Demandante"]:
        lineas.append(f"👤 Demandante: {', '.join(agrupado['Demandante'])}")
    if agrupado["Demandado"]:
        lineas.append(f"👤 Demandado: {', '.join(agrupado['Demandado'])}")
    if agrupado["otros"]:
        lineas.append(f"Otros: {', '.join(agrupado['otros'])}")
    return "\n".join(lineas)


def construir_tarjetas_judicial(placa: str, procesos_ordenados: list, fecha_consulta: Optional[datetime] = None) -> dict:
    """
    `procesos_ordenados` ya viene de
    judicial_directo.ordenar_y_marcar_procesos (financiera primero, luego
    más reciente arriba; sin radicados repetidos). Devuelve:
    {"piezas": [texto, ...], "numero_por_id_proceso": {id_proceso: N}}
    (piezas: bloques de texto que NUNCA deben partirse a la mitad al
    armar los mensajes -- ver particionar_mensajes_telegram).
    """
    fecha_consulta = fecha_consulta or datetime.now()
    financiera = [p for p in procesos_ordenados if p.get("coincide_acreedor")]
    otros = [p for p in procesos_ordenados if not p.get("coincide_acreedor")]

    piezas = [
        f"⚖️ <b>Procesos de {_esc(placa)} · {len(procesos_ordenados)} encontrados · "
        f"consulta del {fecha_consulta.strftime('%d/%m/%Y')}</b>"
    ]
    numero_por_id_proceso = {}
    numero = 0

    for etiqueta, grupo in (("🏦 De la financiera", financiera), ("📁 Otros", otros)):
        piezas.append(f"{etiqueta} ({len(grupo)})")
        for p in grupo:
            numero += 1
            if not p.get("privado") and p.get("id_proceso") is not None:
                numero_por_id_proceso[p["id_proceso"]] = numero
            piezas.append(_tarjeta_proceso(numero, p))

    return {"piezas": piezas, "numero_por_id_proceso": numero_por_id_proceso}


def particionar_mensajes_telegram(piezas: list, limite: int = LIMITE_MENSAJE_TELEGRAM) -> list:
    """Junta `piezas` (bloques que nunca se parten) en mensajes separados
    por "\\n\\n", cada uno de a lo sumo `limite` caracteres. Una pieza más
    grande que `limite` (no debería pasar con tarjetas normales) queda
    sola en su propio mensaje en vez de lanzar."""
    mensajes = []
    actual = []
    largo_actual = 0
    for pieza in piezas:
        largo_pieza = len(pieza)
        separador = 2 if actual else 0  # "\n\n"
        if actual and largo_actual + separador + largo_pieza > limite:
            mensajes.append("\n\n".join(actual))
            actual = []
            largo_actual = 0
        actual.append(pieza)
        largo_actual += (2 if len(actual) > 1 else 0) + largo_pieza
    if actual:
        mensajes.append("\n\n".join(actual))
    return mensajes or [""]


def paginar(lista: list, pagina: int, por_pagina: int = ACTUACIONES_POR_PAGINA) -> dict:
    """1-based. Nunca lanza con páginas fuera de rango: se recorta al
    rango válido [1, total_paginas] (total_paginas >= 1 aunque la lista
    esté vacía, para no mostrar "página 0 de 0")."""
    total = len(lista)
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = max(1, min(pagina, total_paginas))
    inicio = (pagina - 1) * por_pagina
    return {
        "items": lista[inicio:inicio + por_pagina],
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
    }


def texto_actuacion(a: dict, recortar: bool = True) -> str:
    anotacion = recortar_anotacion(a.get("anotacion")) if recortar else (a.get("anotacion") or "").strip()
    lineas = [f"📌 {formatear_fecha_corta(a.get('fecha_actuacion'))} · {_esc(a.get('actuacion')) or '—'}"]
    if anotacion:
        lineas.append(f"   {_esc(anotacion)}")
    if a.get("con_documentos"):
        lineas.append("   📎 con documentos")
    return "\n".join(lineas)


def texto_detalle_proceso(detalle: dict) -> str:
    lineas = [
        f"<code>{_esc(detalle.get('radicado')) or '—'}</code>",
        f"🏛 {_lugar(detalle.get('despacho'), None)}",
    ]
    if detalle.get("ponente"):
        lineas.append(f"Ponente: {_esc(normalizar_nombre_visible(detalle.get('ponente')))}")
    tipo = detalle.get("tipo_proceso")
    clase = detalle.get("clase_proceso")
    if tipo or clase:
        lineas.append(f"Tipo: {_esc(tipo) or '—'}{(' · ' + _esc(clase)) if clase else ''}")
    if detalle.get("ubicacion"):
        lineas.append(f"Ubicación: {_esc(normalizar_nombre_visible(detalle.get('ubicacion')))}")
    lineas.append(f"Radicado el {formatear_fecha_corta(detalle.get('fecha_proceso'))}")
    return "\n".join(lineas)
