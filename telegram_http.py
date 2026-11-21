"""
telegram_http.py
Prompt 6c punto 1: causa real de TODA la demora de Telegram (botones,
alertas, "Ya la viste", cámara encendida/apagada) -- el log de Gabriel
mostró TIEMPO_BOTON ack≈21,7 s y total≈43,4 s (= 2 × 21,7 s) en TODOS los
toques. 21 s es exactamente lo que Windows tarda en darse por vencido
abriendo una conexión TCP (reintentos a 3+6+12 s): cada llamada a
api.telegram.org con urllib.request abría una conexión NUEVA, intentaba
primero una dirección (casi seguro IPv6, que la red de Gabriel no tiene),
esperaba 21 s y recién entonces caía a IPv4.

Arreglo, sin dependencias nuevas (solo librería estándar):
- Fuerza IPv4 al resolver api.telegram.org (socket.getaddrinfo con
  family=AF_INET): la dirección IPv6 inalcanzable ni siquiera se
  intenta, así que no hay nada que esperar 21 s.
- Reutiliza la conexión (keep-alive, una por hilo): getUpdates, envíos
  normales y envíos prioritarios/botones cada uno con la suya; si se cae,
  se reconecta sola en el siguiente intento.
- Timeout de conexión 5 s; de lectura 35 s (pensado para getUpdates, que
  usa un long poll real) o 15 s (el resto, vía `timeout_lectura`).
- TG_FORZAR_IPV4=1 por defecto; TG_FORZAR_IPV4=0 para desactivarlo (por
  si algún día la red de Gabriel sí tiene IPv6 andando).
- Log por llamada (TIEMPO_TG): método, tiempo de conexión y tiempo total.
"""

import http.client
import os
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Optional

FORZAR_IPV4 = os.environ.get("TG_FORZAR_IPV4", "1") != "0"
TIMEOUT_CONEXION_SEGUNDOS = 5.0
TIMEOUT_LECTURA_GETUPDATES_SEGUNDOS = 35.0
TIMEOUT_LECTURA_NORMAL_SEGUNDOS = 15.0

_contexto_tls = ssl.create_default_context()
_almacen_local = threading.local()


def _conectar_socket_ipv4(host: str, port: int, timeout: float) -> socket.socket:
    """socket.create_connection() de la librería estándar ya hace esto
    mismo (probar cada dirección que devuelva getaddrinfo), pero probaría
    TAMBIÉN la IPv6 inalcanzable si family=AF_UNSPEC (el comportamiento
    por defecto, y el que tenía urllib.request). Pedir family=AF_INET acá
    hace que esa dirección ni siquiera aparezca en la lista: no hay nada
    que esperar 21 s."""
    family = socket.AF_INET if FORZAR_IPV4 else socket.AF_UNSPEC
    ultimo_error: Optional[BaseException] = None
    for familia, tipo, proto, _, direccion in socket.getaddrinfo(host, port, family, socket.SOCK_STREAM):
        s = socket.socket(familia, tipo, proto)
        try:
            s.settimeout(timeout)
            s.connect(direccion)
            return s
        except OSError as e:
            ultimo_error = e
            s.close()
    raise ultimo_error or OSError(f"No se pudo resolver/conectar a {host}:{port}")


class _ConexionPersistente:
    """Una conexión (HTTPS o HTTP) que se reutiliza entre llamadas
    (keep-alive) y se reconecta sola si el servidor la cerró
    (RemoteDisconnected/ConnectionReset/timeout) -- esto es lo que evita
    abrir una conexión TCP+TLS nueva (con su propio intento de IPv6) en
    cada llamada."""

    def __init__(self, host: str, port: int, usar_tls: bool = True):
        self.host = host
        self.port = port
        self.usar_tls = usar_tls
        self._conn: Optional[http.client.HTTPConnection] = None

    def _nueva_conexion(self, timeout_conexion: float) -> http.client.HTTPConnection:
        sock = _conectar_socket_ipv4(self.host, self.port, timeout_conexion)
        if self.usar_tls:
            sock = _contexto_tls.wrap_socket(sock, server_hostname=self.host)
        conn = http.client.HTTPConnection(self.host, self.port)
        conn.sock = sock
        return conn

    def peticion(self, metodo: str, ruta: str, cuerpo=None, headers: Optional[dict] = None,
                 timeout_conexion: float = TIMEOUT_CONEXION_SEGUNDOS,
                 timeout_lectura: float = TIMEOUT_LECTURA_NORMAL_SEGUNDOS):
        """Devuelve (status, bytes_respuesta). Reintenta UNA vez con una
        conexión nueva si la que había (de una llamada anterior) ya no
        sirve -- nunca dos veces: un error real del servidor debe
        propagarse, no quedar escondido detrás de reintentos silenciosos."""
        headers = dict(headers or {})  # http.client ya pone el Host solo
        ultimo_error = None
        for intento in range(2):
            if self._conn is None:
                self._conn = self._nueva_conexion(timeout_conexion)
            try:
                self._conn.sock.settimeout(timeout_lectura)
                self._conn.request(metodo, ruta, body=cuerpo, headers=headers)
                resp = self._conn.getresponse()
                datos = resp.read()
                return resp.status, datos
            except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError,
                    BrokenPipeError, socket.timeout, OSError) as e:
                ultimo_error = e
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        raise ultimo_error or OSError("No se pudo completar la petición a Telegram")


def _conexion_por_hilo(host: str, port: int, usar_tls: bool) -> _ConexionPersistente:
    """Una conexión persistente POR HILO (el pedido de Gabriel: getUpdates,
    envíos normales y envíos de botones cada uno con la suya, para que una
    lenta no bloquee a las demás) y por host:puerto (api.telegram.org de
    la API normal vs. el dominio de descarga de archivos, si llegaran a
    diferir, o el servidor simulado de las pruebas)."""
    conexiones = getattr(_almacen_local, "conexiones", None)
    if conexiones is None:
        conexiones = {}
        _almacen_local.conexiones = conexiones
    clave = (host, port, usar_tls)
    conexion = conexiones.get(clave)
    if conexion is None:
        conexion = _ConexionPersistente(host, port, usar_tls)
        conexiones[clave] = conexion
    return conexion


def reiniciar_conexiones_del_hilo_actual() -> None:
    """Solo para pruebas: olvida las conexiones persistentes del hilo
    actual, para que la siguiente petición vuelva a resolver/conectar."""
    _almacen_local.conexiones = {}


def peticion(url: str, metodo: str = "POST", cuerpo=None, headers: Optional[dict] = None,
             timeout_lectura: float = TIMEOUT_LECTURA_NORMAL_SEGUNDOS, log=print, etiqueta: str = "") -> tuple:
    """Punto de entrada único -- `url` es la URL completa (como la que
    recibía urllib.request.Request antes). Mide y registra TIEMPO_TG
    (método, tiempo de conexión, tiempo total) y devuelve
    (status, bytes_respuesta)."""
    partes = urllib.parse.urlsplit(url)
    usar_tls = partes.scheme != "http"
    host = partes.hostname
    port = partes.port or (443 if usar_tls else 80)
    ruta = partes.path or "/"
    if partes.query:
        ruta += f"?{partes.query}"

    t0 = time.monotonic()
    conexion = _conexion_por_hilo(host, port, usar_tls)
    ya_habia_conexion = conexion._conn is not None
    t_conexion = None
    try:
        status, datos = conexion.peticion(metodo, ruta, cuerpo=cuerpo, headers=headers, timeout_lectura=timeout_lectura)
        return status, datos
    finally:
        t_total = time.monotonic() - t0
        if not ya_habia_conexion:
            t_conexion = t_total  # la primera petición de esta conexión incluye el handshake completo
        conexion_txt = f"{t_conexion:.3f}s" if t_conexion is not None else "reusada"
        log(f"TIEMPO_TG metodo={etiqueta or ruta!r} conexion={conexion_txt} total={t_total:.3f}s")
