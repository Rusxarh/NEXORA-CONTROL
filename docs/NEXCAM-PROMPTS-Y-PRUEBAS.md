# NEXCAM en NEXORA: prompts para Claude Code y plan de pruebas

Actualizado: 17/11/2026, 10:00 p. m.

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

### Pendiente: migrar la base de placas desde WhatsApp
Falta un ejemplo del chat exportado (WhatsApp → grupo → ⋮ → Más → Exportar chat → Sin archivos) y saber cómo se escribe en el grupo una placa que se borra. Con eso se arma el importador.

### Resultados

| Prueba | Fecha | Resultado | Nota |
|---|---|---|---|
| 7.1 | | | |
