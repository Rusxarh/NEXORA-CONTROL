# NEXCAM en NEXORA: prompts para Claude Code y plan de pruebas

Actualizado: 17/11/2026, 10:00 p. m.

## Cómo empezar

Este archivo vive en dos lugares:
- En el Proyecto NEXORA de Claude (`claude/nexcam-prompts-y-pruebas.md`).
- En tu PC, para que Claude Code lo lea: `C:\Users\Rus\Documents\NEXORA\NEXORA-CONTROL\docs\NEXCAM-PROMPTS-Y-PRUEBAS.md`.

Cuando este archivo cambie, reemplaza la copia de la PC.

Orden de trabajo:

| Paso | Qué le pides a Claude Code | Qué pruebas tú |
|---|---|---|
| 1 | Prompts 0 y 2 | Fases 7 y 10 |
| 2 | Nada nuevo (ya está hecho) | Fases 8 y 9 (RGM y Judicial) |
| 3 | Prompt 3 | Fases 11 a 14 |

### Prompt para arrancar el paso 1 (conversación NUEVA de Claude Code)

```text
Lee completos CLAUDE.md y docs/NEXCAM-PROMPTS-Y-PRUEBAS.md (en
C:\Users\Rus\Documents\NEXORA\NEXORA-CONTROL). Ese archivo es la instrucción
vigente. Trabaja en la rama `nexcam`.
Ejecuta ahora el Prompt 0 y después el Prompt 2, sin preguntarme (sigue CLAUDE.md).
No hagas todavía el Prompt 3.
Push de `nexcam` autorizado al terminar. Al final, repórtame punto por punto qué
quedó hecho de los Prompts 0 y 2, y qué pruebas de las Fases 7 y 10 ya puedo hacer.
```

### Prompt para arrancar el paso 3 (cuando las Fases 7 a 10 estén bien)

```text
Lee de nuevo docs/NEXCAM-PROMPTS-Y-PRUEBAS.md (puede haber cambiado) y ejecuta el
Prompt 3 completo, bloques A a F en orden, sin preguntarme (sigue CLAUDE.md). Rama
`nexcam`, commit por bloque, push autorizado al terminar. Al final, repórtame bloque
por bloque qué quedó hecho y qué pruebas de las Fases 11 a 14 ya puedo hacer.
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
Prompt 3 para NEXORA-CONTROL, rama nexcam, sin preguntar (CLAUDE.md). Bloques A a F,
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

Pruebas (Telegram y portales simulados, PDFs falsos generados en la prueba):
extracción de placas con todos los formatos (incluye pegadas y textos con basura);
reactivación una por una y en grupo; tabla de revisión de oficios con nombres al
estilo NEXDOC; alerta con PDF al Admin y sin PDF al operador hasta que el Admin lo
envíe; aviso silencioso al Admin; ida y vuelta del chat; ningún dato sensible al
operador.
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

### Resultados

| Prueba | Fecha | Resultado | Nota |
|---|---|---|---|
| 7.1 | | | |
