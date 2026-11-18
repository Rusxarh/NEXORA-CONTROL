"""
tests/test_nexcam_telegram.py
Pruebas de la FASE 3 (bot de Telegram) que NO dependen de la red: se
reemplaza BotTelegram._llamar/_enviar_foto por versiones falsas que solo
registran lo que se habría enviado. El bucle de "long polling" (_bucle) y
la integración real contra la API de Telegram NO se prueban aquí --
requieren una red o un servidor de Telegram simulado corriendo, que queda
pendiente como hueco de prueba explícito (ver reporte de la FASE 3).
"""

import json
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

def test_rgm_automatico_camara_queda_silencioso(bot):
    """Prompt 5 punto 1: origen "camara"/"estudio" (automático) nunca
    manda nada a Telegram -- ya se guardó el snapshot y actualizó la
    ficha del lado de rgm_cola, eso es todo."""
    _crear_usuario("Admin Tele9b", "Admin", "admin.tele9b", telegram_id=555998)
    bot._vincular(555998, [bot.codigo], "Admin Tele9b")
    bot.llamadas.clear()

    bot.on_evento("rgm.resultado", {
        "item_id": 1, "placa": "ABC123", "alcance": "RGM", "origen": "camara", "avistamiento_id": 10,
        "resultado": {"rgm": {"estado": "OK", "deudor_garante": {"nombre": "Juan Pérez"},
                              "vehiculo": {"marca": "Mazda"}}, "judicial": None},
    })
    assert bot.llamadas == []

    bot.on_evento("rgm.error", {"item_id": 1, "placa": "ABC123", "alcance": "RGM", "origen": "camara", "error": "timeout"})
    assert bot.llamadas == []


def test_rgm_resultado_panel_solo_a_chats_admin(bot):
    """Prompt 5 punto 1: una consulta pedida desde la web ("panel") avisa
    a los chats de Admin; un chat de Operador ni siquiera recibe mensaje
    (antes SÍ le llegaba una versión recortada -- ya no, origen "panel"
    nunca se dirige a un Operador)."""
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
    assert "999888777" in por_chat[555999]
    assert 556000 not in por_chat  # el Operador no recibe nada de un origen "panel"


def test_rgm_resultado_judicial_panel_solo_a_chats_admin(bot):
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
    assert 556112 not in por_chat


def test_rgm_resultado_telegram_responde_solo_al_chat_que_pidio_y_filtra_por_rol(bot):
    """El botón 🔎 RGM lo puede usar cualquier chat con usuario asignado
    (pedir no es sensible); el resultado SÍ se filtra según el rol de
    quien pidió, y solo le llega a ÉL, no a todos los chats."""
    _crear_usuario("Admin Tele9c", "Admin", "admin.tele9c", telegram_id=555997)
    oper = _crear_usuario("Oper Tele2b", "Operador", "oper.tele2b", telegram_id=556001)
    bot._vincular(555997, [bot.codigo], "Admin Tele9c")
    bot._vincular(556001, [bot.codigo], "Oper Tele2b")
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq1", "message": {"chat": {"id": 556001}, "message_id": 1}, "data": "rgm:ABC123"})
    item_id = next(iter(bot._chat_por_item_rgm))
    assert bot._chat_por_item_rgm[item_id] == 556001

    bot.on_evento("rgm.resultado", {
        "item_id": item_id, "placa": "ABC123", "alcance": "RGM", "origen": "telegram", "avistamiento_id": None,
        "resultado": {"rgm": {"estado": "OK", "deudor_garante": {"nombre": "Juan Pérez"},
                              "vehiculo": {"marca": "Mazda"}}, "judicial": None},
    })

    por_chat = {c["chat_id"]: c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert 555997 not in por_chat  # solo le llega a quien lo pidió, no a todos los Admin
    assert "Juan Pérez" not in por_chat[556001]  # pero filtrado: Operador no ve al deudor
    assert "Mazda" in por_chat[556001]
    assert item_id not in bot._chat_por_item_rgm  # se consume, no queda colgado


def test_rgm_snapshot_vigente_responde_de_una_vez_por_boton(bot, monkeypatch):
    """SNAPSHOT_VIGENTE nunca pasa por la cola -- rgm.resultado NUNCA
    llegaría para este caso, así que el botón manda el resultado ahí
    mismo, sin esperar nada."""
    import rgm_cola

    admin = _crear_usuario("Admin Tele9d", "Admin", "admin.tele9d", telegram_id=555996)
    bot._vincular(555996, [bot.codigo], "Admin Tele9d")
    db.insertar_placa("SNV111", "carro", admin["id"], None)
    db.crear_snapshot("SNV111", "RGM", json.dumps({"estado": "OK", "vehiculo": {"marca": "Kia", "modelo": "2022"}}))
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq2", "message": {"chat": {"id": 555996}, "message_id": 1}, "data": "rgm:SNV111"})

    textos = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert any("Kia" in t for t in textos)


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


# --------------------------------------------------------------------- Prompt 2: rediseño del bot

def _deteccion(placa, device_uuid="tel-1", **extra):
    base = {
        "id": extra.pop("id", 1), "placa": placa, "leida": placa, "en_lista": True,
        "match_type": None, "motivo": "Prueba", "operador": "Eduardo", "device": "HONOR ALT-NX3",
        "device_uuid": device_uuid, "confidence": 0.78, "lat": 4.6, "lng": -74.1,
        "ts": int(time.time() * 1000),
    }
    base.update(extra)
    return base


def test_alerta_unica_por_placa_edita_en_vez_de_repetir(bot):
    admin = _crear_usuario("Admin Tele12", "Admin", "admin.tele12", telegram_id=556300)
    bot._vincular(556300, [bot.codigo], "Admin Tele12")
    db.insertar_placa("PQR111", "carro", admin["id"], "Aprehensión judicial")
    bot.llamadas.clear()

    bot.on_evento("camera.started", {"device_uuid": "tel-1", "device": "HONOR ALT-NX3", "operador": "Eduardo"})
    for i in range(5):
        bot.on_evento("detection.hit", _deteccion("PQR111", id=i + 1))

    envios = [m for m in bot.llamadas if m[0] in ("sendMessage", "sendPhoto")]
    ediciones = [m for m in bot.llamadas if m[0] in ("editMessageText", "editMessageCaption")]
    # 1 mensaje nuevo para la alerta de la placa (+ 1 para "Cámara encendida"
    # en vivo) y 4 ediciones de esa misma alerta (una por cada repetición).
    assert len(envios) == 2
    assert len(ediciones) == 4
    assert all(m[0] != "sendLocation" for m in bot.llamadas)


def test_alerta_trae_fecha_de_ingreso_y_telefono(bot):
    admin = _crear_usuario("Admin Tele13", "Admin", "admin.tele13", telegram_id=556301)
    bot._vincular(556301, [bot.codigo], "Admin Tele13")
    db.insertar_placa("STU222", "carro", admin["id"], "Hurto")
    bot.llamadas.clear()

    bot.on_evento("detection.hit", _deteccion("STU222", device_uuid="tel-2", id=10))

    # Sin snapshot/vehiculo en el payload, _foto_principal no encuentra
    # ninguna foto real: la alerta sale por sendMessage (texto), no sendPhoto.
    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "📅 En la lista desde" in ultimo["text"]
    assert "HONOR ALT-NX3" in ultimo["text"]


def test_camara_encendida_muestra_nombre_real_del_telefono(bot):
    admin = _crear_usuario("Admin Tele14", "Admin", "admin.tele14", telegram_id=556302)
    bot._vincular(556302, [bot.codigo], "Admin Tele14")
    bot.llamadas.clear()

    bot.on_evento("camera.started", {"device_uuid": "tel-3", "device": "HONOR ALT-NX3", "operador": "Eduardo"})

    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "HONOR ALT-NX3" in ultimo["text"]
    assert "Teléfono" not in ultimo["text"]


def test_camera_state_false_cierra_con_camara_apagada_no_senal_perdida(bot):
    admin = _crear_usuario("Admin Tele15", "Admin", "admin.tele15", telegram_id=556303)
    bot._vincular(556303, [bot.codigo], "Admin Tele15")
    bot.llamadas.clear()

    bot.on_evento("camera.started", {"device_uuid": "tel-4", "device": "HONOR ALT-NX3", "operador": "Eduardo"})
    bot.on_evento("camera.stopped", {"device_uuid": "tel-4", "device": "HONOR ALT-NX3", "operador": "Eduardo"})

    ultimo = [c for metodo, c in bot.llamadas if metodo == "editMessageText"][-1]
    assert "Cámara apagada" in ultimo["text"]
    assert "Se perdió la señal" not in ultimo["text"]


def test_alerta_fuera_de_sesion_tambien_edita_una_sola_vez(bot):
    """Sin camera.started (en_vivo apagado o lectura antes de abrir sesión):
    el de-duplicado cae al diccionario _alertas_sin_sesion por TG_REPETIR_MIN."""
    admin = _crear_usuario("Admin Tele16", "Admin", "admin.tele16", telegram_id=556304)
    bot._vincular(556304, [bot.codigo], "Admin Tele16")
    bot.cfg["en_vivo"] = False
    db.insertar_placa("VWX333", "carro", admin["id"], "Prueba")
    bot.llamadas.clear()

    bot.on_evento("detection.hit", _deteccion("VWX333", device_uuid="tel-5", id=20))
    bot.on_evento("detection.hit", _deteccion("VWX333", device_uuid="tel-5", id=21))

    envios = [m for m in bot.llamadas if m[0] in ("sendMessage", "sendPhoto")]
    ediciones = [m for m in bot.llamadas if m[0] in ("editMessageText", "editMessageCaption")]
    assert len(envios) == 1
    assert len(ediciones) == 1
    assert ("tel-5", "VWX333") in bot._alertas_sin_sesion


def test_lectura_tardia_no_contamina_la_sesion_y_avisa_aparte(bot):
    """Prueba real con el teléfono: lecturas de la cola acumulada (ts de
    ANTES de que la sesión de cámara actual empezara) no deben aparecer en
    "Cámara encendida" como si fueran de ahora, ni editar una alerta de
    esta sesión como si fuera nueva -- si nunca se avisó esa placa en esta
    sesión, se manda un aviso aparte marcado "con retraso"."""
    admin = _crear_usuario("Admin Tele17", "Admin", "admin.tele17", telegram_id=556305)
    bot._vincular(556305, [bot.codigo], "Admin Tele17")
    db.insertar_placa("LMN222", "carro", admin["id"], "Prueba tardía")

    bot.on_evento("camera.started", {"device_uuid": "tel-tardio", "device": "HONOR ALT-NX3", "operador": "Eduardo"})
    bot.llamadas.clear()

    ts_tardio = int(time.time() * 1000) - 5 * 60 * 1000  # 5 min antes del inicio de la sesión
    bot.on_evento("detection.hit", _deteccion("LMN222", device_uuid="tel-tardio", id=99, ts=ts_tardio))

    sesion = bot.sesiones["tel-tardio"]
    assert "LMN222" not in sesion["placas"]
    assert "LMN222" not in sesion["alertas"]

    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "llegó con retraso" in ultimo["text"]
    assert "🚗" in ultimo["text"] and "LMN222"[:3] in ultimo["text"]

    # Una SEGUNDA lectura tardía de la MISMA placa ya no debe avisar de nuevo.
    bot.llamadas.clear()
    bot.on_evento("detection.hit", _deteccion("LMN222", device_uuid="tel-tardio", id=100, ts=ts_tardio + 1000))
    assert bot.llamadas == []


# --------------------------------------------------------------------- Prompt 5: placa_destacada, lista en vivo, Últimas

def test_placa_destacada_convierte_letras_y_numeros():
    assert nexcam_telegram.placa_destacada("GCV 471") == "𝗚𝗖𝗩 𝟰𝟳𝟭"
    assert nexcam_telegram.placa_destacada("") == ""
    # letras minúsculas se quedan igual (la placa siempre llega normalizada
    # a mayúsculas); los dígitos sí se convierten sin importar el contexto.
    assert nexcam_telegram.placa_destacada("abc-123") == "abc-" + nexcam_telegram.placa_destacada("123")


def test_lista_en_vivo_solo_muestra_las_ultimas_diez_placas(bot):
    admin = _crear_usuario("Admin Tele18", "Admin", "admin.tele18", telegram_id=556310)
    bot._vincular(556310, [bot.codigo], "Admin Tele18")
    bot.on_evento("camera.started", {"device_uuid": "tel-vivo", "device": "HONOR ALT-NX3", "operador": "Eduardo"})

    base_ts = int(time.time() * 1000)
    for i in range(15):
        bot.on_evento("detection.hit", _deteccion(
            f"VVV{i:03d}", device_uuid="tel-vivo", id=i, ts=base_ts + i * 1000, en_lista=False, match_type=None))

    sesion = bot.sesiones["tel-vivo"]
    assert len(sesion["placas"]) == 15  # la cabecera sigue contando el total de la sesión completa
    texto = bot._texto_sesion(sesion)
    assert "15 placas" in texto
    assert nexcam_telegram.placa_destacada("VVV 014") in texto  # la más reciente, sí aparece
    assert nexcam_telegram.placa_destacada("VVV 000") not in texto  # la más vieja, ya no (salió al llegar la 11ª)
    assert "…y 5 placa(s) más." in texto


def test_ultimas_paginada_y_los_botones_editan_el_mismo_mensaje(bot):
    admin = _crear_usuario("Admin Tele19", "Admin", "admin.tele19", telegram_id=556311)
    bot._vincular(556311, [bot.codigo], "Admin Tele19")
    sesion = {"usuario_id": admin["id"], "nombre": "Admin Tele19", "device_uuid": "tel-ultimas"}
    for i in range(25):
        nexcam_db.registrar_avistamiento(
            sesion, f"UUU{i:03d}", f"UUU{i:03d}", "exact", 0.9, None, None, "prueba",
            None, None, int(time.time() * 1000) + i, None, None,
        )

    bot.llamadas.clear()
    resultado = bot._ultimas(556311)
    primero = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "página 1 de 3" in primero["text"]
    assert any("Siguiente" in b["text"] for fila in primero["reply_markup"]["inline_keyboard"] for b in fila)

    bot._boton_pulsado({"id": "cbq1", "message": {"chat": {"id": 556311}, "message_id": resultado["message_id"]}, "data": "ultimas:2"})
    edicion = [c for metodo, c in bot.llamadas if metodo == "editMessageText"][-1]
    assert edicion["message_id"] == resultado["message_id"]  # edita el MISMO mensaje, no manda uno nuevo
    assert "página 2 de 3" in edicion["text"]
    assert sum(1 for metodo, c in bot.llamadas if metodo == "sendMessage") == 1  # sigue siendo un solo mensaje

    # El botón "2/3" (indicador de página) no hace nada, pero no debe fallar.
    bot._boton_pulsado({"id": "cbq2", "message": {"chat": {"id": 556311}, "message_id": resultado["message_id"]}, "data": "noop"})
