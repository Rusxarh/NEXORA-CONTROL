// Prompt 8 punto 5: tiempo real en Consulta/ficha -- un solo EventSource a
// /rgm/stream (detras de sesion de Admin: /app/stream no sirve aqui porque
// no exige sesion y no deben llegarle datos de RGM/Judicial a quien no la
// tenga). Al llegar rgm.solicitud/rgm.resultado/rgm.error se recarga la
// pagina (con un respiro corto para juntar varios eventos seguidos), asi
// la cola, "Ultimas 20" y "Ultimo resultado guardado" se actualizan solos,
// sin que nadie pulse "Actualizar".
(function () {
  if (!window.EventSource) return;
  var fuente = new EventSource("/rgm/stream");
  var pendiente = null;
  function recargar() {
    pendiente = null;
    window.location.reload();
  }
  function programarRecarga() {
    if (pendiente) return;
    pendiente = setTimeout(recargar, 800);
  }
  ["rgm.solicitud", "rgm.resultado", "rgm.error"].forEach(function (evento) {
    fuente.addEventListener(evento, programarRecarga);
  });
})();

// Prompt 8d punto 3/4: "Ver proceso"/paginado/PDF se mudó al panel lateral
// compartido (drawer en base.html, lógica en static/js/judicial_panel.js) --
// este archivo ya no lo maneja, para no tener dos manejadores del mismo
// botón (ver judicial_panel.js: también delegado en document, así que
// sigue funcionando aunque la columna derecha se reemplace por AJAX).
