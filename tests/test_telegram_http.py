"""
Prueba del Prompt 6c punto 1: la causa real de los 21 s de demora era que
cada llamada a Telegram abría una conexión NUEVA, probando primero una
dirección IPv6 inalcanzable (la red de Gabriel no tiene IPv6) antes de
caer a IPv4. Esta prueba simula exactamente eso -- un host que resuelve a
una IPv6 inalcanzable Y a un servidor IPv4 real -- y comprueba que, con
TG_FORZAR_IPV4 (por defecto activo), la IPv6 nunca se intenta: la llamada
debe completarse en menos de 1 s, no en ~21 s.
"""

import socket
import threading
import time

import telegram_http

HOST_FALSO = "fake.pruebas.nexora.test"


class _ServidorFalso:
    """Servidor HTTP mínimo, solo IPv4, en un puerto libre de localhost.
    Responde JSON {"ok": true} a cualquier petición; si `cerrar_tras_una`
    está activo, cierra la conexión después de la primera respuesta (para
    probar la reconexión automática)."""

    def __init__(self, cerrar_tras_una: bool = False):
        self.cerrar_tras_una = cerrar_tras_una
        self.peticiones_atendidas = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._detener = False
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

    def _bucle(self):
        while not self._detener:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                self._atender(conn)
            finally:
                conn.close()

    def _atender(self, conn):
        conn.settimeout(5)
        while True:
            if not self._leer_peticion(conn):
                return
            self.peticiones_atendidas += 1
            cuerpo = b'{"ok": true, "result": {}}'
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(cuerpo)).encode() + b"\r\n"
                b"\r\n" + cuerpo
            )
            if self.cerrar_tras_una:
                return

    @staticmethod
    def _leer_peticion(conn) -> bool:
        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                trozo = conn.recv(4096)
                if not trozo:
                    return False
                buf += trozo
        except socket.timeout:
            return False
        return True

    def detener(self):
        self._detener = True
        try:
            self.sock.close()
        except Exception:
            pass


def _parchar_dns(monkeypatch, servidor):
    """El getaddrinfo REAL de Windows/Linux respeta el `family` que se le
    pide -- lo único que hace falta simular acá es que la resolución del
    host falso, SIN filtrar por familia, devolvería tanto la IPv6
    inalcanzable como la IPv4 real (como pasa con api.telegram.org en una
    red sin IPv6: el registro AAAA existe, solo que nadie responde)."""
    getaddrinfo_real = socket.getaddrinfo

    def falso(host, port, family=0, type=0, proto=0, flags=0):
        if host != HOST_FALSO:
            return getaddrinfo_real(host, port, family, type, proto, flags)
        direcciones = []
        if family in (socket.AF_INET, socket.AF_UNSPEC):
            direcciones.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", servidor.port)))
        if family in (socket.AF_INET6, socket.AF_UNSPEC):
            # 2001:db8::/32 es el rango reservado para documentación (RFC
            # 3849): nunca es enrutable de verdad, así que si el cliente
            # llegara a intentarlo de verdad esta prueba quedaría colgada
            # mucho más de 1 s -- justo lo que se quiere detectar.
            direcciones.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", port, 0, 0)))
        return direcciones

    monkeypatch.setattr(socket, "getaddrinfo", falso)


def test_fuerza_ipv4_evita_el_timeout_de_ipv6_inalcanzable(monkeypatch):
    servidor = _ServidorFalso()
    try:
        _parchar_dns(monkeypatch, servidor)
        monkeypatch.setattr(telegram_http, "FORZAR_IPV4", True)
        telegram_http.reiniciar_conexiones_del_hilo_actual()

        t0 = time.monotonic()
        status, cuerpo = telegram_http.peticion(
            f"http://{HOST_FALSO}:{servidor.port}/bot123/getMe", metodo="GET", log=lambda m: None,
        )
        duracion = time.monotonic() - t0
    finally:
        servidor.detener()

    assert status == 200
    assert b'"ok": true' in cuerpo
    assert duracion < 1.0, f"tardó {duracion:.1f}s -- debería ignorar la IPv6 inalcanzable y usar la IPv4 directo"


def test_reutiliza_la_conexion_entre_llamadas(monkeypatch):
    servidor = _ServidorFalso()
    try:
        _parchar_dns(monkeypatch, servidor)
        telegram_http.reiniciar_conexiones_del_hilo_actual()
        logs = []
        for _ in range(2):
            telegram_http.peticion(
                f"http://{HOST_FALSO}:{servidor.port}/bot123/getMe", metodo="GET", log=logs.append,
            )
    finally:
        servidor.detener()

    assert servidor.peticiones_atendidas == 2
    assert "conexion=reusada" not in logs[0]
    assert "conexion=reusada" in logs[1]


def test_reconecta_sola_si_el_servidor_cerro_la_conexion(monkeypatch):
    servidor = _ServidorFalso(cerrar_tras_una=True)
    try:
        _parchar_dns(monkeypatch, servidor)
        telegram_http.reiniciar_conexiones_del_hilo_actual()
        for _ in range(2):
            status, cuerpo = telegram_http.peticion(
                f"http://{HOST_FALSO}:{servidor.port}/bot123/getMe", metodo="GET", log=lambda m: None,
            )
            assert status == 200
            assert b'"ok": true' in cuerpo
    finally:
        servidor.detener()

    assert servidor.peticiones_atendidas == 2
