"""
templates.py
Instancia compartida de Jinja2Templates.

Existe solo para que usuarios.py, vehiculos.py, auditoria.py y main.py
reutilicen el mismo motor de plantillas (y su mismo caché) en vez de
crear un jinja2.Environment distinto por módulo.
"""

from datetime import datetime, timedelta, timezone

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

# Colombia no tiene horario de verano (fijo en UTC-5 desde 1993): offset
# fijo en vez de zoneinfo porque Windows no trae la base de datos de
# zonas horarias instalada por defecto y así no hace falta sumar tzdata
# como dependencia nueva (misma decisión que BOGOTA en nexcam_telegram.py).
_BOGOTA = timezone(timedelta(hours=-5))


def fecha_co(valor):
    """
    Filtro Jinja único para TODAS las fechas de la web (Hoy, Vehículos,
    ficha, NEXCAM, Consulta, Auditoría): formato colombiano
    "17/11/2026, 11:05 p. m.". Acepta lo que ya circula por NEXORA sin
    normalizar antes en cada vista:
    - texto "AAAA-MM-DD HH:MM:SS" (o solo "AAAA-MM-DD") ya en hora de
      Colombia -- la convención de toda la base (vehiculos.fecha_ingreso,
      logs_auditoria.fecha_hora, nexcam_avistamientos.recibido_en, etc.);
    - epoch-ms (int/float) como el que manda la app NEXCAM en `ts`;
    - un datetime de Python ya construido (p. ej. `ahora` en /hoy).
    Vacío/None -> "—"; si no se reconoce el formato, se muestra tal cual
    en vez de fallar la página entera por una fecha rara.
    """
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, (int, float)):
        momento = datetime.fromtimestamp(valor / 1000, tz=_BOGOTA)
    elif isinstance(valor, datetime):
        momento = valor if valor.tzinfo else valor.replace(tzinfo=_BOGOTA)
    else:
        texto = str(valor)[:19]
        momento = None
        for patron in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                momento = datetime.strptime(texto, patron).replace(tzinfo=_BOGOTA)
                break
            except ValueError:
                continue
        if momento is None:
            return str(valor)
    hora12 = momento.hour % 12 or 12
    meridiano = "a. m." if momento.hour < 12 else "p. m."
    return f"{momento:%d/%m/%Y}, {hora12}:{momento.minute:02d} {meridiano}"


templates.env.filters["fecha_co"] = fecha_co
