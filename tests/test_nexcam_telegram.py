"""
tests/test_nexcam_telegram.py
Pruebas de la FASE 3 (bot de Telegram) que NO dependen de la red: se
reemplaza BotTelegram._llamar/_enviar_foto por versiones falsas que solo
registran lo que se habría enviado. El bucle de "long polling" (_bucle) y
la integración real contra la API de Telegram NO se prueban aquí --
requieren una red o un servidor de Telegram simulado corriendo, que queda
pendiente como hueco de prueba explícito (ver reporte de la FASE 3).
"""

import time
from datetime import datetime

import pytest

import database as db
import nexcam_db
import nexcam_telegram
import usuarios

CLAVE = "clave-de-prueba-123"


@pytest.fixture()
def bot(tmp_path, monkeypatch, cliente):
    """`cliente` (de conftest.py) ya deja database.DB_PATH y
    nexcam_db.NEXCAM_FOTOS_DIR apuntando a tmp_path; aquí solo se agrega
    un telegram.json de prueba y se reemplazan las llamadas de red."""
    ruta_cfg = tmp_path / "telegram.json"
    ruta_cfg.write_text('{"token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabc12", "chats": [], "modo": "alertas", "silencio": false, "en_vivo": true}')

    b = nexcam_telegram.BotTelegram(ruta_cfg, log=lambda *a: None)
    b.llamadas = []

    def _llamar_falso(metodo, cuerpo):
        b.llamadas.append((metodo, cuerpo))
        if metodo == "getMe":
            return {"username": "nexcam_prueba_bot"}
        if metodo == "sendMessage":
            return {"message_id": len(b.llamadas)}
        return {}

    monkeypatch.setattr(b, "_llamar", _llamar_falso)
    monkeypatch.setattr(b, "_enviar_foto", lambda *a, **k: b.llamadas.append(("sendPhoto", a)) or {"message_id": 1})
    monkeypatch.setattr(b, "_enviar_documento", lambda *a, **k: b.llamadas.append(("sendDocument", a)) or {"message_id": 1})
    return b


def _crear_usuario(nombre, rol, username, telegram_id=None):
    uid = db.crear_usuario(
        telegram_id or db.telegram_id_provisional(), nombre, rol,
        username=username, password_hash=usuarios.hash_password(CLAVE),
    )
    return db.obtener_usuario_por_id(uid)


# --------------------------------------------------------------------- config

def test_guardar_y_releer_config(bot):
    bot.cfg["modo"] = "todo"
    bot._guardar()
    releido = nexcam_telegram.BotTelegram(bot.config_path, log=lambda *a: None)
    assert releido.cfg["modo"] == "todo"


# --------------------------------------------------------------------- vinculación

def test_vincular_con_codigo_correcto_resuelve_usuario_por_telegram_id(bot):
    _crear_usuario("Admin Tele", "Admin", "admin.tele", telegram_id=555111)
    bot._vincular(555111, [bot.codigo], "Admin Tele")
    assert bot._autorizado(555111)
    assert bot._usuario_de_chat(555111)["username"] == "admin.tele"


def test_vincular_con_codigo_incorrecto_no_vincula(bot):
    bot._vincular(999, ["000000"], "Alguien")
    assert not bot._autorizado(999)


def test_vincular_sin_usuario_nexora_queda_sin_usuario(bot):
    bot._vincular(777, [bot.codigo], "Chat Sin Usuario")
    assert bot._autorizado(777)
    assert bot._usuario_de_chat(777) is None


# --------------------------------------------------------------------- acciones que exigen usuario

def test_agregar_sin_usuario_asignado_pide_asignacion(bot):
    bot._vincular(888, [bot.codigo], "Chat Pendiente")
    bot._mensaje(888, "/agregar ABC123 motivo de prueba", "Chat Pendiente")
    ultimo = bot.llamadas[-1]
    assert ultimo[0] == "sendMessage"
    assert "usuario de NEXORA" in ultimo[1]["text"]
    assert db.buscar_placa("ABC123") is None


def test_agregar_con_usuario_asignado_inserta_placa(bot):
    admin = _crear_usuario("Admin Tele2", "Admin", "admin.tele2", telegram_id=555222)
    bot._vincular(555222, [bot.codigo], "Admin Tele2")
    bot._agregar(555222, admin["id"], "ABC123 Aprehensión judicial")
    placa = db.buscar_placa("ABC123")
    assert placa is not None
    assert placa["observaciones"] == "Aprehensión judicial"


def test_agregar_placa_duplicada_no_falla(bot):
    admin = _crear_usuario("Admin Tele3", "Admin", "admin.tele3", telegram_id=555333)
    db.insertar_placa("DEF456", "carro", admin["id"], None)
    bot._agregar(555333, admin["id"], "DEF456 otra vez")
    ultimo = bot.llamadas[-1]
    assert "ya está en el inventario" in ultimo[1]["text"]


def test_quitar_placa_existente(bot):
    admin = _crear_usuario("Admin Tele4", "Admin", "admin.tele4", telegram_id=555444)
    db.insertar_placa("GHI789", "carro", admin["id"], None)
    bot._quitar(555444, admin["id"], "GHI789")
    assert db.buscar_placa("GHI789")["estado"] == "baja"


def test_quitar_placa_inexistente(bot):
    admin = _crear_usuario("Admin Tele5", "Admin", "admin.tele5", telegram_id=555555)
    bot._quitar(555555, admin["id"], "ZZZ000")
    assert "no está en la lista" in bot.llamadas[-1][1]["text"]


# --------------------------------------------------------------------- buscar / estado

def test_buscar_placa_no_existente_no_falla(bot):
    bot._buscar(1, "JKL012")
    assert bot.llamadas[-1][0] == "sendMessage"
    assert "No está en la lista" in bot.llamadas[-1][1]["text"]


def test_estado_no_falla_sin_dispositivos(bot):
    texto = bot._texto_estado()
    assert "Estado de NEXCAM" in texto


# --------------------------------------------------------------------- eventos del servidor

def test_on_evento_device_new_solo_a_chats_admin(bot):
    admin = _crear_usuario("Admin Tele6", "Admin", "admin.tele6", telegram_id=555666)
    oper = _crear_usuario("Oper Tele1", "Operador", "oper.tele1", telegram_id=555667)
    bot._vincular(555666, [bot.codigo], "Admin Tele6")
    bot._vincular(555667, [bot.codigo], "Oper Tele1")
    bot.llamadas.clear()  # descarta los mensajes de bienvenida de _vincular

    bot.on_evento("device.new", {"device_uuid": "dev-x", "brand": "Moto", "model": "G"})

    destinatarios = [c["chat_id"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert 555666 in destinatarios
    assert 555667 not in destinatarios


def test_on_evento_detection_hit_no_falla_sin_fotos(bot):
    admin = _crear_usuario("Admin Tele7", "Admin", "admin.tele7", telegram_id=555777)
    bot._vincular(555777, [bot.codigo], "Admin Tele7")
    bot.on_evento("detection.hit", {
        "placa": "MNO345", "leida": "MNO345", "en_lista": True, "motivo": "Prueba",
        "operador": "Admin Tele7", "ts": int(time.time() * 1000), "id": 1,
        "snapshot": None, "vehiculo": None, "lat": None, "lng": None,
    })
    assert any(m[0] == "sendMessage" for m in bot.llamadas)


def test_asignar_usuario_a_chat(bot):
    admin = _crear_usuario("Admin Tele8", "Admin", "admin.tele8", telegram_id=555888)
    bot._vincular(999888, [bot.codigo], "Chat Reasignable")
    assert bot.asignar_usuario_a_chat(999888, admin["id"]) is True
    assert bot._usuario_de_chat(999888)["id"] == admin["id"]
    assert bot.asignar_usuario_a_chat(123123, admin["id"]) is False


# --------------------------------------------------------------------- FASE 6: filtro de seguridad RGM/Judicial

def test_rgm_resultado_admin_ve_deudor_operador_no(bot):
    admin = _crear_usuario("Admin Tele9", "Admin", "admin.tele9", telegram_id=555999)
    oper = _crear_usuario("Oper Tele2", "Operador", "oper.tele2", telegram_id=556000)
    bot._vincular(555999, [bot.codigo], "Admin Tele9")
    bot._vincular(556000, [bot.codigo], "Oper Tele2")
    bot.llamadas.clear()

    bot.on_evento("rgm.resultado", {
        "item_id": 1, "placa": "ABC123", "alcance": "RGM", "origen": "panel", "avistamiento_id": None,
        "resultado": {"rgm": {"estado": "OK", "deudor_garante": {"nombre": "Juan Pérez", "numero_identificacion": "999888777"},
                              "vehiculo": {"marca": "Mazda"}}, "judicial": None},
    })

    por_chat = {c["chat_id"]: c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert "Juan Pérez" in por_chat[555999]
    assert "Juan Pérez" not in por_chat[556000]
    assert "999888777" not in por_chat[556000]
    assert "Mazda" in por_chat[556000]  # el dato del vehículo SÍ es visible para todos
    assert "consulta hecha" in por_chat[556000].lower()


def test_rgm_resultado_judicial_solo_admin_ve_radicados(bot):
    admin = _crear_usuario("Admin Tele10", "Admin", "admin.tele10", telegram_id=556111)
    oper = _crear_usuario("Oper Tele3", "Operador", "oper.tele3", telegram_id=556112)
    bot._vincular(556111, [bot.codigo], "Admin Tele10")
    bot._vincular(556112, [bot.codigo], "Oper Tele3")
    bot.llamadas.clear()

    bot.on_evento("rgm.resultado", {
        "item_id": 2, "placa": "DEF456", "alcance": "JUDICIAL", "origen": "panel", "avistamiento_id": None,
        "resultado": {"rgm": None, "judicial": {"procesos": [{"radicado": "05001310300120250012300"}]}},
    })

    por_chat = {c["chat_id"]: c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert "05001310300120250012300" in por_chat[556111]
    assert "05001310300120250012300" not in por_chat[556112]
    assert "consulta hecha" in por_chat[556112].lower()


def test_rgm_resultado_responde_al_mensaje_del_avistamiento(bot):
    admin = _crear_usuario("Admin Tele11", "Admin", "admin.tele11", telegram_id=556222)
    bot._vincular(556222, [bot.codigo], "Admin Tele11")
    bot._mensajes_por_avistamiento[42] = [{"chat_id": 556222, "message_id": 777}]
    bot.llamadas.clear()

    bot.on_evento("rgm.resultado", {
        "item_id": 3, "placa": "GHI789", "alcance": "RGM", "avistamiento_id": 42,
        "resultado": {"rgm": {"vehiculo": {"marca": "Kia"}}, "judicial": None},
    })

    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert ultimo["reply_to_message_id"] == 777
    assert 42 not in bot._mensajes_por_avistamiento  # se consume, no queda colgado


# --------------------------------------------------------------------- "0. Pendientes rápidos": instalar app

def test_bot_no_arranca_bajo_pytest(bot, monkeypatch):
    # _pruebas_activas() debe ser True aquí mismo (estamos corriendo con pytest).
    assert nexcam_telegram._pruebas_activas() is True
    bot.arrancar()
    assert bot.llamadas == []  # ni getMe ni setMyCommands: no llamó a nada


def test_url_apk_fija_se_relee_del_archivo(bot):
    assert bot._url_apk_fija() is None
    bot.config_path.write_text(bot.config_path.read_text().replace("}", ', "apkUrl": "https://example.com/NEXCAM.apk"}'))
    assert bot._url_apk_fija() == "https://example.com/NEXCAM.apk"


def test_enviar_apk_usa_enlace_fijo_cuando_existe(bot):
    bot.cfg["apkUrl"] = "https://example.com/NEXCAM.apk"
    bot.enviar_apk(999)
    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert ultimo["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://example.com/NEXCAM.apk"


def test_comando_app_llama_a_enviar_apk(bot, monkeypatch):
    llamado = {}
    monkeypatch.setattr(bot, "enviar_apk", lambda chat_id: llamado.setdefault("chat_id", chat_id))
    bot._vincular(321, [bot.codigo], "Nuevo")
    assert llamado.get("chat_id") == 321  # se manda sola al vincular por primera vez

    llamado.clear()
    bot._mensaje(321, "/app", "Nuevo")
    assert llamado.get("chat_id") == 321


def test_enviar_apk_sin_enlace_ni_archivo_avisa(bot, monkeypatch):
    monkeypatch.setattr(nexcam_telegram, "ruta_apk", lambda: nexcam_telegram.Path("Z:/no-existe/app.apk"))
    bot.enviar_apk(555)
    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "Todavía no hay una app compilada" in ultimo["text"]


def test_descargar_apk_token_invalido_403(cliente):
    r = cliente.get("/app/nexcam.apk", params={"k": "token-que-no-existe"})
    assert r.status_code == 403


def test_crear_enlace_apk_temporal_queda_vigente():
    url = nexcam_telegram.crear_enlace_apk_temporal("http://192.168.1.9:8000")
    token = url.split("k=")[1]
    assert nexcam_telegram.enlace_apk_temporal_vigente(token) is True
    assert nexcam_telegram.enlace_apk_temporal_vigente("otro-token") is False
