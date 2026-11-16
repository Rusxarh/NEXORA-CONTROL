# Cómo trabajar en este proyecto (instrucciones de Gabriel para Claude Code)

## Decide tú, no me des opciones

- NO me preguntes qué opción elegir. Cuando haya varias formas de hacer algo, elige la
  que recomendarías, hazla y anota la decisión en el reporte final
  ("Decidí X porque Y; si prefieres Z, se cambia así: ...").
- No uses preguntas de selección múltiple. Si algo es ambiguo, toma la opción más
  segura y conservadora y sigue.
- Si una especificación o prompt dice "pregúntame", "propónmelo" o "espera mi OK",
  interprétalo como "decide, anótalo y continúa", salvo los casos de la lista de abajo.
- Trabaja todas las fases seguidas. Commit al terminar cada fase, pruebas en verde,
  y un solo reporte al final con: qué hiciste, qué decidiste, cómo probarlo y qué
  quedó pendiente. Si algo falla y no lo puedes arreglar, déjalo anotado y sigue con
  lo demás.

## Solo te detienes (y me preguntas) en estos casos

1. Borrar o reescribir datos reales (MasterPlacas.db, fotos, Excel). Migraciones
   siempre aditivas; antes de tocar la base, copia de seguridad.
2. `git push`, borrar ramas, `reset --hard`, reescribir historial o descartar
   cambios que no hiciste tú y que NO estén ya en origin.
3. Cualquier secreto (tokens, contraseñas, llaves): nunca al repositorio. Van en
   archivos ignorados por git (telegram.json, .env). La llave de NEXDOC
   (Documents\NEXDOC llave) nunca se toca.
4. Gastar dinero o crear cuentas/servicios externos.
5. Las pruebas automáticas NUNCA mandan mensajes reales (Telegram u otro medio) ni
   hacen consultas reales a un portal externo (RGM, Rama Judicial): siempre contra
   datos/servidores simulados o funciones con las llamadas de red reemplazadas.
   Si hace falta probar contra el servicio real, se hace a mano y se avisa en el
   reporte, nunca dentro de la suite automática.

## Proyecto

- FastAPI + Jinja2 + SQLite (MasterPlacas.db vía database.py). CSS local
  (static/css/app.css), sin Tailwind CDN: clases nuevas en app.css o en un <style>.
- Roles Admin/Operador (usuarios.py). La web es solo para Admin.
- Datos sensibles (deudor, documento, acreedor, radicado, procesos judiciales):
  solo Admin, en web y en Telegram.
- Textos de la interfaz en español sencillo, sin jerga técnica.
- Commits pequeños en español.
