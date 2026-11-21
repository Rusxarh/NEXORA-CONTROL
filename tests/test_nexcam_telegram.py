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
from datetime import datetime, timedelta

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
    nunca se dirige a un Operador). Prompt 6 punto 1: el texto se arma con
    la ficha única, leída de `vehiculos`/snapshots_placa (no del payload
    del evento) -- igual que en la vida real, donde rgm_cola.completar_item
    ya dejó el snapshot guardado ANTES de emitir "rgm.resultado"."""
    admin = _crear_usuario("Admin Tele9", "Admin", "admin.tele9", telegram_id=555999)
    oper = _crear_usuario("Oper Tele2", "Operador", "oper.tele2", telegram_id=556000)
    bot._vincular(555999, [bot.codigo], "Admin Tele9")
    bot._vincular(556000, [bot.codigo], "Oper Tele2")
    db.insertar_placa("ABC123", "carro", admin["id"], None)
    db.crear_snapshot("ABC123", "RGM", json.dumps({
        "estado": "OK", "deudor_garante": {"nombre": "Juan Pérez", "numero_identificacion": "999888777"},
        "vehiculo": {"marca": "Mazda"},
    }))
    bot.llamadas.clear()

    bot.on_evento("rgm.resultado", {
        "item_id": 1, "placa": "ABC123", "alcance": "RGM", "origen": "panel", "avistamiento_id": None,
        "resultado": {"rgm": {"estado": "OK"}, "judicial": None},
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


def test_texto_judicial_pone_la_financiera_primero_y_marca_privados(bot):
    """Prompt 8 punto 6: los procesos donde el demandante coincide con el
    acreedor del RGM van primero, con "🏦 Demanda la financiera"; los
    privados salen como "🔒 Proceso privado", sin radicado ni botón."""
    admin = _crear_usuario("Admin Tele70", "Admin", "admin.tele70", telegram_id=556700)
    db.insertar_placa("JUD999", "carro", admin["id"], None)
    db.crear_snapshot("JUD999", "RGM", json.dumps({"estado": "OK", "acreedor": {"razon_social": "BANCO FINANDINA SA BIC"}}))

    resultado_judicial = {
        "procesos": [
            {"radicado": "R-OTRO", "despacho": "Juzgado 1", "fecha_ultima_actuacion": "2026-01-01",
             "privado": False, "sujetos": [{"rol": "Demandante", "nombre": "OTRO BANCO"}]},
            {"radicado": None, "despacho": "Juzgado Privado", "privado": True, "sujetos": []},
            {"radicado": "R-FINANDINA", "despacho": "Juzgado 2", "fecha_ultima_actuacion": "2026-06-01",
             "privado": False, "sujetos": [{"rol": "Demandante/accionante", "nombre": "BANCO FINANDINA S.A. BIC"}]},
        ],
    }
    texto = bot._texto_resultado_judicial("JUD999", resultado_judicial, es_admin=True)
    assert texto.index("R-FINANDINA") < texto.index("R-OTRO")  # la financiera primero
    assert "🏦 <code>R-FINANDINA</code>" in texto  # marcada
    assert "<code>R-OTRO</code>" in texto and "🏦 <code>R-OTRO</code>" not in texto  # el otro, sin marca
    assert "🔒 Proceso privado · Juzgado Privado" in texto
    assert "Demandante/accionante: BANCO FINANDINA" in texto


def test_rgm_resultado_telegram_responde_solo_al_chat_que_pidio_y_filtra_por_rol(bot):
    """El botón 🔎 RGM lo puede usar cualquier chat con usuario asignado
    (pedir no es sensible); el resultado SÍ se filtra según el rol de
    quien pidió, y solo le llega a ÉL, no a todos los chats."""
    _crear_usuario("Admin Tele9c", "Admin", "admin.tele9c", telegram_id=555997)
    oper = _crear_usuario("Oper Tele2b", "Operador", "oper.tele2b", telegram_id=556001)
    bot._vincular(555997, [bot.codigo], "Admin Tele9c")
    bot._vincular(556001, [bot.codigo], "Oper Tele2b")
    bot.llamadas.clear()

    db.insertar_placa("ABC123", "carro", oper["id"], None)
    bot._boton_pulsado({"id": "cbq1", "message": {"chat": {"id": 556001}, "message_id": 1}, "data": "rgm:ABC123"})
    # Prompt 7 bloque A: quién pidió esto ahora vive en rgm_avisos (no en
    # un diccionario en memoria), para sobrevivir a un reinicio de NEXORA.
    item = db.item_rgm_pendiente_o_en_curso("ABC123", "RGM")
    item_id = item["id"]
    avisos = db.listar_avisos_rgm(item_id)
    assert len(avisos) == 1 and avisos[0]["chat_id"] == 556001 and avisos[0]["alerta_message_id"] is None

    # completar_item (no crear_snapshot a mano) deja tanto el snapshot
    # como "Mazda" en `vehiculos`, que es de donde la ficha única lee
    # el vehículo (Prompt 6 punto 1/2). Este bot de prueba no está
    # suscrito a nexcam_api (no se llamó bot.arrancar()), así que hay que
    # simular la llegada del evento aparte, como hace el resto del archivo.
    import rgm_cola

    rgm_cola.completar_item(item_id, {
        "rgm": {"estado": "OK", "deudor_garante": {"nombre": "Juan Pérez"}, "vehiculo": {"marca": "Mazda"}},
        "judicial": None,
    })
    bot.on_evento("rgm.resultado", {
        "item_id": item_id, "placa": "ABC123", "alcance": "RGM", "origen": "telegram", "avistamiento_id": None,
        "resultado": {"rgm": {"estado": "OK"}, "judicial": None}, "avisos": avisos,
    })

    por_chat = {c["chat_id"]: c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert 555997 not in por_chat  # solo le llega a quien lo pidió, no a todos los Admin
    assert "Juan Pérez" not in por_chat[556001]  # pero filtrado: Operador no ve al deudor
    assert "Mazda" in por_chat[556001]
    assert db.obtener_item_rgm(item_id)["estado"] == "OK"  # terminado: el vigilante de esperas ya no lo mira


def test_rgm_snapshot_vigente_responde_de_una_vez_por_boton(bot, monkeypatch):
    """SNAPSHOT_VIGENTE nunca pasa por la cola -- rgm.resultado NUNCA
    llegaría para este caso, así que el botón manda el resultado ahí
    mismo, sin esperar nada. El snapshot se deja con completar_item (el
    camino real: también deja "Kia" en `vehiculos`, que es de donde la
    ficha única del Prompt 6 lee el vehículo)."""
    import rgm_cola

    admin = _crear_usuario("Admin Tele9d", "Admin", "admin.tele9d", telegram_id=555996)
    bot._vincular(555996, [bot.codigo], "Admin Tele9d")
    db.insertar_placa("SNV111", "carro", admin["id"], None)
    item = rgm_cola.solicitar_consulta("SNV111", "RGM", "panel", admin["id"])["item"]
    rgm_cola.completar_item(item["id"], {"rgm": {"estado": "OK", "vehiculo": {"marca": "Kia", "modelo": "2022"}}, "judicial": None})
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq2", "message": {"chat": {"id": 555996}, "message_id": 1}, "data": "rgm:SNV111"})

    textos = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert any("Kia" in t for t in textos)


# --------------------------------------------------------------------- Prompt 6 punto 4: botón responde al instante

def test_boton_responde_answerCallbackQuery_antes_de_cualquier_otra_llamada(bot):
    """Prompt 6 punto 4: answerCallbackQuery se manda INMEDIATO, antes de
    calcular nada -- debe ser SIEMPRE la primera llamada de red del
    botón, sin importar cuánto tarde lo que sigue (una consulta a
    rgm_cola, una foto, etc.)."""
    admin = _crear_usuario("Admin Tele23", "Admin", "admin.tele23", telegram_id=556320)
    bot._vincular(556320, [bot.codigo], "Admin Tele23")
    db.insertar_placa("ACK111", "carro", admin["id"], "Hurto")
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq-ack", "message": {"chat": {"id": 556320}, "message_id": 1}, "data": "ficha:ACK111"})

    assert bot.llamadas[0][0] == "answerCallbackQuery"
    assert bot.llamadas[0][1]["callback_query_id"] == "cbq-ack"


# --------------------------------------------------------------------- Prompt 6 punto 1: ficha única

def test_ficha_unica_motivo_vacio_dice_ubicar_no_sin_motivo(bot):
    admin = _crear_usuario("Admin Tele15", "Admin", "admin.tele15", telegram_id=556303)
    bot._vincular(556303, [bot.codigo], "Admin Tele15")
    db.insertar_placa("UBC111", "carro", admin["id"], None)  # sin observaciones/motivo
    bot.llamadas.clear()

    bot._buscar(556303, "UBC111")

    texto = bot.llamadas[-1][1]["text"]
    assert "🚨 En lista: UBICAR" in texto
    assert "sin motivo" not in texto


def test_rgm_a_pedido_desde_una_alerta_edita_esa_alerta_no_manda_mensaje_nuevo(bot):
    """Prompt 6 punto 1: si el botón 🔎 RGM se pulsó sobre una alerta EN
    VIVO, el resultado EDITA esa misma alerta (editMessageText/Caption);
    no se manda ningún sendMessage nuevo con los datos del RGM."""
    import rgm_cola

    admin = _crear_usuario("Admin Tele16", "Admin", "admin.tele16", telegram_id=556304)
    bot._vincular(556304, [bot.codigo], "Admin Tele16")
    db.insertar_placa("ALR222", "carro", admin["id"], "Hurto")

    bot.on_evento("detection.hit", _deteccion("ALR222", device_uuid="tel-alr", id=20))
    # detection.hit pasa primero por _lectura_en_vivo, que abre una sesión
    # de cámara (en_vivo=True por defecto): la alerta queda en
    # sesiones["tel-alr"]["alertas"], no en _alertas_sin_sesion.
    estado = bot.sesiones["tel-alr"]["alertas"]["ALR222"]
    message_id = estado["mensajes"][0]["message_id"]
    bot.llamadas.clear()

    bot._boton_pulsado({
        "id": "cbq-alr", "message": {"chat": {"id": 556304}, "message_id": message_id},
        "data": "rgm:ALR222",
    })
    item = db.item_rgm_pendiente_o_en_curso("ALR222", "RGM")
    item_id = item["id"]
    avisos = db.listar_avisos_rgm(item_id)
    assert len(avisos) == 1
    assert avisos[0]["alerta_chat_id"] == 556304 and avisos[0]["alerta_message_id"] == message_id  # sí la encontró
    bot.llamadas.clear()

    rgm_cola.completar_item(item_id, {"rgm": {"estado": "OK", "vehiculo": {"marca": "Mazda", "modelo": "2019"}},
                                       "judicial": None})
    bot.on_evento("rgm.resultado", {
        "item_id": item_id, "placa": "ALR222", "alcance": "RGM", "origen": "telegram", "avistamiento_id": None,
        "avisos": avisos,
        "resultado": {"rgm": {"estado": "OK"}, "judicial": None},
    })

    nuevos_mensajes = [c for metodo, c in bot.llamadas if metodo == "sendMessage"]
    ediciones = [(metodo, c) for metodo, c in bot.llamadas if metodo in ("editMessageText", "editMessageCaption")]
    assert nuevos_mensajes == []  # nada nuevo: se editó la alerta
    assert len(ediciones) == 1
    assert ediciones[0][1]["message_id"] == message_id
    assert "Mazda" in ediciones[0][1].get("text") or "Mazda" in ediciones[0][1].get("caption", "")


def test_ficha_unica_recorta_ubicaciones_si_no_cabe_en_el_pie_de_foto(bot):
    """Prompt 6 punto 1: el límite de 1024 caracteres del pie de foto se
    respeta recortando la lista de Ubicaciones primero, nunca las líneas
    fijas de arriba (placa, en lista, vehículo, etc.). El tope de 8 filas
    ya ayuda, pero un operador con nombre largo puede igual pasarse de
    1024 -- es justo lo que esto fuerza."""
    admin = _crear_usuario("Admin Tele17", "Admin", "admin.tele17", telegram_id=556305)
    db.insertar_placa("LIM333", "carro", admin["id"], "Hurto")
    operador_largo = "Eduardo Alejandro Camacho Restrepo Villamizar"
    sesion = {"usuario_id": admin["id"], "nombre": operador_largo, "device_uuid": "tel-lim"}
    for i in range(10):
        nexcam_db.registrar_avistamiento(
            sesion, "LIM333", "LIM333", "exact", 0.9, None, None, "prueba",
            4.6 + i * 0.001, -74.1, int(datetime.now().timestamp() * 1000) + i, None, None,
        )

    texto_sin_limite = bot._texto_ficha_telegram("LIM333", es_admin=False)
    texto_limitado = bot._texto_ficha_telegram("LIM333", es_admin=False, limite=1024)
    assert len(texto_sin_limite) > 1024  # de verdad hacía falta recortar
    assert len(texto_limitado) <= 1024
    assert "🚗 <b>LIM 333</b>" in texto_limitado  # las líneas fijas de arriba se conservan
    assert "🚨 En lista: Hurto" in texto_limitado


# --------------------------------------------------------------------- Prompt 7 bloque A: RGM a pedido que no responde

def test_boton_responde_consultando_con_puesto_en_la_cola(bot):
    admin = _crear_usuario("Admin Tele50", "Admin", "admin.tele50", telegram_id=556500)
    bot._vincular(556500, [bot.codigo], "Admin Tele50")
    db.insertar_placa("CON111", "carro", admin["id"], None)
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq-con", "message": {"chat": {"id": 556500}, "message_id": 1}, "data": "rgm:CON111"})

    textos = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert any("Consultando" in t and "CON 111" in t and "puesto" in t for t in textos)


def test_rgm_error_avisa_a_quien_lo_pidio_con_boton_reintentar(bot):
    admin = _crear_usuario("Admin Tele51", "Admin", "admin.tele51", telegram_id=556501)
    oper = _crear_usuario("Oper Tele51", "Operador", "oper.tele51", telegram_id=556502)
    bot._vincular(556501, [bot.codigo], "Admin Tele51")
    bot._vincular(556502, [bot.codigo], "Oper Tele51")
    db.insertar_placa("ERR111", "carro", oper["id"], None)

    # El Operador lo pide (pedir no es sensible); el ítem queda con SU aviso.
    bot._boton_pulsado({"id": "cbq-err1", "message": {"chat": {"id": 556502}, "message_id": 1}, "data": "rgm:ERR111"})
    item = db.item_rgm_pendiente_o_en_curso("ERR111", "RGM")
    avisos = db.listar_avisos_rgm(item["id"])
    bot.llamadas.clear()

    bot.on_evento("rgm.error", {
        "item_id": item["id"], "placa": "ERR111", "alcance": "RGM", "origen": "telegram",
        "error": "timeout de prueba", "avisos": avisos,
    })

    por_chat = {c["chat_id"]: (c["text"], c.get("reply_markup")) for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert 556501 not in por_chat  # solo le llega al Operador que lo pidió
    texto, teclado = por_chat[556502]
    assert "No se pudo consultar RGM" in texto and "ERR 111" in texto
    assert teclado["inline_keyboard"][0][0]["callback_data"] == "rgm:ERR111"


def test_rgm_error_automatico_sin_nadie_pendiente_queda_silencioso(bot):
    admin = _crear_usuario("Admin Tele52", "Admin", "admin.tele52", telegram_id=556503)
    bot._vincular(556503, [bot.codigo], "Admin Tele52")
    bot.llamadas.clear()

    bot.on_evento("rgm.error", {
        "item_id": 999, "placa": "AUT999", "alcance": "RGM", "origen": "camara",
        "error": "timeout", "avisos": [],
    })

    assert bot.llamadas == []


def test_vigilante_de_esperas_edita_una_vez_el_mensaje_consultando(bot):
    admin = _crear_usuario("Admin Tele53", "Admin", "admin.tele53", telegram_id=556504)
    bot._vincular(556504, [bot.codigo], "Admin Tele53")
    db.insertar_placa("ESP111", "carro", admin["id"], None)
    bot._boton_pulsado({"id": "cbq-esp", "message": {"chat": {"id": 556504}, "message_id": 1}, "data": "rgm:ESP111"})
    item = db.item_rgm_pendiente_o_en_curso("ESP111", "RGM")
    aviso = db.listar_avisos_rgm(item["id"])[0]
    assert aviso["mensaje_espera_id"] is not None
    # Lo envejece más de MINUTOS_AVISO_ESPERA_RGM para que la vencida lo recoja.
    vieja = (datetime.now() - timedelta(minutes=db.MINUTOS_AVISO_ESPERA_RGM + 1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_connection() as conn:
        conn.execute("UPDATE rgm_avisos SET creado_en = ? WHERE id = ?", (vieja, aviso["id"]))
    bot.llamadas.clear()

    bot._revisar_esperas_rgm_una_vez()

    ediciones = [c for metodo, c in bot.llamadas if metodo == "editMessageText"]
    assert len(ediciones) == 1
    assert "Sigue en cola" in ediciones[0]["text"]
    assert ediciones[0]["message_id"] == aviso["mensaje_espera_id"]

    # Una segunda pasada no debe volver a editar (avisado_espera=1 ya).
    bot.llamadas.clear()
    bot._revisar_esperas_rgm_una_vez()
    assert bot.llamadas == []


# --------------------------------------------------------------------- Prompt 6b punto 2: botón RGM siempre está, solo Admin

def test_boton_rgm_siempre_esta_en_la_alerta_y_cambia_de_texto(bot):
    """Antes se ocultaba si ya había un RGM de menos de 24 h; ahora
    siempre está, con "🔄 Actualizar RGM" cuando ya hay datos y "🔎
    Consultar RGM" cuando no."""
    import rgm_cola

    admin = _crear_usuario("Admin Tele60", "Admin", "admin.tele60", telegram_id=556600)
    bot._vincular(556600, [bot.codigo], "Admin Tele60")
    db.insertar_placa("BTN111", "carro", admin["id"], "Hurto")

    bot.on_evento("detection.hit", _deteccion("BTN111", device_uuid="tel-btn", id=1))
    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    botones_rgm = [b for fila in ultimo["reply_markup"]["inline_keyboard"] for b in fila if b["callback_data"] == "rgm:BTN111"]
    assert len(botones_rgm) == 1 and botones_rgm[0]["text"] == "🔎 Consultar RGM"

    item = rgm_cola.solicitar_consulta("BTN111", "RGM", "panel", admin["id"])["item"]
    rgm_cola.completar_item(item["id"], {"rgm": {"estado": "OK", "vehiculo": {"marca": "Mazda"}}, "judicial": None})
    bot.llamadas.clear()

    bot.on_evento("detection.hit", _deteccion("BTN111", device_uuid="tel-btn", id=2))
    ultimo2 = [c for metodo, c in bot.llamadas if metodo in ("editMessageText", "editMessageCaption")][-1]
    botones_rgm2 = [b for fila in ultimo2["reply_markup"]["inline_keyboard"] for b in fila if b["callback_data"] == "rgm:BTN111"]
    assert len(botones_rgm2) == 1 and botones_rgm2[0]["text"] == "🔄 Actualizar RGM"  # sigue ahí, ahora dice "Actualizar"


def test_boton_rgm_no_aparece_para_operador(bot):
    admin = _crear_usuario("Admin Tele61", "Admin", "admin.tele61", telegram_id=556601)
    oper = _crear_usuario("Oper Tele61", "Operador", "oper.tele61", telegram_id=556602)
    bot._vincular(556601, [bot.codigo], "Admin Tele61")
    bot._vincular(556602, [bot.codigo], "Oper Tele61")
    db.insertar_placa("BTN222", "carro", oper["id"], "Hurto")

    bot.on_evento("detection.hit", _deteccion("BTN222", device_uuid="tel-btn2", id=1))

    mensajes = {c["chat_id"]: c for metodo, c in bot.llamadas if metodo == "sendMessage"}
    botones_admin = [b for fila in mensajes[556601]["reply_markup"]["inline_keyboard"] for b in fila]
    botones_oper = [b for fila in mensajes[556602]["reply_markup"]["inline_keyboard"] for b in fila]
    assert any(b["callback_data"] == "rgm:BTN222" for b in botones_admin)
    assert not any(b["callback_data"] == "rgm:BTN222" for b in botones_oper)


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


def test_alerta_trae_fecha_de_ingreso_y_operador(bot):
    """Prompt 6 punto 1: la alerta usa el formato único -- "📅 Ingreso:"
    (ya no "📅 En la lista desde:") y la línea de la lectura actual trae
    al operador, pero YA NO el modelo del teléfono (no está en el
    formato único; el teléfono sigue viéndose en "📷 Cámara encendida")."""
    admin = _crear_usuario("Admin Tele13", "Admin", "admin.tele13", telegram_id=556301)
    bot._vincular(556301, [bot.codigo], "Admin Tele13")
    db.insertar_placa("STU222", "carro", admin["id"], "Hurto")
    bot.llamadas.clear()

    bot.on_evento("detection.hit", _deteccion("STU222", device_uuid="tel-2", id=10))

    # Sin snapshot/vehiculo en el payload, _foto_principal no encuentra
    # ninguna foto real: la alerta sale por sendMessage (texto), no sendPhoto.
    ultimo = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "📅 Ingreso:" in ultimo["text"]
    assert "👤 Eduardo" in ultimo["text"]


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


# --------------------------------------------------------------------- Prompt 6 punto 7: Ya la viste completo

def test_retro_hits_usa_ficha_unica_y_solo_avisa_a_chats_admin(bot):
    """Prompt 6 punto 7: "Ya la viste" ahora usa el formato único del
    punto 1 (con 🗺️ Ubicaciones) y SOLO avisa a chats de Admin -- antes
    iba a todos con _a_todos()."""
    admin = _crear_usuario("Admin Tele41", "Admin", "admin.tele41", telegram_id=556401)
    oper = _crear_usuario("Oper Tele41", "Operador", "oper.tele41", telegram_id=556402)
    bot._vincular(556401, [bot.codigo], "Admin Tele41")
    bot._vincular(556402, [bot.codigo], "Oper Tele41")
    db.insertar_placa("YLV111", "carro", admin["id"], "Hurto")
    sesion = {"usuario_id": admin["id"], "nombre": "Eduardo", "device_uuid": "tel-ylv"}
    nexcam_db.registrar_avistamiento(
        sesion, "YLV111", "YLV111", "exact", 0.9, None, None, "prueba",
        4.6, -74.1, int(time.time() * 1000), None, None,
    )
    bot.llamadas.clear()

    vistas = nexcam_db.vistas_de(["YLV111"])["YLV111"]
    bot.on_evento("retro.hits", {"origen": "panel", "hits": [vistas]})

    por_chat = {c["chat_id"]: c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"}
    assert 556402 not in por_chat  # el Operador no recibe nada
    assert "🗂️ <b>Ya la viste: YLV 111</b>" in por_chat[556401]
    assert "🗺️ Ubicaciones (1)" in por_chat[556401]


def test_retro_hits_maximo_10_por_carga_y_resto_en_la_web(bot):
    admin = _crear_usuario("Admin Tele42", "Admin", "admin.tele42", telegram_id=556403)
    bot._vincular(556403, [bot.codigo], "Admin Tele42")
    hits = []
    for i in range(13):
        placa = f"MUC{i:03d}"
        db.insertar_placa(placa, "carro", admin["id"], None)
        sesion = {"usuario_id": admin["id"], "nombre": "Eduardo", "device_uuid": "tel-muc"}
        nexcam_db.registrar_avistamiento(
            sesion, placa, placa, "exact", 0.9, None, None, "prueba",
            None, None, int(time.time() * 1000) + i, None, None,
        )
        hits.append(nexcam_db.vistas_de([placa])[placa])
    bot.llamadas.clear()

    bot.on_evento("retro.hits", {"origen": "panel", "hits": hits})

    mensajes = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    por_placa = [m for m in mensajes if "Ya la viste" in m]
    assert len(por_placa) == 10
    assert any("y 3 más, ver en la web" in m for m in mensajes)


def test_alta_individual_web_dispara_ya_la_viste(bot, cliente):
    admin = _crear_usuario("Admin Tele43", "Admin", "admin.tele43", telegram_id=556404)
    bot._vincular(556404, [bot.codigo], "Admin Tele43")
    import nexcam_api

    nexcam_api.suscribir(bot.on_evento)
    sesion = {"usuario_id": admin["id"], "nombre": "Eduardo", "device_uuid": "tel-alta"}
    nexcam_db.registrar_avistamiento(
        sesion, "ALT111", "ALT111", "exact", 0.9, None, None, "prueba",
        None, None, int(time.time() * 1000), None, None,
    )
    bot.llamadas.clear()
    r = cliente.post("/login", data={"username": "admin.tele43", "password": CLAVE}, follow_redirects=False)
    assert r.status_code == 303

    r_alta = cliente.post("/vehiculos/alta", data={"placa": "ALT111", "tipo_vehiculo": "carro"}, follow_redirects=False)
    assert r_alta.status_code == 303

    import time as _time

    limite = _time.monotonic() + 3
    while not any(m[0] == "sendMessage" for m in bot.llamadas) and _time.monotonic() < limite:
        _time.sleep(0.05)
    textos = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert any("Ya la viste: ALT 111" in t for t in textos)


def test_agregar_por_telegram_dispara_ya_la_viste(bot):
    admin = _crear_usuario("Admin Tele44", "Admin", "admin.tele44", telegram_id=556405)
    bot._vincular(556405, [bot.codigo], "Admin Tele44")
    import nexcam_api

    nexcam_api.suscribir(bot.on_evento)
    sesion = {"usuario_id": admin["id"], "nombre": "Eduardo", "device_uuid": "tel-agr"}
    nexcam_db.registrar_avistamiento(
        sesion, "AGR111", "AGR111", "exact", 0.9, None, None, "prueba",
        None, None, int(time.time() * 1000), None, None,
    )
    bot.llamadas.clear()

    bot._agregar(556405, admin["id"], "AGR111 Hurto")

    import time as _time

    limite = _time.monotonic() + 3
    while sum(1 for m in bot.llamadas if m[0] == "sendMessage") < 2 and _time.monotonic() < limite:
        _time.sleep(0.05)
    textos = [c["text"] for metodo, c in bot.llamadas if metodo == "sendMessage"]
    assert any("Ya la viste: AGR 111" in t for t in textos)


def test_camara_encendida_cierra_y_arranca_de_nuevo_al_cambiar_de_dia(bot):
    """Prompt 6 punto 6: una sesión que sigue abierta al cruzar la
    medianoche (hora de Bogotá) cierra su mensaje de "Cámara encendida"
    con el resumen del día ("🌙 Fin del día", NO "Cámara apagada": el
    teléfono sigue grabando) y abre uno nuevo que arranca en 0 -- nada se
    borra, solo el contador EN VIVO de ese mensaje de Telegram."""
    admin = _crear_usuario("Admin Tele40", "Admin", "admin.tele40", telegram_id=556400)
    bot._vincular(556400, [bot.codigo], "Admin Tele40")
    bot.on_evento("camera.started", {"device_uuid": "tel-dia", "device": "HONOR ALT-NX3", "operador": "Eduardo"})
    bot.on_evento("detection.hit", _deteccion("DIA111", device_uuid="tel-dia", id=1, en_lista=False, match_type=None))
    sesion_vieja = bot.sesiones["tel-dia"]
    sesion_vieja["dia"] = "2020-01-01"  # simula que viene de ayer
    bot.llamadas.clear()

    bot._revisar_sesiones_una_vez()

    ediciones = [c for metodo, c in bot.llamadas if metodo == "editMessageText"]
    assert any("🌙 Fin del día" in e["text"] and nexcam_telegram.placa_destacada("DIA 111") in e["text"]
               for e in ediciones)
    assert not any("Cámara apagada" in e["text"] for e in ediciones)
    nueva = bot.sesiones["tel-dia"]
    assert nueva is not sesion_vieja  # es una sesión nueva, no la misma
    assert nueva["placas"] == {}  # arranca en 0
    assert nueva["dia"] != "2020-01-01"
    nuevo_mensaje = [c for metodo, c in bot.llamadas if metodo == "sendMessage"][-1]
    assert "Cámara encendida" in nuevo_mensaje["text"]
    assert "0 placas" in nuevo_mensaje["text"]


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


# --------------------------------------------------------------------- Prompt 5/6: placa_destacada, lista en vivo, Últimas en vivo

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


def test_ultimas_en_vivo_edita_el_mismo_mensaje_coalescido(bot, monkeypatch):
    """Prompt 6 punto 3: al pulsar 🕒 Últimas se abre UN mensaje con las
    últimas TG_ULTIMAS_VIVO_PAGINA placas; varias lecturas que llegan
    juntas se funden en una sola edición (máximo 1 cada
    TG_ULTIMAS_VIVO_EDIT_SEGUNDOS, no una por lectura)."""
    admin = _crear_usuario("Admin Tele19", "Admin", "admin.tele19", telegram_id=556311)
    bot._vincular(556311, [bot.codigo], "Admin Tele19")

    timers = []

    class _TimerFalso:
        def __init__(self, espera, funcion):
            self.funcion = funcion
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(nexcam_telegram.threading, "Timer", _TimerFalso)
    bot.cfg["en_vivo"] = False  # aísla el temporizador de "Últimas en vivo" del de "Cámara encendida"

    bot._ultimas(556311)
    assert "en vivo" in bot._ultimas_vivo[556311]["ultimo_texto"]
    mensaje_id = bot._ultimas_vivo[556311]["message_id"]
    bot.llamadas.clear()

    sesion = {"usuario_id": admin["id"], "nombre": "Eduardo", "device_uuid": "tel-uvivo"}
    for i in range(7):
        placa = f"UVI{i:03d}"
        # bot.on_evento(...) solo mueve el estado EN MEMORIA del bot (la
        # cola de edición); la fila real de "Últimas" sale de la base, que
        # en la vida real llena /api/detections/upload (nexcam_db) antes
        # de emitir el evento -- hay que insertarla igual aquí.
        nexcam_db.registrar_avistamiento(
            sesion, placa, placa, "exact", 0.78, None, None, "prueba",
            None, None, int(time.time() * 1000) + i, None, None,
        )
        bot.on_evento("detection.hit", _deteccion(
            placa, device_uuid="tel-uvivo", id=i, en_lista=False, match_type=None))

    # 7 lecturas seguidas solo programan UN temporizador: las demás se le
    # juntan porque ya había uno en curso (info["timer"]).
    assert len(timers) == 1
    assert bot._ultimas_vivo[556311]["pendiente"] is True
    assert not any(m[0] == "editMessageText" for m in bot.llamadas)  # todavía no se editó nada

    timers[0].funcion()  # simula que se cumplió la espera de TG_ULTIMAS_VIVO_EDIT_SEGUNDOS

    ediciones = [c for metodo, c in bot.llamadas if metodo == "editMessageText"]
    assert len(ediciones) == 1
    assert ediciones[0]["message_id"] == mensaje_id
    assert nexcam_telegram.placa_destacada("UVI 006") in ediciones[0]["text"]  # la más reciente
    assert bot._ultimas_vivo[556311]["pendiente"] is False


def test_ultimas_en_vivo_se_congela_al_pulsar_otra_vez(bot):
    admin = _crear_usuario("Admin Tele19b", "Admin", "admin.tele19b", telegram_id=556315)
    bot._vincular(556315, [bot.codigo], "Admin Tele19b")
    bot._ultimas(556315)
    primero_id = bot._ultimas_vivo[556315]["message_id"]
    bot.llamadas.clear()

    bot._ultimas(556315)  # pulsarlo de nuevo: congela el anterior y abre uno nuevo
    ediciones = [c for metodo, c in bot.llamadas if metodo == "editMessageText"]
    assert any(e["message_id"] == primero_id and "Ya no se actualiza" in e["text"] for e in ediciones)
    nuevo_id = bot._ultimas_vivo[556315]["message_id"]
    assert nuevo_id != primero_id


def test_ultimas_en_vivo_anteriores_pagina_sin_actualizarse_y_en_vivo_vuelve(bot):
    admin = _crear_usuario("Admin Tele19c", "Admin", "admin.tele19c", telegram_id=556316)
    sesion = {"usuario_id": admin["id"], "nombre": "Admin Tele19c", "device_uuid": "tel-uvivo2"}
    for i in range(12):
        nexcam_db.registrar_avistamiento(
            sesion, f"UPG{i:03d}", f"UPG{i:03d}", "exact", 0.9, None, None, "prueba",
            None, None, int(time.time() * 1000) + i, None, None,
        )
    bot._vincular(556316, [bot.codigo], "Admin Tele19c")
    bot._ultimas(556316)
    message_id = bot._ultimas_vivo[556316]["message_id"]
    bot.llamadas.clear()

    bot._boton_pulsado({"id": "cbq-a", "message": {"chat": {"id": 556316}, "message_id": message_id},
                         "data": "uvivo:atras:2"})
    edicion = [c for metodo, c in bot.llamadas if metodo == "editMessageText"][-1]
    assert "página 2 de" in edicion["text"]
    assert bot._ultimas_vivo[556316]["viendo_pagina"] == 2
    assert any("En vivo" in b["text"] for fila in edicion["reply_markup"]["inline_keyboard"] for b in fila)

    # Mientras se mira una página vieja, una lectura nueva NO toca el mensaje.
    bot.llamadas.clear()
    bot.on_evento("detection.hit", _deteccion("NEW001", device_uuid="tel-uvivo2x", id=99, en_lista=False, match_type=None))
    assert not any(m[0] == "editMessageText" for m in bot.llamadas)

    bot._boton_pulsado({"id": "cbq-b", "message": {"chat": {"id": 556316}, "message_id": message_id},
                         "data": "uvivo:vivo"})
    edicion2 = [c for metodo, c in bot.llamadas if metodo == "editMessageText"][-1]
    assert "en vivo" in edicion2["text"]
    assert bot._ultimas_vivo[556316]["viendo_pagina"] is None
