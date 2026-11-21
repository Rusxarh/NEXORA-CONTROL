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

// Prompt 8 punto 3: "Ver proceso" trae detalle + actuaciones por AJAX,
// nunca abre el portal al frente (judicial_tarjetas en _macros.html arma
// el boton con data-placa/data-id-proceso).
document.addEventListener("click", function (e) {
  var boton = e.target.closest(".boton-ver-proceso");
  if (!boton) return;
  var contenedor = boton.nextElementSibling;
  var placa = boton.dataset.placa;
  var idProceso = boton.dataset.idProceso;
  var textoOriginal = boton.textContent;
  boton.disabled = true;
  boton.textContent = "Consultando…";
  var datos = new URLSearchParams();
  datos.set("placa", placa);
  datos.set("id_proceso", idProceso);
  fetch("/rgm/judicial/ver-proceso", { method: "POST", body: datos })
    .then(function (r) { return r.json(); })
    .then(function (resp) {
      boton.disabled = false;
      boton.textContent = textoOriginal;
      if (!resp.ok) {
        contenedor.textContent = "No se pudo consultar: " + (resp.error || "error desconocido");
        contenedor.style.display = "block";
        return;
      }
      var d = resp.detalle || {};
      var actuaciones = resp.actuaciones || [];
      var filas = actuaciones.map(function (a) {
        var anotacion = a.anotacion ? " (" + a.anotacion + ")" : "";
        return "<p>" + (a.fecha_actuacion || "—") + " — " + (a.actuacion || "") + anotacion + "</p>";
      }).join("");
      contenedor.innerHTML =
        "<p>Despacho: " + (d.despacho || "—") + "</p>" +
        "<p>Tipo de proceso: " + (d.tipo_proceso || "—") + (d.clase_proceso ? " · " + d.clase_proceso : "") + "</p>" +
        "<p class=\"mt-1\">Actuaciones (" + actuaciones.length + "), más reciente arriba:</p>" +
        (filas || "<p>Sin actuaciones.</p>");
      contenedor.style.display = "block";
    })
    .catch(function () {
      boton.disabled = false;
      boton.textContent = textoOriginal;
      contenedor.textContent = "No se pudo consultar (sin conexión).";
      contenedor.style.display = "block";
    });
});
