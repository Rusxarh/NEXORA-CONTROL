# NEXCAM en NEXORA: prompts para Claude Code y plan de pruebas

Actualizado: 20/11/2026

## Cómo empezar

Este archivo vive en dos lugares:
- En el Proyecto NEXORA de Claude (`claude/nexcam-prompts-y-pruebas.md`).
- En tu PC, para que Claude Code lo lea: `C:\Users\Rus\Documents\NEXORA\PROMPTS Y PRUEBAS DOCS\NEXCAM-PROMPTS-Y-PRUEBAS.md` (fuera de los repositorios: sirve para todas las apps y no se sube a GitHub).

Cuando este archivo cambie, reemplaza la copia de la PC.

Orden de trabajo:

| Paso | Qué le pides a Claude Code | Qué pruebas tú |
|---|---|---|
| 1 | Prompts 0, 2 y 2b | Fases 7 y 10 |
| 2 | Nada nuevo (ya está hecho) | Fases 8 y 9 (RGM y Judicial) |
| 3 | Prompt 3 (bloques A a G) | Fases 11 a 14 |
| 4 | Prompt 4 (en NEXDOC) | Fase 15 |
| 1b | Prompt 5 (después de los 0, 2 y 2b; antes del 3) | Fase 16 |
| 1c | Prompt 6 (antes del 3) + apps 0.5.4 y 0.5.5 | Fase 16 (16.9 a 16.20) |
| 1c2 | Prompt 6b (correcciones de la prueba del 6) | 16.9, 16.12, 16.13, 16.17, 16.20 |
| 1c3 | Prompt 6c (botones lentos, búsqueda en vivo, chat sin usuario) | 16.12, 16.13, 16.21, 16.22 |
| 1d | Prompt 7 (RGM a pedido + acceso desde internet) | Fase 17 |
| 1e | Prompt 8 (RGM y Judicial en segundo plano, todo en NEXORA) | Fase 18 |
| 1e2 | Prompt 8b (RGM sigue abriendo pestaña, 500 FK, database locked, Ya vistas) | Fase 18 de nuevo |
| 1e3 | Prompt 8c (RGM sin pestaña de verdad, con RGM-PLAN-A.md) | 18.1, 18.9 |
| 1e4 | Prompt 8d (procesos completos y legibles en Telegram + plan A sin falsa alarma) | Fase 18 (18.12 a 18.16) |
| 1f | Prompt 9 (Telegram limpio con menú y Volver) | Fase 19 |

### Regla para todos los reportes de Claude Code

```text
Al final de CADA reporte (de cualquier prompt o bloque), agrega la sección
"Pruebas para tu visto bueno" con:
- Las pruebas de este documento que ya se pueden hacer con lo que acabas de
  terminar (número y nombre, ej. "16.1 RGM automático sin mensajes"), en el orden
  en que conviene hacerlas.
- Para cada una: qué abrir, qué hacer y qué debe verse si está bien, en pasos
  cortos y sin jerga.
- Lo que hay que hacer antes (reiniciar NEXORA, recargar el Connector, compilar
  la app, etc.).
- Las que todavía NO se pueden hacer y por qué.
- Al final, la línea para responderte: "16.1 OK · 16.2 falla (captura)".
Si cambiaste algo que no tiene prueba en este documento, propón la prueba nueva
con el siguiente número de su fase.
```

### Prompt para arrancar el paso 1 (conversación NUEVA de Claude Code)

```text
Lee completos CLAUDE.md (de NEXORA-CONTROL) y
"C:\Users\Rus\Documents\NEXORA\PROMPTS Y PRUEBAS DOCS\NEXCAM-PROMPTS-Y-PRUEBAS.md".
Ese archivo es la instrucción vigente. Trabaja en NEXORA-CONTROL, rama `nexcam`.
Ejecuta ahora el Prompt 0 y después el Prompt 2, sin preguntarme (sigue CLAUDE.md).
No hagas todavía el Prompt 3.
Push de `nexcam` autorizado al terminar. Al final, repórtame punto por punto qué
quedó hecho de los Prompts 0 y 2, y qué pruebas de las Fases 7 y 10 ya puedo hacer.
```

### Prompt para arrancar el paso 3 (cuando las Fases 7 a 10 estén bien)

```text
Lee de nuevo "C:\Users\Rus\Documents\NEXORA\PROMPTS Y PRUEBAS DOCS\NEXCAM-PROMPTS-Y-PRUEBAS.md" (puede haber cambiado) y ejecuta el
Prompt 3 completo, bloques A a G en orden, sin preguntarme (sigue CLAUDE.md). Rama
`nexcam`, commit por bloque, push autorizado al terminar. Al final, repórtame bloque
por bloque qué quedó hecho y qué pruebas de las Fases 11 a 14 ya puedo hacer.
Después, en la carpeta de NEXDOC, ejecuta el Prompt 4.
```

---

## Prompt 0: errores planos en la API de la app (crítico)

```text
Bug de contrato (crítico): la app NEXCAM lee los errores como JSON plano
{"code": "...", "message": "..."} (igual que mock-api/server.js), pero nexcam_api.py
los devuelve anidados en {"detail": {...}} por usar HTTPException. Resultado real
en el teléfono: el login muestra "HTTP 403" en vez del mensaje, y los códigos
TOKEN_EXPIRED / DEVICE_REVOKED / SESSION_REPLACED nunca llegan a la app (no cierra
sesión ni borra datos al revocar).

Arréglalo sin preguntar:
- Un exception handler en main.py (o en el router de nexcam_api) que, para rutas
  /api/* y /app/*, devuelva el detail aplanado: el mismo status y
  {"code": ..., "message": ...} en la raíz. El resto de la web no cambia.
- Ajusta las pruebas a la forma plana (r.json()["code"]) y agrega una prueba que
  compare cada error contra lo que espera app/src/services/api.js
  (data.code, data.message).
- Revisa el resto del contrato contra mock-api/server.js campo por campo (login,
  status, plates, upload, camera/state) por si hay otra diferencia igual, y
  repórtala.
- Commit y push de nexcam.
```

## Prompt 1: una sola alerta por placa + ubicación como enlace

(Ya está incluido completo en el Prompt 2; se deja como referencia.)

```text
Dos cambios en el bot de NEXORA (nexcam_telegram.py), sin preguntar:

1) Una sola alerta por placa. Hoy, si la cámara sigue viendo la misma placa, la app
   la reconfirma cada 60 s y el bot manda una alerta nueva cada vez (pasó con GCV471).
   - Todas las lecturas se siguen guardando en la base (no tocar la API de la app).
   - Alertas (🚨 en lista, ⚠️ posible): una por placa (la placa de la lista, no la
     leída) por sesión de cámara del teléfono, o por 30 min si no hay sesión
     (configurable con TG_REPETIR_MIN). Las repeticiones NO mandan mensaje nuevo:
     editan la alerta original (editMessageCaption si fue foto, editMessageText si
     fue texto) agregando "Vista N veces · última HH:MM" y el enlace a la última
     ubicación. Guarda message_id por chat para poder editar.
   - Lista en vivo "📷 Cámara encendida": una fila por placa con contador "×N" y la
     hora de la última lectura, en vez de una fila por lectura.
   - El encolado RGM automático tampoco se repite (ya debería por la regla de
     no duplicados; verifícalo).

2) Ubicación como enlace, no como mapa: elimina el sendLocation. En el texto de la
   alerta pon "📍 Ver ubicación" como enlace HTML a
   https://www.google.com/maps/search/?api=1&query=LAT,LNG
   y quita el botón "🗺️ Mapa" si queda duplicado con ese enlace (deja Ficha y Foto
   de la placa). Lo mismo en "ya la viste" y en la ficha por Telegram.

Pruebas con Telegram simulado: 5 lecturas seguidas de la misma placa = 1 alerta +
4 ediciones; ningún sendLocation; el enlace de ubicación presente. Commit y push de
nexcam.
```

## Prompt 2: rediseño del bot (formato de alerta, lista en vivo y bugs de la prueba real)

```text
Rediseño del bot de Telegram de NEXORA (nexcam_telegram.py), sin preguntar.
Incluye el arreglo de errores planos de la API si aún no lo hiciste.

1) Una alerta por placa: una por placa de la lista por sesión de cámara (o 30 min,
   TG_REPETIR_MIN). Las repeticiones EDITAN la alerta original (editMessageCaption /
   editMessageText) con "Vista N veces · última HH:MM". Todas las lecturas se siguen
   guardando. El RGM automático tampoco se repite.

2) Formato de la alerta (foto del vehículo + este texto, HTML):
   🚗 <b>GCV 471</b>
   🚨 EN LISTA · <motivo>        (⚠️ POSIBLE · leyó XXX para las posibles)
   <marca línea · color · modelo>   (omitir la línea si no hay datos)
   📅 En la lista desde: <vehiculos.fecha_ingreso, dd/mm/aaaa, h:mm a. m./p. m.>
   👁 Primera vez que se ve   |   Vista N veces · antes: <fecha de la anterior>
   🕘 <fecha y hora de la lectura> · confianza NN %
   👤 <operador> · <marca modelo del teléfono>
   📍 <a href="https://www.google.com/maps/search/?api=1&query=LAT,LNG">Ver ubicación</a>
   Botones: [📋 Ficha] [🔢 Placa] [🔎 RGM] (RGM solo si no hay datos RGM < 24 h).
   Nada de sendLocation; quita el botón Mapa. Hora siempre America/Bogota y
   formato colombiano (17/11/2026, 9:21 p. m.), también en "Cámara encendida",
   "ya la viste" y la ficha.

3) Lista en vivo: una fila por placa con contador ×N y hora de la última lectura;
   cabecera "📷 Cámara encendida · <operador> (<teléfono>)" y "Desde h:mm · N placas
   · M en lista". 🚨 en lista, ⚠️ posible, ▫️ resto.

4) Bugs vistos en la prueba real:
   - El mensaje en vivo decía "Teléfono" en vez de "HONOR ALT-NX3": el nombre del
     dispositivo no llega en camera.started / lecturas. Tómalo de nexcam_dispositivos.
   - Terminó con "Se perdió la señal" en vez de "Cámara apagada": revisa que
     POST /api/camera/state {activa:false} llegue y cierre la sesión (contrato igual
     a mock-api: body {"activa": bool}, auth Bearer + X-Device-UUID).

5) Ficha por Telegram (botón Ficha y /buscar) con el mismo estilo: 🚗 placa, estado,
   vehículo, 📅 en la lista desde, cada vez que se vio (fecha · lugar con enlace),
   datos RGM si hay (sensibles solo Admin).

Pruebas con Telegram simulado: 5 lecturas de la misma placa = 1 alerta + 4
ediciones; 0 sendLocation; fecha de ingreso presente; nombre del teléfono presente;
camera/state false cierra con "Cámara apagada". Commit y push de nexcam.
```

## Prompt 2b: los datos del RGM reescriben la placa y llegan a la app

```text
Rama nexcam, sin preguntar. Cuando llega un resultado RGM con estado "OK"
(automático, desde Consulta o desde Telegram):

1) Reescribe los datos del vehículo con lo del RGM (el RGM manda sobre la lista):
   - marca ← vehiculo.marca; modelo ← vehiculo.modelo; tipo_servicio ←
     vehiculo.tipo_servicio.
   - línea: el RGM la trae dentro de vehiculo.descripcion_bien ("...Linea: LOGAN");
     extráela de ahí (texto después de "Linea:" hasta el final o el siguiente
     rótulo). El color no viene en el RGM: no lo toques.
   - Guarda lo anterior en el historial/auditoría ("Actualizado con RGM el
     17/11/2026, 11:30 p. m.: marca X → Y") y muestra en la ficha
     "Datos del vehículo: según RGM del <fecha>".
   - Si el RGM no trae un campo, se conserva el que había.
   - Esto anula la regla anterior de "solo completar vacíos" para estos campos.

2) A la app NEXCAM le llegan esos datos del vehículo: /api/plates (sync completa e
   incremental) incluye marca, línea, modelo, color, tipo_servicio y motivo, y una
   placa actualizada por RGM entra en el ?since= siguiente (marca su cambio). Así la
   alerta del teléfono muestra "Renault Logan · 2020". NUNCA van a la app: deudor,
   documento, acreedor/banco, radicado ni procesos (regla Etapa 5 §2/§37).
   Verifica qué campos lee la app hoy (app/src/services/db.js plate_cache y
   CameraScreen) y repórtalo; si la app necesita un campo nuevo, dímelo y lo agrego
   yo en la app.

3) Telegram: la alerta usa esos datos ("Renault Logan · 2020 · Particular").
   El banco (acreedor) en la alerta solo para chats de Admin.

Pruebas con resultado RGM simulado (el JSON de ejemplo con descripcion_bien real de
formato, datos ficticios). Commit y push de nexcam.
```

### Cómo debe verse la alerta

```
🚗 GCV 471
🚨 EN LISTA · Aprehensión judicial
Chevrolet Spark · gris · 2016
🏦 Bancolombia · 📄 Oficio del 10/11/2026      (desde el Prompt 3)
📅 En la lista desde: 17/11/2026, 9:21 p. m.
👁 Primera vez que se ve
🕘 17/11/2026, 9:03 p. m. · confianza 78 %
👤 Eduardo Camacho · HONOR ALT-NX3
📍 Ver ubicación

[📋 Ficha] [🔢 Placa] [🔎 RGM]
```

### Cómo debe verse la lista en vivo

```
📷 Cámara encendida · Eduardo (HONOR ALT-NX3)
Desde 9:04 p. m. · 6 placas · 2 en lista

🚨 GCV 471  ×4  9:03
🚨 GCT 953       9:06
▫️ ABC 123       9:05
```

### Mejora anotada (no pedida todavía)

La cámara lee una placa por cuadro (la centrada en el recuadro). Leer varias placas en el mismo cuadro es posible, pero es un cambio de la app Android y exige recompilar el APK.

## Prompt 3: carga inteligente de placas, ficha, oficios, operadores por Telegram, estudio masivo y vigencia

Decisiones de Gabriel (17/11/2026): Telegram es la herramienta de trabajo (nada de chat dentro de la app NEXCAM); oficio = PDF adjunto a la placa (sin PDF = "Sin oficio"); garantía = banco; los oficios llegan nombrados por NEXDOC (juzgado + placa + banco); "Gestión masiva de placas" se queda igual, solo cambia cómo se pegan las placas; el estudio masivo usa RGM/Judicial; aviso de vigencia a los 3 meses, la baja siempre manual.

```text
Prompt 3 para NEXORA-CONTROL, rama nexcam, sin preguntar (CLAUDE.md). Bloques A a G,
en orden. Commit por bloque, push de nexcam al final, un solo reporte punto por punto.

A) CARGA DE PLACAS (dentro de "Gestión masiva de placas", que se queda igual)
NO cambies el nombre ni la estructura de "Gestión masiva de placas": sus pestañas
siguen siendo Carga masiva de placas · Baja masiva · Reactivación masiva ·
Consulta / Estudio masivo. Lo que cambia es CÓMO se pegan las placas en las tres
primeras pestañas, copiando el flujo de la app "Gestión de Placas" que usa Gabriel:
- Pestaña Carga masiva: sub-opciones [Texto] [Excel].
- Selector "Grado" (= Grado Operativo de camara.py) que se asigna a las placas cargadas.
- Caja "Pega texto de WhatsApp / Telegram / correo": extrae TODAS las placas LLLDDD
  del texto tal como venga: una por línea, varias por línea separadas por comas,
  espacios o punto y coma, y pegadas sin separador ("XYZ789WQU456" = 2 placas).
  Mayúsculas, sin guiones. Lo que no sea placa va a "omitidos" (mostrar cuáles).
- Contador en vivo: "N placas válidas detectadas · Ver lista" y "M omitidos · Ver".
  Duplicadas dentro del mismo texto cuentan una vez.
- Botón "Alistar": reescribe la caja con una placa por línea, ya limpias y ordenadas.
- Botón "Cargar placas": modal de confirmación (operación CARGA / ACTUALIZACIÓN,
  cuántas placas, grado). Luego, para cada placa que estaba DADA DE BAJA,
  pregunta una por una: placa, fecha de baja, quién la dio de baja; [Omitir]
  [Confirmar] y un "Aplicar a todas las que faltan". Reactivar queda en auditoría.
- Placas que ya estaban activas: se actualizan los datos que traiga la carga (lo que
  no traiga se conserva) y se conserva la fecha de ingreso original.
- Baja masiva y Reactivación masiva usan la misma caja inteligente (pegar cualquier
  texto → detectadas/omitidas → Alistar → confirmar).
- Excel/CSV: columnas reconocidas por encabezado (sin importar orden ni tildes):
  Placa, Tipo aprehensión, Banco, Modelo, Marca, Línea, Color, Fecha oficio.
- Resumen final: nuevas, actualizadas, reactivadas, omitidas, y "ya las vio la
  cámara" (esas se avisan por Telegram como "🗂️ Ya la viste").
- La MISMA función de extracción se usa en la web y en Telegram (una sola).
- Archivos .txt (incluido el chat exportado de WhatsApp, "Sin archivos"): se pueden
  arrastrar a las pestañas Carga / Baja / Reactivación, y también enviarse al bot.
  El NOMBRE del archivo decide la operación cuando se envía al bot o se suelta en
  la pestaña Carga: si empieza por "BORRAR" → Baja masiva; "REACTIVAR" →
  Reactivación; "CARGAR" o cualquier otro → Carga. Siempre se muestra el resumen y
  se pide confirmación antes de aplicar.
- Chat exportado de WhatsApp. Formato REAL (verificado con 3 exportaciones de
  grupos de Gabriel, 18/11/2026; ~5.000 placas de carro y ~900 de moto entre los 3):
  · Encabezado de mensaje "[m/d/aa, h:mm:ss AM/PM] Nombre: texto"
    (ej. "[9/16/26, 8:08:47 PM]": MES/DÍA, estilo EE. UU.). Puede traer un carácter
    invisible U+200E al inicio y U+202F antes de AM/PM. Acepta también el formato
    Android "dd/mm/aa, h:mm a. m. - Nombre: texto". Si algún primer número es > 12,
    es día/mes; si no, en el formato con [ ] y AM/PM, mes/día.
  · Un mensaje sigue en las líneas siguientes hasta el próximo encabezado (listas de
    cientos de placas en un solo mensaje).
  · Prefijo "[Reenviado]" al inicio del texto: se ignora.
  · Palabra "UBICAR" / "UBICAR BD" (y sus errores: UBCIAR, UBUCAR, UBICATR) = cargar.
  · Líneas "PLACA<TAB>CC<TAB>número" (ej. "LVZ077  CC  1234…"): se toma SOLO la
    placa para la lista; el número se guarda como documento del deudor (solo Admin,
    ver abajo) y nunca va a la app ni a operadores.
  · Ignorar: "<Mensaje de voz omitido>", "<Sticker omitido>", "<Imagen omitida>",
    "Se eliminó este mensaje.", "#####", "🚨🚨🚨", saludos y conversación.
  · Bajas dentro del mismo chat: un mensaje que contenga "CANCELAR", "CANCELAR
    BÚSQUEDA DEL VEHÍCULO DE PLACA X", "BAJAR DE BASES LA PLACA X" (también
    "ELIMINAR", "RETIRAR", "SACAR DE LA BASE", "CLIENTE AL DÍA", "INSOLVENCIA")
    → las placas de ESE mensaje (en la misma línea o en las siguientes) van a
    "Para dar de baja", no a "Para cargar". Cuenta el orden: el mensaje más reciente
    de cada placa decide (UBICAR después de CANCELAR = vuelve a cargar).
    En la prueba real: 17 placas marcadas para cancelar, 10 de ellas habían sido
    pedidas antes con UBICAR.
  · "CORREGIR X POR Y": X va a baja e Y a carga (las dos en el resumen).
  · Mensajes con ficha del vehículo (grupo "UBICACIONES": un mensaje por placa,
    ~245 de 273 traen ficha). Debajo de la placa vienen rótulos con el valor en la
    MISMA línea o en la SIGUIENTE: "Tipo de servicio:", "Clase de vehículo:",
    "Marca:", "Línea:", "Modelo:", "Color:", "SOAT si/no", "Tecnomecanica si/no".
    Guardar en vehiculos: marca, línea, modelo, color, tipo_servicio, clase
    (columna nueva, aditiva), soat y tecnomecanica (si/no, columnas nuevas). Es la
    única fuente del COLOR (el RGM no lo trae): el RGM después reescribe marca,
    línea y modelo, nunca el color.
  · En esos mismos mensajes también se guardan (decisión de Gabriel, 18/11/2026)
    el BANCO, el DEUDOR y las DIRECCIONES. Son datos sensibles: solo Admin, en la
    web y en Telegram; NUNCA a la app NEXCAM ni a operadores (regla de siempre).
    - Bloque "PARTES": 1 a 3 líneas hasta "DETALLE" o el siguiente rótulo. El orden
      cambia (a veces el banco primero, a veces la persona). La línea que parece
      financiera (BANCO, S.A., S.A.S, RCI, GM, CHEVYPLAN, FINANDINA, FINANZAUTO,
      FINSOCIAL, DAVIVIENDA, BBVA, SANTANDER, TOYOTA, CARROFACIL, PLANAUTOS,
      CAPITAL, COMPAÑIA DE FINANCIAMIENTO…) es el banco → vehiculos.banco; la línea
      con nombre de persona es el deudor → deudor_nombre. "DETALLE", "PARTICULAR"
      y líneas de vehículo ("• MARCA: …") no son partes. Si no se puede decidir
      cuál es cuál, se guarda en "partes sin clasificar" para revisar en la ficha.
    - Banco con nombre normalizado para filtrar y agrupar (ej. "RCI COLOMBIA S.A.
      COMPAÑIA DE FINANCIAMIENTO" y "RCI" → RCI; "GM FINANCIAL…" y "GM" → GM
      Financial; "BANCO DAVIVIENDA S.A." y "BANCO DAVIVIENDA" → Davivienda),
      conservando el texto original. Tabla de equivalencias editable en la web.
    - Documento del deudor: de la línea "PLACA<TAB>CC<TAB>número" → deudor_tipo_doc
      y deudor_documento (esto reemplaza la regla anterior de descartarlo; ahora se
      guarda, solo Admin).
    - "UBICACION": de 0 a ~14 líneas "dirección<TAB>CIUDAD ( DEPARTAMENTO )" hasta
      "SOAT", "Tecnomecanica" o el siguiente mensaje. Tabla nueva
      vehiculo_direcciones (placa, direccion, ciudad, departamento, fuente
      'whatsapp', fecha del mensaje, activa). Sin duplicar la misma dirección.
    - Ficha (web, Admin): bloque "Deudor y direcciones registradas" con banco,
      deudor, documento y la lista de direcciones, cada una con enlace "Ver en el
      mapa" (Google Maps con dirección + ciudad); se pueden editar, agregar y
      marcar como "ya no vive ahí". Origen visible ("del chat del 16/09/2026").
    - Telegram (solo Admin): en la ficha, botón [🏠 Direcciones (N)] que manda la
      lista con enlaces al mapa. En la alerta de un Admin, línea
      "🏠 N direcciones registradas". Nada de esto en alertas de operadores.
    - Si después llega el RGM: el acreedor y el deudor del RGM se muestran como
      "según RGM del <fecha>" y, si difieren del chat, se ven los dos (no se borra
      lo del chat). Las direcciones no las toca el RGM.
    - Columnas y tablas nuevas con migración aditiva y copia de la base antes.
  · "UBICAR A" / "UBICAR L": la letra no significa nada; se ignora.
  · "YA FUE CAPTURADA" (y "CAPTURADO", "RECUPERADO") también cuenta como baja.
    Ojo: "aprehensión" NO es palabra de baja (aparece en mensajes de carga).
  · Motos (LLLDDL, ej. BEE48H): lista aparte "N motos (la cámara no las busca)",
    con casilla "Guardar también las motos" (desactivada por defecto; si se marca,
    se guardan con la marca "moto", igual que en el bloque G).
  · Placas cortadas o raras ("MC0350…", "SZS", "387") → omitidos.
  · Un mismo chat repite las mismas listas muchas veces (un grupo tenía ~2.600
    placas repetidas): se cuentan una vez, con la PRIMERA fecha como fecha de
    ingreso (opción "Usar la fecha del mensaje como fecha de ingreso", activada por
    defecto cuando se detecta el formato).
  · No se guarda el nombre ni el teléfono de quien escribió en el grupo, ni la
    conversación: solo placa, fecha, operación y los datos de ficha descritos aquí.
- Se acepta el .zip que entrega WhatsApp al exportar (lee el chat.txt de adentro;
  ignora el resto) y VARIOS archivos a la vez: un solo resumen combinado, sin
  duplicar entre archivos, con una línea por archivo ("Placas_Base_Captucol.zip:
  3.604 placas, 1.210 nuevas"). La regla del nombre (CARGAR/BORRAR/REACTIVAR) se
  aplica al nombre del .zip o del .txt.
- Volumen: una carga de 5.000 placas se aplica en una sola transacción (con copia
  de seguridad de MasterPlacas.db antes, regla de CLAUDE.md), no placa por placa,
  y sin mandar un mensaje de Telegram por placa: un solo resumen al terminar. La
  lista "Ver lista" se pagina.
- Resumen antes de aplicar, en este orden: para cargar (nuevas / ya estaban activas
  / estaban dadas de baja), para dar de baja (estaban activas / no estaban),
  motos, omitidos. Cargar y dar de baja se confirman por separado.

Telegram (solo usuarios Admin vinculados):
- Pegar cualquier texto en el bot (o reenviar un mensaje de WhatsApp) → responde
  "📥 N placas detectadas · M omitidas · K estaban dadas de baja · J ya las vio la
  cámara" con botones [✅ Cargar N] [🗑 Dar de baja N] [👁 Ver lista] [✖ Cancelar].
  Si K > 0, después de cargar: [♻️ Reactivar las K] [Omitir] (en grupo, no una por
  una). El botón "📥 Cargar lista" y /cargar siguen existiendo.
- Telegram admite ~4.000 caracteres por mensaje (≈500 placas); para más, archivo
  .csv o .xlsx enviado al bot.

B) VEHÍCULOS: tabla limpia + ficha editable
- La fila de la tabla hoy se corta (columnas y botón "Dar de baja" se salen). Tabla:
  placa · tipo de aprehensión · banco · oficio (✓ con PDF / ✗) · última vez vista ·
  estado · [Ficha]. Sin botones de baja en la fila; en celular, scroll horizontal
  dentro de su caja.
- Campos nuevos en vehiculos (migración aditiva): tipo_aprehension (texto libre con
  sugerencias de los valores ya usados), banco, fecha_oficio, modelo (si no existe).
  marca/linea/color ya existen.
- Ficha /vehiculos/{placa}: bloque "Datos" editable (todos los campos de arriba),
  bloque "Oficio" (ver C), "Dónde se ha visto", RGM, Judicial, y "Dar de baja" al
  final de la ficha con confirmación.

C) OFICIOS
- Regla: una placa "tiene oficio" SOLO si tiene un PDF adjunto. Sin PDF = "Sin oficio".
- Nueva entrada de menú "Oficios":
  · Subir varios PDF a la vez (arrastrar y soltar o elegir; típicamente 5-10). Los
    nombra NEXDOC con juzgado + placa + banco: lee el código de NEXDOC en
    C:\Users\Rus\Documents\Trabajo\Placas\_App Ordenes (SOLO LECTURA: no copies
    datos reales, ni Excel, ni config.json, ni nada de "NEXDOC llave") para saber
    exactamente cómo arma el nombre del archivo y parsearlo igual. De cada archivo
    saca placa, banco, juzgado y, si el nombre la trae, la fecha del oficio.
  · Antes de guardar, tabla de revisión: archivo · placa · banco · juzgado · fecha
    del oficio (editable si no vino) · estado (placa en lista ✓ / no está en la
    lista → botón "Agregar a la lista"). Botón "Guardar oficios".
  · Si la placa ya tenía oficio: guardar el nuevo como vigente y conservar el
    anterior en el historial de la ficha.
  · Lista de oficios con buscador y filtros (con placa en lista / sin placa / por
    banco / por fecha).
- PDFs guardados fuera del repo (carpeta datos_nexcam/oficios/, en .gitignore),
  servidos solo a Admin con sesión; nombre interno aleatorio, ruta validada.
- Telegram: un Admin puede enviar PDFs al bot y se procesan igual (misma revisión,
  resumida: "3 oficios: GCV471 Bancolombia ✓, ... [Guardar] [Ver en la web]").
- Alerta cuando la cámara ve una placa CON oficio: al Admin le llega la alerta y,
  justo debajo, el PDF (sendDocument, respondiendo a la alerta). Al operador que la
  vio le llega "📄 Con oficio" y el PDF solo cuando el Admin pulsa
  [📤 Enviar oficio a <operador>] en su alerta. Sin oficio: la alerta dice
  "📄 Sin oficio".
- Línea nueva en la alerta (después de la del vehículo):
  🏦 <banco> · 📄 Oficio del <fecha_oficio>   |   📄 Sin oficio

D) OPERADORES: Telegram es la herramienta de trabajo (nada en la app NEXCAM)
- Cada operador vincula su propio Telegram al bot (código /vincular, asociado a su
  usuario de NEXORA). La app NEXCAM no cambia: solo cámara y envío de lecturas.
- Cuando la cámara de un operador ve una placa en lista: la alerta completa (foto,
  datos no sensibles, ubicación) le llega A ÉL en su Telegram. Al Admin le llega una
  sola línea de texto EN SILENCIO (disable_notification=true, sin foto):
  "🔕 <operador> vio GCV 471 (en lista) · 9:03 p. m. · [Abrir]" (botón que muestra
  la alerta completa con foto en el chat del Admin si la quiere ver), y todo queda
  en la web.
- Chat operador ↔ Admin a través del bot:
  · Lo que el operador escribe al bot (que no sea un botón ni un comando) le llega
    al Admin como "💬 <operador>: <texto>" (con fotos/ubicaciones si las manda), con
    botón [↩️ Responder]. El Admin responde citando ese mensaje (reply) o con el
    botón, y el bot se lo entrega al operador como "💬 Admin: <texto>".
  · Si el operador responde a una alerta concreta, el mensaje del Admin muestra a
    qué placa se refiere.
  · Todo queda guardado (tabla chat_mensajes) y visible en la web en
    "NEXCAM → Operadores": una conversación por operador, de solo lectura con opción
    de escribir desde la web (llega al operador por Telegram).
  · Los datos sensibles siguen la regla de siempre: el bot nunca le reenvía al
    operador deudor, documento, radicado ni procesos.

E) CONSULTA / ESTUDIO MASIVO con RGM y Judicial (pestaña existente de Gestión masiva)
- Se queda la pantalla actual (período, cifras, lista, ficha a la derecha). Lo nuevo:
  junto a "Iniciar estudio masivo", el mismo selector segmentado de Consulta:
  [RGM] [Judicial] [RGM + Judicial] (Judicial y Completo solo Admin).
- Al iniciar, las placas del período/filtro se encolan en rgm_cola con ese alcance
  (origen 'estudio', prioridad más baja que cámara y Telegram). La pestaña Consulta,
  abierta en Chrome, las va atendiendo como cualquier otra consulta. NO crear un
  segundo mecanismo: el estudio existente (cola_estudio) pasa a usar rgm_cola, o se
  conecta a ella; elige lo más simple y anótalo.
- Las cifras (Actualizadas / Pendientes / Sin datos) y "Última consulta" de la lista
  se calculan con los snapshots RGM/JUDICIAL reales. "Priorizar" sube la placa en
  rgm_cola. Pausar y cancelar siguen funcionando.
- Agregar el período "Más de 3 meses" (placas con más de 90 días en el sistema).

F) REVISAR VIGENCIA (aviso, nunca borrado automático)
- Placas activas con más de N días desde su ingreso (o desde su última revisión)
  → aviso en la página Hoy: "N placas llevan más de 3 meses en el sistema: revisa si
  siguen vigentes". N = 90 por defecto, configurable (90-120).
- El aviso abre una lista: placa · días en el sistema · banco · oficio ✓/✗ · última
  vez vista · RGM/Judicial más reciente, con acciones por placa y en grupo:
  [Sigue vigente] (guarda fecha_revision = hoy y la saca del aviso por otros N días)
  [Dar de baja] (confirmación, auditoría) [Ficha] [Estudiar] (la encola en el
  estudio masivo con el alcance elegido).
- Contador pequeño junto a "Hoy" en el menú cuando haya placas por revisar.
- Nada se da de baja solo: la decisión es siempre del Admin.

G) OFICIOS DESDE NEXDOC (cada Admin, desde su propio computador)
Contexto verificado en el código de NEXDOC (C:\Users\Rus\Documents\Trabajo\Placas\_App Ordenes,
solo lectura): NEXDOC archiva en Placas\<Departamento>\<Municipio>\ con nombre
<DANE><esp><NN>-<PLACA>-<FINANCIERA>-<AUTO|OFICIO>[-CORREGIDO][-2].pdf
(esp: C, P, PC, CC; ej. 05088C01-LGT748-GMAC-OFICIO.pdf), y ya extrae placa,
radicado, juzgado, municipio, depto, financiera, tipo, fecha, correo del juzgado y
vehículo (marca, línea, modelo, color). Google Drive es opcional y personal: NO se usa.
NEXDOC enviará cada documento directo a NEXORA por la red (ver Prompt 4).
- AUTO u OFICIO: cualquiera de los dos cuenta como "tiene documento" (✓). Mostrar el
  tipo: "📄 Oficio del <fecha>" o "📄 Auto del <fecha>".
- API nueva: POST /api/nexdoc/documentos (multipart: archivo PDF + "datos" JSON con
  los campos de NEXDOC) y GET /api/nexdoc/estado (prueba de conexión). Auth: token
  propio de NEXDOC (Bearer), NO la sesión web ni el token de la app NEXCAM.
- Tokens: en Usuarios → ficha de un usuario → "Conectar NEXDOC": genera un token
  (se muestra una sola vez, se guarda solo su hash), con nombre del equipo, último
  uso y botón Revocar. Solo un Admin genera tokens, para Admins o para operadores.
- Subir documentos A MANO (web "Oficios" y PDFs enviados al bot de Telegram): solo Admin.
- Lo que llega desde NEXDOC:
  · de un ADMIN: se guarda directo. Si la placa (LLLDDD) no está en la lista, se
    agrega sola (banco, tipo de aprehensión vacío, grado por defecto) y se avisa.
  · de un OPERADOR (NEXDOC lo envía solo, en segundo plano, sin preguntarle): entra
    como "De operador · por validar". Queda adjunto a la ficha marcado así, y un
    Admin lo valida o descarta (web y botón en Telegram). Si la placa no está en la
    lista, NO se agrega sola: se ofrece "Agregar a la lista" al validar.
  · motos (LLLDDL) y placas raras: se guardan con su documento pero la cámara no
    las busca; se marcan "moto".
- Sin duplicados por SHA-256 del PDF. "-CORREGIDO" reemplaza al vigente de esa
  placa y tipo; el anterior queda en el historial de la ficha. Responde
  {"ok": true, "placa": "...", "estado": "nuevo"|"duplicado"|"reemplaza"|"por_revisar"}.
- Completar en vehiculos marca/línea/modelo/color si están vacíos; guardar
  radicado (string), juzgado, municipio, departamento, correo del juzgado, fecha.
- Telegram (Admin): resumen agrupado cada 2 min como máximo:
  "📄 3 documentos nuevos desde NEXDOC (Juan): LGT 748 GMAC oficio, …"
  y botón [Revisar] si hay alguno por revisar. Nunca un mensaje por archivo.
- Pruebas con PDFs falsos y nombres reales de formato (sin datos reales).

Pruebas (Telegram y portales simulados, PDFs falsos generados en la prueba):
extracción de placas con todos los formatos (incluye pegadas y textos con basura);
reactivación una por una y en grupo; tabla de revisión de oficios con nombres al
estilo NEXDOC; alerta con PDF al Admin y sin PDF al operador hasta que el Admin lo
envíe; aviso silencioso al Admin; ida y vuelta del chat; ningún dato sensible al
operador.
```

## Prompt 4: NEXDOC envía los documentos a NEXORA

Se hace DESPUÉS del bloque G del Prompt 3 (necesita la API de NEXORA). Se trabaja en la carpeta de NEXDOC, no en NEXORA-CONTROL.

```text
Trabajo en NEXDOC (C:\Users\Rus\Documents\Trabajo\Placas\_App Ordenes), sin preguntar
(mismas reglas de CLAUDE.md de NEXORA-CONTROL). Si la carpeta tiene git, rama
`conexion-nexora`; si no, haz primero una copia de respaldo de la carpeta del código.
NUNCA toques: firma.nxd, nexdoc_licencia.py (salvo leerlo), la carpeta
"Documents\NEXDOC llave", el Excel REGISTRO DE RADICADOS real ni ningún PDF real.

1) Conexión con NEXORA:
   - En configuración: "Conectar con NEXORA": dirección (ej. http://192.168.1.2:8000)
     y token (lo genera un Admin en NEXORA → Usuarios → Conectar NEXDOC). Botón
     "Probar conexión" (GET /api/nexdoc/estado). Se guarda en el config.json del
     usuario (USR_DIR), nunca en el repositorio.
   - Esa sección la configura un Admin de NEXDOC (nexdoc_usuarios / nexdoc_acceso):
     un usuario operador no puede verla, cambiarla ni apagarla. Una vez configurada,
     el envío es automático: NUNCA se le pregunta al operador si sube o no un archivo.
   - Transparencia (una sola vez, no por archivo): agrega en LEEME/manual y en la
     pantalla de licencia la frase "Los documentos que procesa NEXDOC se sincronizan
     con el sistema NEXORA de la empresa".
   - Indicador en el encabezado, junto a "Drive conectado": "NEXORA conectado" /
     "NEXORA: N por enviar" / "NEXORA sin conexión".
2) Envío en segundo plano: cada vez que NEXDOC guarda un AUTO u OFICIO (guardar /
   confirmar / procesar, incluidas las "hermanas"), encola el envío a
   POST /api/nexdoc/documentos: el PDF + JSON con placa, radicado, codigo, juzgado,
   municipio, depto, financiera, tipo, fecha, correo_juzgado, correo_policia,
   vehiculo (marca, línea, modelo, color por separado; hoy se juntan en
   res["vehiculo"]: guarda también los campos sueltos), usuario, nombre del archivo.
   - Hilo aparte: la ventana nunca se congela. Cola persistente en USR_DIR
     (como pendientes_ws.json), reintentos con espera creciente, sobrevive a cerrar
     la app. Lo que responde "duplicado" se da por enviado.
3) "Enviar lo ya archivado a NEXORA": botón que recorre Placas\<depto>\<municipio>\
   y encola todos los PDF con nombre de formato NEXDOC (los de formato viejo
   "2401-..." se omiten), con progreso y sin duplicar.
4) Excel REGISTRO DE RADICADOS: agrega al final las columnas Marca, Línea, Modelo,
   Color y Correo juzgado, igual que se agregó "Usuario" (registros viejos: se añade
   la columna sin tocar lo demás).
5) Sube VERSION en nexdoc_rutas.py (1.4.2 → 1.5.0) y anota en CHANGELOG.md.
6) Pruebas con PDFs falsos y un servidor NEXORA simulado: envío, cola sin conexión,
   reintento, duplicado, backfill, columnas nuevas en un Excel de prueba.
Reporte final punto por punto y cómo publicarlo para los demás con
NEXDOC-actualizaciones.
```

## Prompt 5: RGM completo en la ficha, avisos solo a pedido, En vivo compacto y Telegram más legible

Resultado de la prueba real del 18/11/2026: el RGM ya funciona (20 placas consultadas OK). Lo que se corrige aquí sale de esa prueba.

```text
Rama nexcam, sin preguntar (CLAUDE.md). Commit por punto, push al final, reporte
punto por punto. Incluye el Prompt 2b si aún no lo hiciste.

1) RGM automático = SILENCIOSO. Hoy cada consulta automática manda a Telegram
   "📄 RGM consultado: GCV 471 · Vehículo: RENAULT · Consulta hecha" (llegaron 20
   mensajes seguidos). Cambia:
   - Consultas automáticas (cámara, estudio masivo): solo guardan y completan la
     ficha. NO mandan mensaje.
   - Consultas pedidas por un Admin (botón 🔎 RGM en Telegram, o Consultar en la
     página Consulta): al terminar, un mensaje al chat de quien la pidió (si la
     pidió desde la web: a los chats de Admin) con los datos registrados:
       🚗 <b>GCT 953</b> · RGM del 18/11/2026, 12:37 a. m.
       Chevrolet Tracker · 2021 · Particular
       Serie/VIN: … · Motor: …
       🏦 Acreedor: …            (solo Admin)
       👤 Deudor: nombre · C.C. · ciudad   (solo Admin)
       [📋 Ficha] [⚖️ Procesos judiciales (en prueba)]
     Si el RGM no trae datos: "RGM sin datos para GXX 931".

2) RGM llena TODAS las partes del vehículo (vehiculos + ficha), reescribiendo lo
   que había (el RGM manda; lo anterior va a auditoría):
   marca, línea (de descripcion_bien "Linea: X"), modelo (año), tipo_servicio,
   número de serie/VIN, chasis y motor (de descripcion_bien "Chasis:", "Vin:",
   "Motor:", "Serie:"). Agrega esas columnas con migración aditiva. El color NO
   viene en el RGM: se conserva el de la lista o NEXDOC.
   Hoy la ficha de GCT953 muestra "CHEVROLET / —" y "— / —" aunque el RGM trae
   línea TRACKER y modelo 2021: eso debe quedar lleno.

3) Ficha → "Datos RGM": hoy muestra el JSON crudo. Muéstralo como tabla legible
   (Vehículo: marca, línea, modelo, servicio, serie, motor · Acreedor · Deudor con
   nombre, documento y ciudad), fecha de consulta, y el JSON crudo solo detrás de un
   "Ver datos originales" plegado.

4) NEXCAM → En vivo: hoy cada fila tiene la foto enorme. Hazlo compacto: filas de
   ~56 px con miniatura de 72×48 (vehículo) y 48×32 (placa); al tocar una miniatura
   se abre ampliada en un visor (overlay oscuro, Esc o clic fuera para cerrar,
   flechas para pasar a la siguiente lectura). Paginación de 30.

5) Telegram, lista en vivo "📷 Cámara encendida": solo las ÚLTIMAS 10 placas
   (configurable TG_VIVO_MAX), la más reciente arriba; al llegar la 11ª sale la más
   vieja. La cabecera sigue contando el total de la sesión ("31 placas · 2 en lista").
   Formato de cada fila, más grande y fácil de leer en la calle:
     🔴 🚗 𝗚𝗖𝗩 𝟰𝟳𝟭   1:09     (en lista)
     🟡 🚗 𝗔𝗕𝗤 𝟳𝟳𝟳   1:09     (posible)
     ⚪ 🚗 𝗟𝗤𝗥 𝟵𝟱𝟭   1:09     (leída)
   La placa en letras Unicode "negrita sans" (𝗔-𝗭, 𝟬-𝟵), que en Telegram se ven
   más gruesas y de otra fuente (Telegram no permite cambiar el tamaño de letra).
   Hazlo con una función única placa_destacada(texto) y úsala también en la lista
   de "🕒 Últimas". En alertas y fichas la placa sigue en texto normal en negrilla
   (para poder copiarla y buscarla).

6) Telegram, "🕒 Últimas": paginada, la más reciente primero, 10 por página:
   "🕒 Últimas placas · página 1 de 4" + filas con el formato del punto 5 + hora y
   operador; botones [◀ Anterior] [1/4] [Siguiente ▶] que EDITAN el mismo mensaje
   (no mandan otro); y debajo los botones 📋 de las placas de esa página en 2
   columnas.

Pruebas con Telegram y RGM simulados: consulta automática no manda mensaje; a
pedido sí, con datos y sensibles solo a Admin; línea/serie/motor extraídos de
descripcion_bien; lista en vivo con máximo 10; Últimas paginada edita el mensaje.
```

## Prompt 6: Telegram ordenado, datos del vehículo bajo la placa y "Últimas" en vivo

Resultado de las pruebas 16.1 a 16.6 (18/11/2026, 3:20 a. m.): 16.4 OK. Lo demás se corrige aquí.

```text
Rama nexcam, sin preguntar (CLAUDE.md). Commit por punto, push al final, reporte
punto por punto con la sección "Pruebas para tu visto bueno".

1) Un solo formato de ficha en Telegram (alerta, respuesta del 🔎 RGM, botón
   📋 Ficha y /buscar), armado por UNA función. Hoy el RGM a pedido se pega al
   final de la alerta y queda desordenado ("🚗 GCT 953" repetido, "Consulta hecha.",
   "En tu lista: CHEVROLET", "Vista 4 veces. Última…", "Antes:"). Debe quedar así,
   en este orden, omitiendo las líneas sin datos:
     🚗 <b>GCT 953</b>
     🚨 En lista: UBICAR                (el motivo; si está vacío: "UBICAR",
                                          nunca "sin motivo")
     📅 Ingreso: 13/09/2026, 10:05 p. m.
     🚙 Chevrolet Tracker · 2021 · Particular · <color si hay>
     🔩 Serie/VIN: … · Motor: …
     🏦 Acreedor: …                      (solo Admin)
     👤 Deudor: nombre · C.C. · ciudad    (solo Admin)
     📄 RGM del 18/11/2026, 12:37 a. m.
     🗺️ Ubicaciones (4)
     • 18/11/2026, 3:06 a. m. · Eduardo Camacho · <a href=…>Ver</a>
     • 18/11/2026, 12:25 a. m. · Eduardo Camacho · <a href=…>Ver</a>
     (la más reciente arriba; máximo 8 y "… y N más en la web")
   - Elimina "Consulta hecha.", "En tu lista: …", "Vista N veces. Última: …" y
     "Antes:" (eso ya lo dice la lista de Ubicaciones).
   - En una ALERTA, arriba de 🗺️ va la línea de la lectura actual: "🕘 18/11/2026,
     3:06 a. m. · confianza 78 % · 👤 Eduardo Camacho".
   - Cuando llega el RGM a pedido de una alerta: se EDITA esa alerta con el formato
     completo (no se agrega texto al final ni se manda otro mensaje). Si la
     consulta se pidió por /buscar o desde la web: un mensaje nuevo con ese formato.
   - Marca, línea y servicio con mayúscula inicial ("Chevrolet Tracker"), no todo
     en mayúsculas.
   - Respeta el límite de 1024 caracteres del pie de foto: si no cabe, recorta la
     lista de Ubicaciones primero.

2) Web, ficha del vehículo: los datos del vehículo van DEBAJO DE LA PLACA, en el
   bloque principal (hoy dice "CHEVROLET / —" y "— / —" aunque el RGM trae
   TRACKER 2021). Bloque principal: Tipo · Marca / Línea · Modelo · Color ·
   Servicio · Serie/VIN · Motor · Chasis · En la lista desde · Registrado por ·
   Fecha de baja, con "según RGM del <fecha>" en pequeño.
   - La caja lateral "Datos RGM" ya no repite el vehículo: solo fecha de consulta,
     Actualizar, Acreedor y Deudor (solo Admin) y "Ver datos originales".
   - Arreglo de datos viejos: los RGM guardados ANTES del Prompt 2b nunca llenaron
     la placa. Haz una vez (al arrancar, con copia de MasterPlacas.db antes y
     anotado en auditoría) que el último RGM "OK" de cada placa se aplique a
     vehiculos con la misma regla del 2b. Así GCT 953 queda Chevrolet Tracker 2021
     sin volver a consultar.

3) "🕒 Últimas" en vivo: hoy es una lista paginada. Gabriel quiere que se vaya
   actualizando sola mientras la cámara lee:
   - Al pulsar 🕒 Últimas, el bot manda UN mensaje con las últimas 5 placas leídas
     (formato del Prompt 5: 🔴/🟡/⚪ 🚗 placa gruesa · hora · operador) y lo EDITA
     cada vez que entra una lectura nueva: la nueva entra arriba y la más vieja
     sale. Ese mensaje sigue vivo hasta que se pulse 🕒 Últimas otra vez (el viejo
     queda fijo con "⏸ Ya no se actualiza") o pasen 12 h.
   - Máximo una edición cada 3 s por mensaje (si llegan varias lecturas juntas se
     juntan en una edición), para no chocar con los límites de Telegram.
   - Debajo, [◀ Anteriores] para ver páginas viejas (5 por página) y [🔴 En vivo]
     para volver a la vista en vivo. Mientras se mira una página vieja, no se
     actualiza.
   - Botones 📋 de las 5 placas visibles en 2 columnas.

4) Demora de los botones (Gabriel nota "mucho delay al pasar de páginas"):
   - answerCallbackQuery INMEDIATO al recibir el botón, antes de calcular nada.
   - Las ediciones que responden a un botón NO pasan por la cola de envíos de las
     alertas: van directo (o por una cola aparte de alta prioridad).
   - getUpdates en su propio hilo, sin esperar envíos. Meta: el mensaje cambia en
     menos de 1 s. Mide y reporta el tiempo antes y después.
   - Lo mismo para "📷 Cámara encendida" / "⏹️ Cámara apagada": Gabriel nota
     demora al prender y apagar. POST /api/camera/state debe responder al teléfono
     de una vez y el cambio del mensaje en Telegram ir por la cola de alta
     prioridad (no detrás de alertas, fotos ni RGM). Meta: menos de 2 s desde que
     se toca ⏻ hasta que cambia el mensaje. Mide y reporta.

5) Web, Vehículos (tabla "Inventario de vehículos"): el botón rojo de cada fila
   (dar de baja) sigue saliéndose por la derecha y se ve cortado. Adelanta aquí esa
   parte del bloque B del Prompt 3:
   - Quita el botón rojo de la fila; en la fila solo queda [Ficha]. "Dar de baja"
     pasa al final de la ficha, con confirmación (y queda en auditoría).
   - La tabla entra completa en su caja a 1366 px y 1440 px de ancho (sin cortar
     columnas); en pantallas más angostas, scroll horizontal DENTRO de la caja.
   - Agrega la columna "Vehículo" (marca y línea, ej. "Chevrolet Tracker") entre
     Tipo y Estado.
   Revisa con capturas de Playwright a 1366 y 1440 px antes de dar por hecho.

6) Cada día empieza en 0 (la app 0.5.5 ya lo hace en el teléfono): en la web
   (Hoy, NEXCAM) y en Telegram las cifras "N placas · M en lista" son del día
   (desde medianoche, America/Bogota). La lista en vivo "📷 Cámara encendida" que
   sigue abierta al pasar la medianoche cierra con el resumen del día y arranca
   un mensaje nuevo. Nada se borra: todas las lecturas (también las "sin novedad",
   con fotos y ubicación) siguen guardadas en NEXORA.

7) "Ya la viste" completo: cuando entra a la lista una placa que la cámara ya vio
   antes (alta individual en Vehículos, Carga masiva, Telegram, NEXDOC o
   reactivación), NEXORA busca en TODAS las lecturas guardadas (también las "sin
   novedad") y avisa:
   - Telegram (Admin): "🗂️ Ya la viste: GCT 953" con la foto del vehículo de la
     última vez + formato del punto 1 (vehículo si hay datos) + 🗺️ Ubicaciones
     con cada vez que se vio (fecha · operador · enlace al mapa). Un mensaje por
     placa (máximo 10 por carga y luego "… y N más, ver en la web").
   - Web: en el resumen de la carga y arriba de la ficha, un aviso "La cámara ya
     vio esta placa N veces (última: <fecha>, <lugar>)" con las fotos.
   - Verifica que hoy se suben al servidor todas las lecturas con fotos (no solo
     las de la lista) y que no se borran a los pocos días; si hay limpieza
     automática, que conserve al menos 180 días (configurable) y lo reportas.

Pruebas con Telegram simulado: formato único (orden de las líneas, sin "Consulta
hecha", sin "En tu lista"); motivo vacío → UBICAR; RGM a pedido edita la alerta;
ficha web con datos bajo la placa; arreglo de RGM viejos aplicado una sola vez;
Últimas: 7 lecturas → el mensaje muestra las 5 últimas y se editó ≤ 3 veces en 3 s;
el botón responde sin pasar por la cola de alertas.
```

## Prompt 6b: correcciones de la prueba del Prompt 6 (20/11/2026)

Resultado: 16.11 OK, 16.18 OK. Fallan o faltan detalles en 16.17, 16.9/16.10, 16.12/16.13 y 16.20.

```text
Rama nexcam, sin preguntar (CLAUDE.md). Hazlo apenas termines lo que estés haciendo
(antes del Prompt 9). Commit por punto, push al final, reporte con "Pruebas para tu
visto bueno" (16.9, 16.12, 16.13, 16.17, 16.20 de nuevo).

1) Vehículos (16.17): la tabla todavía muestra una barra de desplazamiento
   horizontal blanca. Gabriel quiere las columnas "a justo": que la tabla quepa
   completa en su caja sin barra a 1280, 1366 y 1440 px (anchos de columna
   ajustados al contenido, placa y botón Ficha sin cortar, Vehículo en dos líneas
   si hace falta, fechas más compactas "13/09/2026 10:05 p. m."). Solo por debajo
   de 1024 px se permite el desplazamiento, y entonces con barra oscura del tema
   (scrollbar-color / ::-webkit-scrollbar), nunca blanca. Vehículo con mayúscula
   inicial ("Chevrolet Tracker"). Capturas con Playwright a los 3 anchos.

2) Botón 🔎 RGM en las alertas (16.9/16.10): Gabriel no lo ve. Hoy se oculta si hay
   RGM de menos de 24 h. Cámbialo: el botón SIEMPRE está en la alerta y en la
   ficha de Telegram; si ya hay RGM reciente dice "🔄 Actualizar RGM", si no hay,
   "🔎 Consultar RGM". (Admin; operadores no lo ven.) Revisa también por qué
   placas de la lista vistas por la cámara (ej. JXO455, cargada 20/11 3:48 a. m.)
   quedaron sin datos: si el RGM automático no corrió, que la ficha diga
   "Sin RGM todavía · [Consultar]" y reporta la causa.

3) Últimas y botones lentos (16.12/16.13): Gabriel no nota mejoría. Mide de verdad
   en su PC, no solo en pruebas: agrega un log de tiempos por botón (recibido de
   Telegram → respuesta enviada) y reporta los números de 10 toques. Revisa:
   - getUpdates con long polling real (timeout=25-50) y SIN sleep entre llamadas;
     si hay un time.sleep o un intervalo fijo de sondeo, quítalo.
   - que el hilo de getUpdates nunca espere a la base, a fotos ni a envíos;
     callbacks atendidos en un hilo/pool aparte.
   - que armar la página de Últimas no haga consultas pesadas (índices en
     lecturas por fecha; máximo 1 consulta por página).
   - que las ediciones del mensaje vivo de Últimas no se encolen detrás de las
     ediciones del paginado.
   Meta medida: menos de 1 s entre el toque y el cambio en 9 de 10 toques.

4) "🗂️ Ya la viste" tarda en llegar (16.20): envíalo por la cola de alta prioridad
   apenas se da de alta la placa (no al final de un proceso ni por un trabajo
   periódico). Meta: menos de 5 s. Reporta cuánto tardaba y cuánto tarda.
```

## Prompt 6c: botones lentos, búsqueda en vivo y chat sin usuario (prueba del 20/11/2026, 3:35 p. m.)

Resultado: 16.17 OK, 16.20 OK, 17.1 OK, 17.4 OK. 16.9/16.10: el botón RGM no salía porque el chat de Telegram de Gabriel no tenía usuario de NEXORA asignado (`usuario_id` vacío → `es_admin=False`); por eso tampoco veía acreedor ni deudor. Se arregla asignándolo en NEXCAM → Telegram; el punto 3 de abajo evita que vuelva a pasar sin aviso.

```text
Rama nexcam, sin preguntar (CLAUDE.md). Commit por punto, push al final, reporte con
"Pruebas para tu visto bueno".

1) CAUSA ENCONTRADA EN EL LOG DE GABRIEL (20/11/2026): todas las líneas dicen
   TIEMPO_BOTON ack≈21,7 s y total≈43,4 s (= 2 × 21,7 s). 21 s es exactamente el
   tiempo que Windows espera para darse por vencido al abrir una conexión TCP
   (3+6+12 s de reintentos). Es decir: CADA llamada a api.telegram.org con
   urllib.urlopen abre una conexión nueva, primero intenta una dirección que no
   responde (casi seguro IPv6, que su red no tiene), espera 21 s y luego cae a
   IPv4. Eso explica la demora de TODO: botones, alertas, "Ya la viste",
   cámara encendida/apagada.
   Arreglo (sin dependencias nuevas, solo librería estándar):
   - Un cliente HTTP propio para Telegram que fuerce IPv4 (resolver
     api.telegram.org con socket.getaddrinfo(..., family=AF_INET) y conectar a esa
     IP con SNI/Host correctos, p. ej. http.client.HTTPSConnection con
     server_hostname, o un parche acotado de getaddrinfo SOLO para ese host) y que
     REUTILICE la conexión (keep-alive, una conexión por hilo: getUpdates, envíos
     normales, envíos prioritarios/botones), reconectando si se cae.
   - Timeout de conexión 5 s y de lectura 35 s (getUpdates) / 15 s (resto).
   - Configurable TG_FORZAR_IPV4=1 (por defecto sí) por si algún día su red tiene IPv6.
   - Log por llamada: método, tiempo de conexión y tiempo total (TIEMPO_TG).
   - Prueba automática con un servidor simulado que solo escucha en IPv4 y un
     host que también resuelve a una IPv6 inalcanzable: la llamada no debe tardar
     más de 1 s.
   - Además: "no se pudo paginar Últimas - HTTP 400": lee la descripción del 400;
     si es "message is not modified", ignóralo en silencio; si es otro, regístralo
     con el texto completo.
   Después de esto, revisa lo de abajo solo si sigue habiendo demora.

1b) BOTONES (refuerzo, 16.12/16.13): grabación de Gabriel: 6 toques
   seguidos a "◀ Anteriores" → el botón queda "cargando" y solo avanza 1 página
   ~15 s después. El callback no se responde a tiempo.
   - answerCallbackQuery ANTES de cualquier otra cosa, en su propio hilo, sin
     esperar la base ni la cola de envíos. Verifícalo con TIEMPO_BOTON.
   - Registra en el log toda respuesta 429 (Too Many Requests / retry_after). Si
     el bot duerme el retry_after en el mismo hilo o cola que atiende botones,
     sepáralo: las ediciones automáticas (lista en vivo, Últimas en vivo) nunca
     bloquean respuestas a botones.
   - Ediciones automáticas: máximo 1 cada 5 s por mensaje y 1 por segundo en todo
     el chat; con 429 se saltan (la siguiente trae lo último), no se encolan.
   - Toques repetidos al mismo botón: aplica solo el último (6× Anteriores =
     avanzar 6 páginas en una sola edición).
   - Registra el atraso de getUpdates (hora del update vs. hora en que se procesa).
   Mide 10 toques en la PC de Gabriel y reporta antes/después.

2) BÚSQUEDA EN VIVO (Vehículos y Consulta): mientras Gabriel escribe, sin Enter, la
   tabla se filtra letra por letra (desde la 1.ª letra; espera de 200 ms entre
   teclas; endpoint JSON que busca por prefijo y por "contiene", máx. 50
   resultados, sin recargar la página). Respeta los filtros Todos/Activos/Dados de
   baja. Enter o clic abre la ficha si queda una sola placa. En Consulta, la caja
   de placa muestra sugerencias debajo mientras se escribe.

0) BUG (primero, es de una línea): en nexcam_web.py,
   nexcam_asignar_usuario_telegram recibe `usuario_id: str = ""` sin Form(), así
   que FastAPI lo lee de la URL y no del formulario: el selector de NEXCAM →
   Telegram SIEMPRE guarda "Sin usuario" (Gabriel lo intentó varias veces). Cambia a
   `usuario_id: str = Form("")` y revisa TODAS las rutas POST del proyecto por el
   mismo error (parámetros de formulario sin Form). Prueba: enviar el formulario
   con un usuario → queda asignado.

3) CHAT SIN USUARIO: si un chat vinculado no tiene usuario de NEXORA asignado,
   - en el panel NEXCAM → Telegram sale un aviso amarillo "Este chat no tiene
     usuario: no verá datos de Admin ni el botón RGM" con el selector a la vista;
   - el bot le manda UNA vez a ese chat "⚠️ Este chat no tiene usuario de NEXORA
     asignado: pídele al Admin que lo asigne en el panel (NEXCAM → Telegram)".
   - Al vincular con /vincular desde un chat cuyo Telegram coincide con el de un
     usuario, se asigna solo (ya existe); si el único Admin no tiene Telegram
     registrado, la página de Usuarios lo sugiere.
```

## Prompt 7: RGM a pedido que no responde + NEXORA accesible desde internet

Resultado de las pruebas del 18/11/2026: todo OK salvo que el 🔎 RGM pedido desde Telegram a veces no responde (hay latido de Consulta, pero el resultado nunca llega al chat). Y ya toca abrir NEXORA para entrar desde cualquier red.

Diagnóstico ya hecho por Claude (chat) leyendo `rgm_cola.py` y `nexcam_telegram.py` en `nexcam` (3e3105e):
- Si la cámara ya había encolado esa placa (origen "camara"), el botón 🔎 RGM recibe `YA_EN_COLA` y solo guarda el chat en `_chat_por_item_rgm`. Al terminar, `_on_rgm_resultado` hace `return` porque el ítem tiene origen "camara" (RGM silencioso del Prompt 5) ANTES de mirar si alguien lo pidió. Resultado: nadie recibe nada.
- `_chat_por_item_rgm` vive en memoria: si NEXORA se reinicia entre el pedido y el resultado, se pierde quién lo pidió.
- Un ítem de cámara o de estudio tiene prioridad baja: el pedido de Telegram queda esperando detrás de otros.

```text
Rama nexcam, sin preguntar (CLAUDE.md), DESPUÉS de terminar el Prompt 6. Commit por
bloque, push al final, reporte con "Pruebas para tu visto bueno".

A) RGM PEDIDO SIEMPRE RESPONDE
1. Guarda en la base quién pidió cada consulta: tabla rgm_avisos (item_id, chat_id,
   usuario_id, creado_en) o columnas en rgm_cola (migración aditiva). Reemplaza el
   diccionario en memoria _chat_por_item_rgm. Un ítem puede tener VARIOS
   solicitantes (cámara + Telegram + web).
2. YA_EN_COLA: agrega al solicitante a ese ítem y SUBE su prioridad a la de
   Telegram (si estaba como cámara/estudio). Respuesta inmediata en el chat:
   "🔎 Consultando GCT 953… (puesto N en la cola)".
3. _on_rgm_resultado: el silencio de cámara/estudio aplica SOLO si el ítem no tiene
   solicitantes. Si tiene, se avisa a cada uno con el formato del Prompt 6 punto 1
   (editando la alerta si se pidió desde una alerta), sensibles solo a Admin.
4. rgm.error también se avisa a los solicitantes ("No se pudo consultar RGM de
   GCT 953: <motivo>. [🔁 Reintentar]").
5. Si a los 3 min de pedirlo no hay resultado: el bot edita "🔎 Consultando…" a
   "⏳ Sigue en cola (puesto N). ¿Está abierta Consulta en la PC?" — una sola vez.
6. Log de diagnóstico (archivo, no Telegram) de cada paso: pedido → reservado por
   Consulta → resultado recibido → aviso enviado/fallido, con tiempos. Página
   pequeña "Consulta → Diagnóstico" que muestre los últimos 50.
Pruebas: botón 🔎 RGM sobre una placa que la cámara ya encoló → llega el mensaje;
reiniciar NEXORA entre pedido y resultado → igual llega; error → aviso; dos
pedidos de la misma placa → una sola consulta y dos avisos.

B) NEXORA DESDE CUALQUIER RED (con Cloudflare Tunnel; Gabriel confirma y compra el dominio antes de este bloque)
NEXORA sigue corriendo en la PC de Gabriel (la base y el Connector RGM viven ahí).
Se publica con Cloudflare Tunnel: sin abrir puertos del router, HTTPS automático y
una dirección fija https://<dominio> (variable NEXORA_URL_PUBLICA en .env; el
dominio lo pone Gabriel).
1. Configuración en .env (ignorado por git): NEXORA_URL_PUBLICA, NEXORA_ENTORNO
   (local|publico). Nada de dominios ni tokens en el código.
2. Seguridad antes de exponer (obligatorio con NEXORA_ENTORNO=publico):
   - Cookie de sesión secure=True, httponly, samesite=lax; respeta X-Forwarded-Proto
     de Cloudflare (uvicorn --proxy-headers, forwarded_allow_ips=127.0.0.1).
   - /docs, /redoc y /openapi.json apagados.
   - Límite de intentos de login: 5 fallos por usuario o IP en 15 min → bloqueo de
     15 min y aviso "🔐 Intentos fallidos de entrar como <usuario> desde <IP>" al
     Telegram de los Admin. Igual para /api/auth/login de la app.
   - Cabeceras: HSTS, X-Content-Type-Options, X-Frame-Options DENY,
     Referrer-Policy. Tamaño máximo de subida (fotos 5 MB, PDFs 20 MB).
   - Fotos, oficios y PDFs nunca por URL pública directa: siempre detrás de sesión.
   - La página Consulta (la que atiende la cola con el Connector) solo funciona
     desde la propia PC (localhost/IP local): desde internet muestra "Consulta solo
     se atiende en la PC de la oficina". El resto de la web sí.
   - Revisa todas las rutas: ninguna sin requerir_admin salvo login, /api/* de la
     app (Bearer + dispositivo) y /app/nexcam.apk (enlace con ?k=).
   - Reporta la lista de rutas públicas que quedan.
3. Script `publicar-nexora.ps1` (en la raíz de NEXORA-CONTROL), que Gabriel corre
   una vez, con explicación en español en cada paso:
   - Instala cloudflared con winget si falta.
   - `cloudflared tunnel login` (abre el navegador: Gabriel elige su dominio).
   - Crea el túnel "nexora", el registro DNS del dominio y el config.yml en
     %USERPROFILE%\.cloudflared (fuera del repo) apuntando a http://localhost:8000.
   - Lo instala como servicio de Windows (arranca solo con la PC).
   - Al final prueba https://<dominio>/api/health (crea esa ruta simple) y lo
     escribe en .env como NEXORA_URL_PUBLICA.
   - iniciar-nexora.bat sigue igual (NEXORA en el puerto 8000); el túnel es aparte.
4. App NEXCAM y Telegram:
   - El enlace de vinculación, el de la app y los botones "Abrir en la web" de
     Telegram usan NEXORA_URL_PUBLICA cuando existe.
   - Reporta a Gabriel la línea exacta para app/.env:
     EXPO_PUBLIC_API_URL=https://<dominio> (Claude chat compila la app con eso).
5. Documento corto `docs/ACCESO-REMOTO.md`: cómo entrar desde el teléfono, qué hacer
   si la PC se apaga (nada funciona hasta que vuelva), cómo revocar el túnel.
Pruebas: con NEXORA_ENTORNO=publico, cookie secure, /docs 404, 6 logins malos →
bloqueo + aviso, Consulta desde IP no local → mensaje, /api/health responde.
```

## Prompt 8: RGM y Judicial en segundo plano, todo dentro de NEXORA

Resultado de la grabación de Gabriel (20/11/2026, placa de prueba LRY517) y de la revisión del código del Connector (rama `alcance-rgm`, d16b13f):
- **Consulta no se actualiza sola:** la cola y "Últimas 20" quedan en "Pendiente" hasta pulsar Actualizar, aunque el RGM ya terminó.
- **Judicial solo avanza si la pestaña del portal está al frente.** En segundo plano se queda en el modal ("Se han encontrado varios registros…"). Causa probable: el navegador frena las pestañas ocultas (requestAnimationFrame y temporizadores), y el portal (Vue) no termina de cerrar el modal ni de pintar la tabla.
- **Demandante perdido:** `extraerSujetosProcesales()` (judicial.js) no reconoce roles con barra ni en minúscula ("Demandante/accionante:", "Demandado/indiciado/causante:"). Por eso salen "Sujetos procesales no disponibles" o "JARAMILLO Defensor Privado: …", y falta el banco demandante, que es justo lo que se necesita.
- **SELECCIONAR no abre el proceso:** la pestaña Judicial se cierra o la consulta deja de ser "la activa en el motor" cuando la cola sigue, y el detalle nunca llega.
- **"Último resultado guardado" en Consulta sigue en JSON crudo.**

### Hallazgos de las capturas de red (HAR de Gabriel, 20/11/2026) — reemplazan la "investigación" de los puntos 1 y 4

**Rama Judicial: API JSON pública.** Sin login, sin captcha, sin cookies, con CORS `*`. Base: `https://consultaprocesos.ramajudicial.gov.co:448/api/v2/`
- `GET Procesos/Consulta/NombreRazonSocial?nombre=<NOMBRE>&tipoPersona=nat&SoloActivos=false&codificacionDespacho=&pagina=1`
  → `{tipoConsulta, procesos:[{idProceso, idConexion, llaveProceso (radicado), fechaProceso, fechaUltimaActuacion, despacho, departamento, sujetosProcesales, esPrivado, cantFilas}], parametros:{…}, paginacion:{cantidadRegistros, registrosPagina, cantidadPaginas, pagina}}`
  - `sujetosProcesales` es UN texto separado por `|`: `"Demandante/accionante: X| Demandado/indiciado/causante: Y| Defensor Privado: Z"`. Se parte por `|` y luego por el primer `:`.
  - En la captura se usó `SoloActivos=true` ("actuaciones recientes"). Para todos los procesos: `false`. Recorrer todas las páginas (`cantidadPaginas`).
  - Persona jurídica: `tipoPersona=jur` (confírmalo con una prueba manual de una sola consulta).
- `GET Proceso/Detalle/{idProceso}` → `{idRegProceso, llaveProceso, idConexion, esPrivado, fechaProceso, codDespachoCompleto, despacho, ponente, tipoProceso, claseProceso, subclaseProceso, recurso, ubicacion, contenidoRadicacion, fechaConsulta, ultimaActualizacion}`
- `GET Proceso/Actuaciones/{idProceso}?pagina=1` → `{actuaciones:[{idRegActuacion, llaveProceso, consActuacion, fechaActuacion, actuacion, anotacion, fechaInicial, fechaFinal, fechaRegistro, codRegla, conDocumentos, cant}], paginacion:{…}}` (40 por página).
- Probable también `GET Proceso/Sujetos/{idProceso}` (no salió en la captura; si responde, úsalo; si no, basta el texto del listado).
- `GET Lista/Departamentos` → `[{codigo, nombre}]`.

**Decisión: Judicial lo consulta NEXORA directamente desde el servidor (Python, httpx), sin Connector ni pestañas.** Funciona aunque Brave esté cerrado. Con cuidado: máximo 1 petición cada 1,5 s, timeout de 20 s, 3 reintentos con espera, User-Agent de navegador normal, caché de 24 h por nombre. Si algún día el portal bloquea al servidor (por ejemplo, al pasar a la nube con IP de fuera), queda el plan B: el Connector hace las mismas llamadas con fetch desde su service worker.

**RGM (garantiasmobiliarias.com.co): ASP.NET WebForms con UpdatePanel.** No hay API JSON.
- Flujo: `GET ConsultaGarantia.aspx` → `POST` asíncrono (`X-MicrosoftAjax: Delta=true`, `__ASYNCPOST=true`) con `__VIEWSTATE`, `__EVENTVALIDATION`, `__VIEWSTATEGENERATOR`, `ctl00$ctl00$hdnCSFK` (token de 64 caracteres), el campo de placa, el radio "Consultas no oficiales" y el botón Consultar → respuesta "delta" con la grilla.
- Segundo `POST` seleccionando la fila de la grilla → `pageRedirect` a `ResultadoConsultaGarantias.aspx?CodigoGarantia=…&ConsultaOficial=…&FechaInscripcionInicial=…&NombreTipoFormulario=…` → `GET` del HTML del detalle (lo que ya lee content.js).
- El formulario trae un campo `recaptchaResponse` (vacío en la captura: hoy no se exige, pero puede activarse) y la página usa `?_TSM` (protección anti-bots tipo F5 con cookies del navegador).
- **Decisión: el RGM lo hace el Connector con fetch desde su service worker** (usa las cookies del navegador de Gabriel; sin pestaña): GET → extraer los campos ocultos → POST consulta → POST selección → GET detalle → mismo parser de content.js sobre el HTML (con DOMParser en un offscreen document si el service worker no tiene DOM). Si una respuesta pide captcha o trae una página de desafío → plan B automático: pestaña inactiva en una ventana aparte que nunca se activa, y aviso en Diagnóstico.
- Nunca guardar en el repo ni en los logs las cookies, el ViewState ni los tokens.

```text
Dos repos, sin preguntar (CLAUDE.md), DESPUÉS del Prompt 7 bloque A:
- Connector: C:\Users\Rus\Documents\NEXORA\nexora-connector-rgm-v0.1, rama
  alcance-rgm (NO toques ni borres el git stash que hay ahí).
- NEXORA-CONTROL, rama nexcam.
Commit por punto, push de las dos ramas al final, reporte con "Pruebas para tu
visto bueno". Las pruebas automáticas NUNCA consultan los portales reales: usa
respuestas grabadas con datos ficticios.

Meta de Gabriel: pedir RGM y Judicial desde NEXORA y ver todo en NEXORA, sin que se
abra, active ni cambie de pestaña nada. Los portales trabajan en segundo plano.

1) JUDICIAL SIN PESTAÑA: YA INVESTIGADO (ver "Hallazgos de las capturas de red"
   arriba de este bloque). Implementa la consulta desde el servidor de NEXORA con
   la API JSON; lo de abajo queda solo como referencia:
   - Con la pestaña del portal abierta a mano, registra en un archivo de
     diagnóstico (no en el repo) qué peticiones de red hace la página
     consultaprocesos.ramajudicial.gov.co al buscar por nombre, al abrir un proceso,
     sus sujetos y sus actuaciones. Probablemente es una API JSON
     (…:448/api/v2/Procesos/Consulta/NombreRazonSocial, /Proceso/Detalle/{id},
     /Proceso/Sujetos/{id}, /Proceso/Actuaciones/{id}); confírmalo.
   - Si es una API pública sin captcha: el service worker del Connector la llama
     directo con fetch (host_permissions), sin pestaña. Respeta: máx. 1 petición
     cada 1,5 s, paginación completa, reintentos con espera, User-Agent normal.
     Sin abrir pestañas ya no hay modal que cerrar.
   - Si no se puede (captcha, bloqueo): plan B = pestaña en una ventana aparte NO
     minimizada que nunca toma el foco, y desactivar el freno de pestañas ocultas
     donde se pueda; repórtalo con lo que viste.
   - Devuelve por proceso: radicado, id interno, despacho, departamento, fecha de
     radicación, fecha de última actuación, privado sí/no, y sujetos [{rol, nombre}]
     con el rol COMPLETO ("Demandante/accionante").

2) SUJETOS PROCESALES BIEN LEÍDOS (aunque siga habiendo DOM en algún caso):
   - Corrige extraerSujetosProcesales: roles con "/" y palabras en minúscula después
     de la barra, "Defensor Privado", "Defensor Público", "Apoderado", "Tercero",
     etc. Mejor aún: tomar el rol del elemento en negrilla de la celda y el nombre
     del texto que lo sigue. Pruebas con el texto real de formato (nombres ficticios).

3) SELECCIONAR UN PROCESO EN CUALQUIER MOMENTO:
   - Desde NEXORA (Consulta y ficha del vehículo), "Ver proceso" trae detalle,
     sujetos y actuaciones de ese radicado aunque la consulta ya haya terminado y la
     cola siga con otras placas (por la API del punto 1 o reabriendo en segundo
     plano). Se guarda como snapshot y se muestra en NEXORA; nunca abre el portal
     al frente.

4) RGM EN SEGUNDO PLANO: YA INVESTIGADO (ver "Hallazgos"). Implementa el fetch
   desde el service worker del Connector con plan B automático. Referencia:
   - Investiga igual las peticiones de garantiasmobiliarias.com.co (ASP.NET). Si se
     puede consultar por fetch (ViewState) sin captcha ni sesión especial, hazlo sin
     pestaña. Si no, la pestaña RGM se abre inactiva en una ventana aparte y nunca
     se activa (quita el "activarMientrasExtrae"), y verifica que la extracción
     termina con la pestaña oculta (si el freno la detiene, reporta y deja la
     opción más estable).

5) NEXORA EN TIEMPO REAL (página Consulta y ficha):
   - La cola, "Últimas 20", el Estado y el resultado se actualizan solos con los
     eventos que ya existen (SSE rgm.solicitud / rgm.resultado / rgm.error y los
     estados del Connector), sin pulsar Actualizar: Pendiente → En curso → OK/Error
     en el momento.
   - "Último resultado guardado" como tabla (mismo componente de la ficha del
     Prompt 5/6), JSON solo en "Ver datos originales".

6) PROCESOS JUDICIALES LEGIBLES Y ÚTILES (Consulta y ficha, solo Admin):
   - Cada proceso en una tarjeta: radicado · despacho (ciudad) · radicado el
     <fecha> · última actuación <fecha> · Demandante(s) · Demandado(s) · otros.
   - Marca arriba, en verde, "🏦 Demanda la financiera" los procesos donde el
     demandante coincide con el acreedor del RGM de esa placa (nombre normalizado:
     sin "S.A.", "SA", "BIC", "COMPAÑÍA DE FINANCIAMIENTO", tildes ni puntos; ej.
     BANCO FINANDINA SA BIC = BANCO FINANDINA S.A. BIC). Ordena: esos primero,
     luego por fecha de última actuación (más reciente arriba).
   - Procesos privados: una línea "🔒 Proceso privado · <despacho>", sin botón.
     Radicados repetidos se muestran una vez.
   - Botón "Ver proceso" (punto 3) en lugar de "SELECCIONAR". Las actuaciones se
     muestran debajo, la más reciente arriba.
   - Telegram (Admin), botón ⚖️ Procesos: mismo resumen, con los de la financiera
     primero.

Pruebas: respuestas grabadas de la API judicial (ficticias) → lista correcta con
demandantes; parser con "Demandante/accionante:" y "Defensor Privado:"; coincidencia
acreedor ↔ demandante; Ver proceso después de terminada la consulta; Consulta se
actualiza sola (evento SSE simulado); ninguna llamada a chrome.tabs.update con
active:true en todo el Connector.
```

## Prompt 8b: correcciones de la prueba del Prompt 8 (21/11/2026, 1:20 a. m.)

Resultado: 16.12/16.13 OK (botones en 0,5 s después del arreglo de IPv4), 16.9/16.10 OK, 16.21 OK. Fase 18: **falla, el RGM sigue abriendo la pestaña del portal**. Del log de Gabriel salieron además tres errores:
- `POST /rgm/trabajador/resultado` → 500, `sqlite3.IntegrityError: FOREIGN KEY constraint failed` en `database.crear_snapshot` (rgm_cola.completar_item, línea 341). Placa NHP973, pedida por Telegram (/buscar) y que NO está en vehiculos: el snapshot no se puede guardar y la consulta termina en "Error".
- `AVISO: el hilo de Judicial directo tuvo un error inesperado: database is locked`.
- NEXCAM → Ya vistas muestra la foto a tamaño gigante (sin miniatura).

```text
Rama nexcam (y el Connector, rama alcance-rgm), sin preguntar (CLAUDE.md). Commit por
punto, push al final, reporte con "Pruebas para tu visto bueno".

1) RGM SIGUE ABRIENDO PESTAÑA (fase 18 falla):
   - Primero comprueba qué Connector está cargado: sube la versión del manifest,
     haz que el Connector la mande en el latido/PING y muestra en Consulta
     "Connector vX.Y.Z conectado" (y un aviso rojo si es más vieja que la del
     repo). Así Gabriel sabe si recargó la extensión.
   - Registra en Consulta → Diagnóstico POR QUÉ el RGM usó pestaña: "plan A (sin
     pestaña) OK" o "plan B porque: <motivo exacto>" (captcha, desafío anti-bots,
     error de red, parser sin datos, DOMParser no disponible en el service
     worker, etc.), con el código HTTP y los primeros 200 caracteres de la
     respuesta (sin cookies ni tokens).
   - Corrige la causa para que el plan A funcione de verdad en Brave. Si el portal
     exige un desafío del navegador, el plan B NO puede abrir una pestaña visible
     ni activarla: ventana aparte creada con focused:false y state:"minimized"
     (o fuera de pantalla), pestaña con active:true SOLO dentro de esa ventana,
     y se cierra al terminar. Verifica que la ventana de NEXORA nunca pierda el foco.
   - Prueba manual que debes describirle a Gabriel paso a paso (no la hagas tú
     contra el portal real).

2) SNAPSHOT DE PLACA QUE NO ESTÁ EN LA LISTA (500 FOREIGN KEY): una consulta RGM o
   Judicial de una placa que no existe en vehiculos (pedida por /buscar, Consulta o
   estudio) debe guardarse igual. Opción recomendada: crear la fila mínima en
   vehiculos con estado "consultada" (no activa, no entra a la lista de la cámara,
   no sale en la tabla de Vehículos salvo con el filtro "Consultadas"), o quitar la
   FK de snapshots_placa con migración aditiva; elige la más segura, con copia de
   la base antes, y anótalo. Ningún 500 en /rgm/trabajador/resultado: si algo
   falla, el ítem queda en Error con el motivo legible y se avisa a quien pidió.

3) "database is locked" (hilo de Judicial directo): todas las conexiones SQLite con
   timeout/busy_timeout de 10 s y modo WAL (PRAGMA journal_mode=WAL, una vez al
   arrancar, con copia antes); escrituras cortas; reintento con espera si aún así
   sale "locked". Prueba con dos hilos escribiendo a la vez.

4) NEXCAM → Ya vistas: la foto sale gigante. Miniaturas como En vivo (72×48 y 48×32)
   con el mismo visor ampliado al tocar.

5) Consulta, cola y "Últimas 20": confirma que el estado cambia solo (Pendiente →
   En curso → OK/Error) sin pulsar Actualizar; en la captura de Gabriel LRY517 se
   quedó "En curso" mientras el portal ya había respondido.
```

## Prompt 8c: RGM sin pestaña de verdad (plan A) con la especificación de la captura

El Prompt 8b dejó el RGM en una ventana aparte sin foco (plan B) y dijo que el plan A no se podía construir sin una captura. La captura ya está analizada y convertida en una especificación sin datos reales: `RGM-PLAN-A.md` (en la misma carpeta que este documento; en el Proyecto: `claude/rgm-plan-a-especificacion.md`).

```text
Connector (rama alcance-rgm) y NEXORA-CONTROL (rama nexcam), sin preguntar (CLAUDE.md).
Lee "C:\Users\Rus\Documents\NEXORA\PROMPTS Y PRUEBAS DOCS\RGM-PLAN-A.md": es la
estructura exacta de la consulta real del RGM (campos, cabeceras, formato delta,
pageRedirect), sacada de la captura de Gabriel y sin datos reales. Con eso:
1) Implementa el plan A en el service worker del Connector: GET formulario → POST
   búsqueda → parsear delta (por longitudes) → elegir la fila más reciente → POST
   detalle → pageRedirect → GET detalle → mismo extractor de content.js sobre ese HTML
   (DOMParser en un offscreen document). credentials:"include". Una consulta a la vez,
   mínimo 3 s entre consultas.
0) URGENTE (prueba del 21/11/2026, 3:11 a. m.): el plan B del 8b FALLA SIEMPRE.
   Diagnóstico de LRY517 y KPQ749: "error — Invalid value for bounds. Bounds must
   be at least 50% within visible screen space." chrome.windows.create no acepta
   ventanas fuera de la pantalla, así que hoy ningún RGM termina. Arréglalo
   primero: ventana con state:"minimized" y focused:false; si con la ventana
   minimizada la extracción no avanza en 20 s, reintenta con una ventana normal
   pequeña (p. ej. 480×360) en una esquina DENTRO de la pantalla (usa
   chrome.system.display o screen.availWidth/Height), focused:false, y vuelve a
   enfocar la ventana anterior si Windows le pasa el foco. Nunca más bounds fuera
   de la pantalla. Prueba automática que valide que los bounds pedidos están
   ≥ 50 % dentro de la pantalla.

2) Plan B automático (la ventana aparte sin foco que ya hiciste) si: falta
   __VIEWSTATE, aparece captcha/desafío, el delta no trae la grilla o el detalle no
   se puede leer. Diagnóstico dice "plan A OK" o "plan B porque: <motivo>".
3) Pruebas automáticas con respuestas simuladas escritas a mano según la
   especificación (grilla de 0, 1 y 2 filas; delta con pageRedirect; detalle
   ficticio). Nunca contra el portal real.
4) Sube la versión del Connector (v0.3.0) y dile a Gabriel cómo verificarlo:
   Consulta debe decir "Connector v0.3.0 conectado" y Diagnóstico "plan A OK".
Además, Prompt 8 punto 2 (sujetos procesales en judicial.js) ya no aplica si Judicial
va por la API JSON desde el servidor: confírmalo y, si judicial.js ya no se usa,
déjalo como plan B documentado.
Push de las dos ramas. Termina con "Pruebas para tu visto bueno".
```

## Prompt 8d: procesos judiciales completos y legibles en Telegram + plan A del RGM

Resultado de la prueba del 21/11/2026, 6:05 p. m.: todo OK (RGM termina, Judicial directo funciona, Ver proceso en la web, Telegram ⚖️ Procesos con la financiera primero). Pero:
- El RGM siempre cae a plan B: "plan B porque: paso 1: la respuesta parece un captcha/desafío, no el formulario". Casi seguro es una falsa alarma: el formulario real del RGM trae el campo `recaptchaResponse` (vacío) y scripts con la palabra "captcha", así que buscar esa palabra en el HTML siempre da positivo.
- Gabriel quiere ver el proceso completo en Telegram y una vista más legible (hoy: fechas "2026-07-22T00:00:00", nombres en mayúsculas mezcladas, todo en un bloque largo).

```text
Rama nexcam (y alcance-rgm para el punto 1), sin preguntar (CLAUDE.md). Commit por
punto, push al final, reporte con "Pruebas para tu visto bueno".

1) PLAN A DEL RGM, FALSA ALARMA DE CAPTCHA: en el paso 1 no decidas "captcha" por
   la palabra en el HTML. El formulario es válido si trae __VIEWSTATE,
   __EVENTVALIDATION, ctl00$ctl00$hdnCSFK y el campo txtNumBien. Solo es desafío si
   faltan esos campos Y aparece una página de desafío (p. ej. g-recaptcha visible
   con data-sitekey en un formulario sin __VIEWSTATE, o un HTML muy corto con
   script de verificación). Si el paso 1 está bien, sigue con el paso 2; si más
   adelante el portal pide captcha de verdad, el delta lo dirá (sin grilla y con
   mensaje de error): entonces plan B con ese motivo. Pruebas con un HTML de
   formulario que contiene recaptchaResponse vacío → plan A sigue.

2) TELEGRAM, LISTA DE PROCESOS MÁS LEGIBLE (botón ⚖️ Procesos, solo Admin):
   Cabecera: "⚖️ Procesos de GCT 953 · 7 encontrados · consulta del 21/11/2026"
   Dos grupos:
     🏦 De la financiera (3)   (demandante = acreedor del RGM, nombre normalizado)
     📁 Otros (4)
   Cada proceso, una tarjeta corta y separada por una línea en blanco:
     <b>1.</b> <code>11001400302220260067500</code>
     🏛 Juzgado 002 Civil Municipal · Bogotá
     📅 Radicado 07/05/2026 · última actuación 22/07/2026
     👤 Demandante: GM Financial Colombia
     👤 Demandado: Pedro Nel Hernández Peñaloza
   - Fechas en dd/mm/aaaa (nunca "T00:00:00"). Nombres y juzgados con mayúscula
     inicial; siglas como S.A., S.A.S., BIC tal cual; sin "Compañía de
     Financiamiento" ni dobles espacios en la vista (el dato completo se guarda).
   - Roles cortos: "Demandante/accionante" → Demandante, "Demandado/indiciado/
     causante" → Demandado; otros roles (Defensor, Apoderado, Tercero) en una
     línea "Otros: …" solo si caben.
   - Privados: "🔒 Proceso privado · Juzgado … · Medellín", sin botón.
   - Si no cabe en 4.096 caracteres, se parte en varios mensajes sin cortar una
     tarjeta a la mitad.
   - Botones debajo: [📄 1] [📄 2] … (uno por proceso no privado, 4 por fila) y
     [🔄 Actualizar] [⬅️ Volver].

3) VER EL PROCESO COMPLETO EN TELEGRAM: al tocar [📄 N]:
   - Panel con el detalle (API Proceso/Detalle): radicado, despacho, ponente, tipo
     y clase de proceso, ubicación, fecha de radicación, sujetos completos.
   - Actuaciones (API Proceso/Actuaciones), la más reciente arriba, 10 por página:
       📌 22/07/2026 · Auto admite demanda
          <anotación recortada a ~200 caracteres>
     con [◀ Más antiguas] [Más recientes ▶] que editan el mismo mensaje, y
     [⬅️ Volver a la lista].
   - Botón [📑 Proceso completo (PDF)]: NEXORA arma un PDF con el detalle y TODAS
     las actuaciones (anotaciones completas), con el encabezado "Consulta de
     procesos – Rama Judicial · generado por NEXORA el <fecha>" y lo manda con
     sendDocument. Nombre: <radicado>-<placa>.pdf. Se guarda también en la ficha
     web del vehículo (sección Procesos). Solo Admin.
   - Si la actuación indica que tiene documentos (conDocumentos), mostrar "📎 con
     documentos" (verlos queda en el portal: enlace al portal de consulta).
   - Todo lo que se abre aquí es un panel con [⬅️ Volver] (mismo criterio del
     Prompt 9: se borra al volver).
   - Caché: detalle y actuaciones se guardan 6 h; [🔄 Actualizar] los pide de nuevo.

4) WEB, PÁGINA CONSULTA EN DOS COLUMNAS (Gabriel: "que los procesos judiciales se
   vean al lado, tenemos buen espacio"): hoy todo va en una columna angosta en el
   centro y sobra espacio a los lados. Nuevo diseño (estilo "Asfalto y placa", el
   mismo de la web):

   ┌─ izquierda (≈420 px) ───────────┐ ┌─ derecha (el resto) ─────────────────────┐
   │ Buscar placa (con sugerencias)  │ │ Placa grande · estado · vehículo          │
   │ Consulta RGM / Judicial         │ │ Datos RGM (acreedor, deudor) en tarjeta   │
   │  · Connector vX conectado       │ │ ─────────────────────────────────────────  │
   │  · [RGM] [Judicial] [Ambos]     │ │ ⚖️ Procesos judiciales (7)                │
   │  · Consultar · Estado           │ │  [🏦 De la financiera 3] [📁 Otros 4]     │
   │ Cola (pendientes y en curso)    │ │  tarjetas: radicado · juzgado · ciudad ·  │
   │ Últimas 20 (con 🩺 Diagnóstico) │ │  fechas · demandante · demandado ·        │
   │                                 │ │  [Ver proceso] [PDF]                      │
   └─────────────────────────────────┘ │ Al tocar "Ver proceso": panel lateral     │
                                       │ (drawer) con detalle y actuaciones        │
                                       │ (línea de tiempo, la más reciente arriba, │
                                       │ "Cargar más"), sin salir de la página.    │
                                       └───────────────────────────────────────────┘
   - La columna derecha muestra la placa elegida: la buscada, o la que se toque en
     la cola / Últimas 20 (clic en la fila → se carga a la derecha sin recargar).
   - Mientras corre la consulta, la derecha muestra el avance en vivo (RGM… →
     Judicial… → listo) con los eventos SSE que ya existen.
   - Tarjetas de proceso: borde verde y etiqueta "🏦 Demanda la financiera" para las
     del acreedor; privados en gris con 🔒; filtros por grupo y buscador por
     juzgado/ciudad; orden por última actuación.
   - Mismas fechas dd/mm/aaaa y nombres con mayúscula inicial que en Telegram;
     botón "Descargar PDF" del proceso completo.
   - El JSON crudo, solo plegado en "Ver datos originales".
   - Por debajo de 1100 px vuelve a una sola columna (derecha debajo).
   - La ficha de Vehículos usa el mismo bloque de procesos y el mismo drawer.
   - Revisa con capturas de Playwright a 1366 y 1440 px.

5) ÍTEMS COLGADOS: NHP973 quedó "En curso" más de 7 min (pedido 6:10 p. m.) y el
   aviso "Ya hay una consulta en curso" impide pedirla de nuevo. Un ítem en curso
   sin latido ni resultado por más de 3 min vuelve a Pendiente (máx. 2
   reintentos) y luego pasa a Error con motivo "sin respuesta del Connector";
   botón "Reintentar" en la fila de la cola. Anótalo en Diagnóstico.

Pruebas con respuestas simuladas de la API judicial (datos ficticios): formato de
la lista (fechas, nombres, grupos), partición en varios mensajes, paginado de
actuaciones, PDF generado con todas las actuaciones, plan A no se detiene por
recaptchaResponse vacío.
```

## Prompt 9: Telegram limpio, todo por menú y paneles que se cierran con "Volver"

Pedido de Gabriel (20/11/2026): que no aparezca el menú escrito (la lista "📊 Estado: teléfonos…, 🕒 Últimas: …"); que todo sea por botones; y que lo que él abre (Últimas, Estado, Buscar, Ficha…) se cierre con "Volver" y desaparezca del chat, para que siempre queden a la vista las cosas importantes: las alertas con foto y la cámara en vivo.

```text
Rama nexcam, sin preguntar (CLAUDE.md). Puede hacerse junto con el Prompt 6 o
después. Commit por punto, push al final, reporte con "Pruebas para tu visto bueno".

1) MENÚ SIEMPRE EN BOTONES, NUNCA ESCRITO
   - /start y /menu ya no mandan el texto explicativo. Muestran el teclado de
     botones fijo abajo (ReplyKeyboardMarkup con is_persistent=true,
     resize_keyboard=true), con un texto mínimo de una línea ("NEXCAM ·
     elige una opción"), que el bot BORRA a los pocos segundos (el teclado
     queda).
   - Botones del teclado (2 o 3 por fila): 📊 Estado · 🕒 Últimas · 🔍 Buscar ·
     📷 En vivo · 📥 Cargar lista · ➕ Agregar · ➖ Quitar · 🔔 Avisos · 🔕 Silencio
     · 📲 Instalar app. Operadores ven solo lo que pueden usar.
   - setMyCommands solo con /start y /menu. El botón "Menú" de Telegram abre los
     comandos; la ayuda larga pasa a un botón ❓ Ayuda dentro de Estado (panel).
   - Si alguien escribe texto que no es placa ni lista, el bot no contesta con
     explicación: borra el mensaje y vuelve a mostrar el teclado.

2) PANELES QUE SE CIERRAN
   - Todo lo que el usuario abre es un "panel": Estado, Últimas, Buscar/resultado,
     Ficha, Datos RGM, Procesos, Cargar lista (resumen), Avisos, Silencio,
     Instalar app, Ayuda. Cada panel lleva al final [⬅️ Volver].
   - Volver = deleteMessage del panel (y de sus mensajes hijos, p. ej. la foto de
     la placa o el PDF que se abrió desde él). No se manda nada nuevo.
   - Un solo panel abierto por chat: al abrir otro, el anterior se borra solo.
   - Los mensajes que escribe el usuario (botón del teclado, /comando, placa
     buscada) también se borran apenas el bot los procesa (en chats privados el
     bot puede borrarlos, < 48 h).
   - Un panel olvidado se borra solo a los 15 min (TG_PANEL_MIN), salvo "Últimas
     en vivo" mientras la cámara esté encendida.
   - Guarda en la base (no en memoria) los message_id de paneles por chat, para
     poder borrarlos después de reiniciar NEXORA.
   - Si deleteMessage falla (mensaje de más de 48 h), edita el panel a una línea
     "·" sin botones y sigue.

3) LO IMPORTANTE NO SE BORRA
   - Nunca se borran solos: alertas 🚨/⚠️ con foto, "🗂️ Ya la viste", "📷 Cámara
     encendida" (lista en vivo) y su cierre "⏹️ Cámara apagada", avisos 🔐 de
     seguridad, mensajes del chat operador ↔ Admin, oficios PDF de alertas.
   - Abrir 📋 Ficha o 🔢 Placa DESDE una alerta crea un panel aparte con
     [⬅️ Volver] (la alerta queda intacta). Lo mismo 🔎 RGM: el resultado edita la
     alerta (Prompt 6) y no deja mensajes sueltos.
   - Botón 🧹 Limpiar en el panel Estado: borra todos los paneles y mensajes
     temporales que queden, y deja solo lo importante.

4) TECLADO SIEMPRE PRESENTE
   - Si el teclado desaparece (Telegram lo oculta al borrar mensajes), el
     siguiente panel o la siguiente alerta lo vuelve a poner.

Pruebas con Telegram simulado: /start no manda texto largo y deja el teclado;
abrir Últimas y Volver → deleteMessage del panel y del mensaje del usuario; abrir
Estado con Últimas abierto → se borra Últimas; panel a los 15 min → borrado;
alertas nunca borradas; reinicio de NEXORA → los paneles viejos se pueden borrar.
```

---

## Plan de pruebas

Antes de cada tanda: reiniciar NEXORA (cerrar la ventana y abrir `iniciar-nexora.bat`) y recargar el Connector en `chrome://extensions`. Reportar "7.1 OK" o la captura del que falle.

### Paso 1 (después de los Prompts 0 y 2)

**Fase 7: cámara y Telegram**
- **7.1** El bot responde: `/menu` muestra los botones; 📊 Estado ve el teléfono en línea.
- **7.2** Cámara encendida: llega el mensaje en vivo con "HONOR ALT-NX3" y el nombre del operador.
- **7.3** Una alerta por placa: apuntar a GCV 471 un minuto → 1 alerta, el contador sube.
- **7.4** Formato: la alerta trae fecha de ingreso a la lista, "primera vez" o "vista N veces", y el enlace 📍.
- **7.5** Varias placas: apuntar una por una (centrada) a 3 placas de la lista → 3 alertas.
- **7.6** Posible coincidencia: placa con O/Q/0 → ⚠️ "Posible".
- **7.7** Cámara apagada: cerrar la app → el mensaje cambia a "⏹️ Cámara apagada" (no "sin señal").
- **7.8** Lista del día: cargar 3 placas por 📥 Cargar lista, una ya vista → "🗂️ Ya la viste".

**Fase 10: seguridad**
- **10.1** Revocar el teléfono desde el panel → la app muestra el aviso, se cierra y borra sus datos.
- **10.2** Iniciar sesión con el mismo usuario en otro teléfono → el primero se cierra solo.
- **10.3** Login con contraseña mala → la app muestra el mensaje en palabras, no "HTTP 401/403".

### Paso 2 (no requiere prompt nuevo)

**Fase 8: RGM**
- **8.1** RGM desde el panel: Consulta → placa → RGM → aparecen los datos.
- **8.2** RGM desde Telegram: botón 🔎 RGM en una alerta → "📄 Datos RGM" respondiendo a la alerta.
- **8.3** RGM automático: con Consulta abierta en Chrome, la cámara ve una placa de la lista → el RGM llega solo.
- **8.4** Sin Consulta abierta: el bot dice "RGM en espera: abre Consulta en la PC".
- **8.5** Operador y Admin: un chat de Operador NO ve deudor ni documento; el Admin sí.

**Fase 9: Judicial (en prueba)**
- **9.1** Judicial desde Consulta, con una placa que ya tenga RGM.
- **9.2** RGM + Judicial juntos desde Consulta.
- **9.3** Botón ⚖️ Procesos judiciales en Telegram (solo Admin).
- **9.4** Consulta con la pestaña en segundo plano (bug pendiente del Connector).

### Paso 3 (después del Prompt 3)

**Fase 11: carga de placas (bloque A)**
- **11.1** Gestión masiva sigue con sus 4 pestañas (Carga, Baja, Reactivación, Consulta / Estudio).
- **11.2** Carga: pegar un texto de WhatsApp con placas por línea, separadas por comas y pegadas (`XYZ789WQU456`) → cuenta bien las válidas y muestra los omitidos.
- **11.3** Alistar deja una placa por línea; Cargar pide confirmación con el grado elegido.
- **11.4** Una placa dada de baja → pregunta una por una (Omitir / Confirmar) y queda en Auditoría.
- **11.5** Baja masiva y Reactivación masiva aceptan el mismo texto pegado.
- **11.6** Telegram: pegar el mismo texto en el bot → resumen con [✅ Cargar] [🗑 Dar de baja] [👁 Ver lista] [✖ Cancelar].
- **11.7** Lo cargado por Telegram aparece en Vehículos con fecha y hora de ingreso y en Auditoría a nombre del Admin.
- **11.8** Excel/CSV con columnas Placa, Tipo aprehensión, Banco, Modelo → los datos quedan en la ficha.

**Fase 12: vehículos y oficios (bloques B y C)**
- **12.1** La tabla de Vehículos ya no se corta; la columna Oficio muestra ✓ o ✗.
- **12.2** Editar en la ficha tipo de aprehensión, banco, modelo y fecha del oficio.
- **12.3** Oficios: subir 5 PDF de NEXDOC a la vez → la tabla de revisión saca placa, banco, juzgado y fecha.
- **12.4** Un oficio de una placa que no está en la lista → botón "Agregar a la lista".
- **12.5** La cámara ve una placa con oficio → al Admin le llega la alerta y el PDF debajo.
- **12.6** La cámara ve una placa sin oficio → la alerta dice "📄 Sin oficio".
- **12.7** Enviar oficios por Telegram (PDF al bot) → se guardan igual que desde la web.

**Fase 13: operadores por Telegram (bloque D)**
- **13.1** Un operador vincula su Telegram con /vincular y queda asociado a su usuario.
- **13.2** La cámara del operador ve una placa en lista → él recibe la alerta completa; el Admin, solo una línea en silencio "🔕 … vio GCV 471".
- **13.3** El operador escribe al bot → al Admin le llega "💬 <operador>: …"; el Admin responde y al operador le llega "💬 Admin: …".
- **13.4** El Admin pulsa [📤 Enviar oficio a <operador>] → el operador recibe el PDF.
- **13.5** La conversación aparece en la web (NEXCAM → Operadores) y desde ahí se puede escribir.
- **13.6** El operador nunca recibe deudor, documento, radicado ni procesos.

**Fase 14: estudio masivo y vigencia (bloques E y F)**
- **14.1** Estudio masivo: período + [RGM] → Iniciar → con Consulta abierta, las placas se van consultando y las cifras (Actualizadas / Pendientes / Sin datos) cambian.
- **14.2** Estudio con [RGM + Judicial] (solo Admin).
- **14.3** Priorizar una placa del estudio → se consulta antes que las demás.
- **14.4** Una alerta de cámara durante un estudio se consulta primero (más prioridad que el estudio).
- **14.5** Período "Más de 3 meses" muestra las placas antiguas.
- **14.6** Hoy muestra "N placas llevan más de 3 meses: revisa si siguen vigentes" y el contador en el menú.
- **14.7** [Sigue vigente] saca la placa del aviso; [Dar de baja] pide confirmación y queda en Auditoría; nada se borra solo.

**Fase 15: oficios desde NEXDOC (bloque G + Prompt 4)**
- **15.1** En NEXORA, Usuarios → tu usuario → Conectar NEXDOC → copiar el token.
- **15.2** En NEXDOC, configuración → Conectar con NEXORA → Probar conexión → "NEXORA conectado".
- **15.3** Procesar un oficio nuevo en NEXDOC → en segundos aparece en la ficha de esa placa y llega el resumen a Telegram.
- **15.4** Un AUTO también cuenta como documento (✓ "Auto del …").
- **15.5** Un oficio de una placa que no estaba en la lista (desde Admin) → se agrega sola.
- **15.6** NEXORA apagada → NEXDOC muestra "N por enviar"; al encender NEXORA se envían solos.
- **15.7** "Enviar lo ya archivado" → sube los oficios viejos sin duplicar.
- **15.8** Un operador procesa un oficio en NEXDOC → se envía solo, sin preguntarle; en NEXORA aparece "De operador · por validar" y tú lo validas.
- **15.10** Un operador no ve ni puede apagar la conexión con NEXORA en NEXDOC.
- **15.11** Subir oficios a mano (web y Telegram) solo funciona para Admin.
- **15.9** El Excel de NEXDOC ahora tiene Marca, Línea, Modelo, Color y Correo juzgado.

**Fase 16: RGM completo y Telegram legible (Prompt 5 + app 0.5.3)**
- **16.1** Con la cámara viendo placas de la lista, el RGM automático llena la ficha y NO manda mensajes a Telegram.
- **16.2** Botón 🔎 RGM en Telegram (o Consultar en la web) → llega un mensaje con marca, línea, modelo, servicio, serie y motor; acreedor y deudor solo para Admin.
- **16.3** La ficha de GCT 953 muestra "Chevrolet / Tracker" y modelo 2021; Datos RGM en tabla, no en JSON.
- **16.4** NEXCAM → En vivo con filas pequeñas; tocar la foto la amplía.
- **16.5** La lista "📷 Cámara encendida" muestra solo las últimas 10, con 🔴/🟡/⚪ y la placa en letra gruesa.
- **16.6** "🕒 Últimas" con páginas: ◀ ▶ cambian de página en el mismo mensaje.
- **16.7** App: botón 🌙 deja la pantalla en negro y la cámara sigue leyendo; mantener el dedo 5 s la enciende; una alerta la enciende sola.
- **16.8** App: botón ⏻ apaga la cámara (Telegram dice "Cámara apagada") y "Encender cámara" la vuelve a prender.

**Después del Prompt 6 y la app 0.5.4**
- **16.9** Botón 🔎 RGM en una alerta → la MISMA alerta se reordena: placa, En lista: UBICAR, Ingreso, vehículo, serie/motor, acreedor y deudor, RGM del…, 🗺️ Ubicaciones. Sin "Consulta hecha" ni "En tu lista".
- **16.10** Una placa sin motivo dice "En lista: UBICAR".
- **16.11** Ficha web de GCT 953 SIN volver a consultar: debajo de la placa dice Chevrolet / Tracker, 2021, servicio, serie y motor; la caja Datos RGM solo trae acreedor y deudor.
- **16.12** 🕒 Últimas con la cámara encendida: el mensaje muestra 5 placas y se va actualizando solo (entra la nueva arriba, sale la vieja).
- **16.13** ◀ Anteriores y 🔴 En vivo cambian el mensaje en menos de 1 segundo.
- **16.14** App 0.5.4: en el menú ⋮ (o Acerca de) dice versión 0.5.4, y arriba se ven los botones 🌙 y ⏻.
- **16.15** Apuntar la cámara a una pantalla con texto (WhatsApp con direcciones y cédulas, o la lista de archivos de Claude) → no aparecen placas inventadas (KRB043, CCI017, AMV057) en Historial ni en Telegram.
- **16.16** Apuntar a una placa real (o una impresa "GCT 953") → la sigue leyendo bien.
- **16.17** Vehículos: ninguna fila tiene el botón rojo cortado; solo [Ficha]. "Dar de baja" está al final de la ficha y pide confirmación. Se ve la columna Vehículo.
- **16.18** Tocar ⏻ en la app → en menos de 2 s el mensaje de Telegram cambia a "⏹️ Cámara apagada"; al encender, igual.
- **16.19** App 0.5.5: al día siguiente (o cambiando la fecha del teléfono a mañana), "Hoy" arranca en 0 placas y la tira de abajo queda vacía; el Historial conserva lo de ayer.
- **16.20** Dar de alta en la web una placa que la cámara ya vio como "sin novedad" → llega "🗂️ Ya la viste" con la foto y la lista de ubicaciones; en la web, el aviso arriba de la ficha.
- **16.21** Vehículos: escribir "GC" → la tabla se filtra sola sin Enter; "GCT9" → queda GCT 953; Enter abre la ficha.
- **16.22** Panel NEXCAM → Telegram: un chat sin usuario muestra el aviso amarillo; al asignarle tu usuario Admin, las alertas traen 🔄/🔎 RGM y acreedor/deudor.

**Fase 17: RGM a pedido y acceso desde internet (Prompt 7)**
- **17.1** Con la cámara viendo una placa de la lista (se encola sola), pulsar 🔎 RGM en su alerta → llega "Consultando… (puesto N)" y luego la alerta se reordena con los datos.
- **17.2** Pedir 🔎 RGM, cerrar y abrir NEXORA antes de que termine → igual llega el resultado.
- **17.3** Cerrar Consulta y pedir 🔎 RGM → a los 3 min "⏳ Sigue en cola… ¿Está abierta Consulta en la PC?".
- **17.4** Consulta → Diagnóstico muestra pedido, reservado, resultado y aviso con sus horas.
- **17.5** Correr `publicar-nexora.ps1` → al final dice que https://<dominio>/api/health responde.
- **17.6** Desde el teléfono con datos móviles (sin Wi-Fi de la casa): abrir https://<dominio>, entrar con tu usuario y ver Hoy, Vehículos y NEXCAM.
- **17.7** La página Consulta abierta desde el teléfono dice "Consulta solo se atiende en la PC de la oficina".
- **17.8** 6 contraseñas malas seguidas → bloqueo de 15 min y aviso 🔐 en Telegram.
- **17.9** App NEXCAM compilada con la dirección nueva: con datos móviles inicia sesión, lee placas y llegan las alertas.
- **17.10** Apagar la PC → la app muestra "Sin conexión" y guarda las lecturas; al prender la PC, se envían solas.

**Fase 18: RGM y Judicial en segundo plano (Prompt 8)**
Antes: recargar el Connector en `brave://extensions` y reiniciar NEXORA.
- **18.1** Consulta → LRY517 → RGM: no se abre ni se activa ninguna pestaña; el estado pasa solo de Pendiente a OK, sin pulsar Actualizar.
- **18.2** Judicial de LRY517 con NEXORA al frente todo el tiempo: llega el listado sin tocar el portal.
- **18.3** Cada proceso muestra demandante y demandado; arriba, en verde, "🏦 Demanda la financiera" el que coincide con el acreedor del RGM.
- **18.4** "Ver proceso" en uno de ellos (aunque la cola ya siguió con otra placa) → aparecen detalle y actuaciones en NEXORA.
- **18.5** "Último resultado guardado" se ve como tabla, no JSON.
- **18.6** El proceso privado sale como "🔒 Proceso privado" y no se repite.
- **18.7** Telegram ⚖️ Procesos (Admin): mismo resumen, la financiera primero.
- **18.8** Dejar Brave minimizado 10 min con varias placas en cola → todas terminan.

**Después del Prompt 8d**
- **18.12** Consulta → RGM de LRY517 → Diagnóstico dice "plan A OK" (sin ventana).
- **18.13** Telegram ⚖️ Procesos de GCT 953: grupos "🏦 De la financiera" y "📁 Otros", fechas dd/mm/aaaa, nombres con mayúscula inicial, tarjetas separadas.
- **18.14** Tocar [📄 1] → detalle del proceso y las 10 actuaciones más recientes; ◀ Más antiguas cambia el mismo mensaje; ⬅️ Volver regresa a la lista.
- **18.15** [📑 Proceso completo (PDF)] → llega el PDF con todas las actuaciones; también aparece en la ficha web.
- **18.16** Un proceso privado sale como "🔒 Proceso privado" sin botón.
- **18.17** Página Consulta en dos columnas: a la izquierda buscar, consultar, cola y Últimas 20; a la derecha la placa, datos RGM y los procesos en tarjetas; "Ver proceso" abre el panel lateral con las actuaciones.
- **18.18** Tocar una fila de Últimas 20 → la placa se carga a la derecha sin recargar la página.
- **18.19** Un ítem que queda "En curso" más de 3 min vuelve a intentarse solo y, si no, pasa a Error con "Reintentar".

**Fase 19: Telegram limpio (Prompt 9)**
- **19.1** Escribir /start → no llega el texto largo; abajo queda el teclado con los botones.
- **19.2** 🕒 Últimas → se abre el panel; [⬅️ Volver] → desaparece el panel y también tu toque del botón; el chat queda como antes.
- **19.3** Con Últimas abierto, tocar 📊 Estado → Últimas se borra solo y queda Estado.
- **19.4** Abrir 📋 Ficha desde una alerta → la ficha sale aparte con Volver; al volver, la alerta sigue igual.
- **19.5** Dejar un panel abierto 15 min → se borra solo.
- **19.6** Las alertas con foto, la lista "📷 Cámara encendida" y "🗂️ Ya la viste" nunca se borran.
- **19.7** Estado → 🧹 Limpiar → quedan solo las alertas y la cámara en vivo.
- **19.8** Escribir "hola" → el bot borra el mensaje y no responde con texto.

**Fase 20: alarma fuerte en la app (app 0.5.6, sin prompt: la hizo Claude chat)**
- **20.1** Menú ☰ → "Probar alarma" → suena la sirena 4 s aunque el volumen de música esté bajo, y vibra fuerte.
- **20.2** Apuntar a una placa de la lista → sirena + vibración que se repite, la franja roja "🚨 Placa en lista" parpadea, la foto del carro sale grande debajo de la placa.
- **20.3** Tocar cualquier parte de la alerta (o 🔇 Silenciar alarma) → se calla y deja de vibrar; la alerta sigue en pantalla.
- **20.4** "Seguir escaneando" → se cierra; el volumen de alarma del teléfono vuelve a como estaba.
- **20.5** Posible coincidencia → tres pitidos agudos (otro sonido) con la foto grande.
- **20.6** Con la pantalla en 🌙 → la alerta enciende la pantalla y suena igual.
- **20.7** Nadie toca la alerta → la sirena se calla sola a los 30 s.

### Migrar la base de placas desde WhatsApp (Prompt 3, bloque A)
- Exportar el chat del grupo: WhatsApp → grupo → ⋮ → Más → Exportar chat → Sin archivos. Sale un .zip: no hace falta descomprimirlo.
- No hace falta separar a mano lo que se borra: los "CANCELAR …" y "BAJAR DE BASES …" del mismo chat se detectan solos y salen en el resumen como "para dar de baja".
- El nombre del archivo solo se usa cuando quieres forzar la operación: `CARGAR … .zip` (subir todo), `BORRAR … .txt` (todo lo que traiga se da de baja), `REACTIVAR … .txt`. Sin esas palabras = carga con detección de bajas.
- Puedes editar el .txt antes de subirlo (quitar o agregar placas, cambiarle el nombre): el importador solo busca placas, fechas y las palabras de baja.
- **11.9** Soltar los 3 .zip juntos en Carga masiva → un resumen: por archivo, nuevas, ya activas, dadas de baja, para dar de baja, motos y omitidos; la fecha de ingreso es la del primer mensaje de cada placa.
- **11.10** Enviar `BORRAR … .txt` al bot → resumen "N placas para dar de baja" con [🗑 Dar de baja N] [✖ Cancelar].
- **11.11** Un chat con "UBICAR GCV471" y después "CANCELAR GCV471" → GCV 471 sale en "para dar de baja", no en "para cargar".
- **11.12** Líneas "PLACA  CC  número" → se carga la placa; el número queda como documento del deudor, visible solo para Admin (nunca en la app ni para operadores).
- **11.13** Motos (ej. BEE48H) salen aparte; solo se guardan si marcas "Guardar también las motos".
- **11.14** Cargar ~5.000 placas tarda menos de un minuto, hace copia de la base antes y manda un solo resumen a Telegram.
- **11.15** Subir `UBICACIONES_2.zip` → las placas con ficha quedan con marca, línea, modelo, color, servicio, clase, banco, deudor con documento y sus direcciones.
- **11.17** Ficha de una de esas placas: el banco sale con su nombre corto (RCI, GM Financial, Davivienda…) y cada dirección tiene "Ver en el mapa".
- **11.18** Telegram como Admin: ficha → [🏠 Direcciones (N)]. Como operador: la alerta y la ficha no muestran banco, deudor, documento ni direcciones; la app NEXCAM tampoco.
- **11.16** Después de un RGM de una de esas placas, el color sigue siendo el del chat.

### Resultados

| Prueba | Fecha | Resultado | Nota |
|---|---|---|---|
| 16.4 | 18/11/2026 | OK | En vivo compacto, la foto se amplía |
| 16.7 | 18/11/2026 | OK | App 0.5.4 |
| 16.8 | 18/11/2026 | OK | App 0.5.4 |
| 16.14 | 18/11/2026 | OK | Botones 🌙 y ⏻ visibles |
| 16.15 | 18/11/2026 | OK | Sin placas inventadas de direcciones/cédulas; lee las placas reales escritas en el chat (TSZ716, GWV599), con buen enfoque |
| 16.1 a 16.3, 16.5, 16.6 | 18/11/2026 | Con cambios | Pasan al Prompt 6 (16.9 a 16.13) |
| 16.11 | 20/11/2026 | OK | Datos del vehículo bajo la placa |
| 16.18 | 20/11/2026 | OK | Encender/apagar sin demora |
| 16.17 | 20/11/2026 | Con cambios | Barra horizontal blanca; columnas a justo → Prompt 6b |
| 16.9, 16.10 | 20/11/2026 | Falla | No aparece el botón de RGM → Prompt 6b |
| 16.12, 16.13 | 20/11/2026 | Falla | Sin mejoría en la demora → Prompt 6b |
| 16.20 | 20/11/2026 | Con cambios | "Ya la viste" llega tarde → Prompt 6b |
| 16.12, 16.13 (2.ª vez) | 20/11/2026 | Falla | Grabación: 6 toques seguidos a ◀ Anteriores → solo avanzó 1 página en ~15 s; el botón queda "cargando" (callback sin responder) |
| 16.17 | 20/11/2026 | OK | Pide búsqueda en vivo → 16.21 |
| 16.20 | 20/11/2026 | OK | |
| 17.1, 17.4 | 20/11/2026 | OK | |
| 16.9, 16.10 (2.ª vez) | 20/11/2026 | Falla | Chat de Telegram sin usuario Admin asignado → asignarlo en NEXCAM → Telegram |
| 16.12, 16.13 (log) | 20/11/2026 | Falla | TIEMPO_BOTON ack≈21,7 s total≈43,4 s en todos los toques → conexión a Telegram tarda 21 s (IPv6/TCP) → Prompt 6c punto 1 |
| 16.12, 16.13 (3.ª vez) | 21/11/2026 | OK | ack ≈ 0,5 s tras forzar IPv4 |
| 16.9, 16.10 (3.ª vez) | 21/11/2026 | OK | |
| 16.21 | 21/11/2026 | OK | Búsqueda en vivo |
| 18.1 a 18.8 | 21/11/2026 | Falla | RGM sigue abriendo la pestaña; 500 FK con NHP973; database locked → Prompt 8b |
| 18.9 | 21/11/2026 | OK | Connector v0.2.0 conectado |
| 18.1 (8b) | 21/11/2026 | Falla | Plan B: "Invalid value for bounds… 50% within visible screen space" → ningún RGM termina → Prompt 8c punto 0 |
| 18.1, 18.2 a 18.7, 18.9, 18.10 | 21/11/2026 | OK | RGM por plan B (falsa alarma de captcha → Prompt 8d); Judicial directo OK |
| 7.1 | | | |
