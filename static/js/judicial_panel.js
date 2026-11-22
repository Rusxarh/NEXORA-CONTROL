// judicial_panel.js
// Prompt 8d puntos 3 y 4: panel lateral ("drawer", definido una sola vez
// en base.html) con el detalle de un proceso judicial y sus actuaciones
// (línea de tiempo, más reciente arriba, "Cargar más"); filtro por grupo
// y buscador por juzgado/ciudad de las tarjetas (judicial_tarjetas en
// _macros.html, usada por Consulta y por la ficha de Vehículos). Todo
// delegado en `document`, así que sigue funcionando después de que la
// columna derecha de Consulta se reemplace por AJAX (sin recargar la
// página) -- ver _consulta_panel_derecho.html.

(function () {
  var ACTUACIONES_POR_TANDA = 10;
  var actuacionesDrawer = [];
  var mostradasDrawer = 0;

  function escaparHtml(t) {
    return String(t === null || t === undefined ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function cerrarDrawerProceso() {
    var el = document.getElementById("drawerProceso");
    if (el) el.classList.add("hidden");
  }
  window.cerrarDrawerProceso = cerrarDrawerProceso;

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") cerrarDrawerProceso();
  });

  function renderActuacionesDrawer() {
    var cont = document.getElementById("drawerActuacionesLista");
    if (!cont) return;
    var tanda = actuacionesDrawer.slice(0, mostradasDrawer);
    cont.innerHTML = tanda.map(function (a) {
      var anotacion = a.anotacion
        ? '<p class="text-xs text-zinc-400 mt-0.5">' + escaparHtml(a.anotacion) + "</p>" : "";
      var docs = a.con_documentos ? '<span class="text-zinc-500"> · 📎 con documentos</span>' : "";
      return '<div class="border-b border-zinc-800 pb-2 mb-2">' +
        '<p class="text-xs text-zinc-500">📌 ' + escaparHtml(a.fecha_actuacion || "—") + " · " +
        escaparHtml(a.actuacion || "—") + docs + "</p>" + anotacion + "</div>";
    }).join("") || '<p class="text-sm text-zinc-500">Sin actuaciones.</p>';

    var botonMas = document.getElementById("drawerCargarMas");
    if (botonMas) botonMas.classList.toggle("hidden", mostradasDrawer >= actuacionesDrawer.length);
  }

  function abrirDrawerConDatos(placa, idProceso, resp) {
    var d = resp.detalle_formateado || resp.detalle || {};
    actuacionesDrawer = resp.actuaciones_formateadas || resp.actuaciones || [];
    mostradasDrawer = Math.min(ACTUACIONES_POR_TANDA, actuacionesDrawer.length);

    var cont = document.getElementById("drawerProcesoContenido");
    cont.innerHTML =
      '<p class="font-mono text-zinc-100">' + escaparHtml(d.radicado || "—") + "</p>" +
      '<p class="text-zinc-400 text-sm">🏛 ' + escaparHtml(d.despacho || "—") + "</p>" +
      (d.ponente ? '<p class="text-zinc-500 text-xs">Ponente: ' + escaparHtml(d.ponente) + "</p>" : "") +
      (d.tipo_proceso || d.clase_proceso
        ? '<p class="text-zinc-500 text-xs">Tipo: ' + escaparHtml(d.tipo_proceso || "—") +
          (d.clase_proceso ? " · " + escaparHtml(d.clase_proceso) : "") + "</p>"
        : "") +
      (d.ubicacion ? '<p class="text-zinc-500 text-xs">Ubicación: ' + escaparHtml(d.ubicacion) + "</p>" : "") +
      '<p class="text-zinc-500 text-xs mb-2">Radicado el ' + escaparHtml(d.fecha_proceso || "—") + "</p>" +
      '<div class="flex flex-wrap gap-2 mb-3">' +
      '<a class="btn-secondary btn-sm" href="/rgm/judicial/pdf-proceso?placa=' + encodeURIComponent(placa) +
      "&id_proceso=" + encodeURIComponent(idProceso) + '">📑 Descargar PDF</a>' +
      "</div>" +
      '<p class="label-field mb-1">Actuaciones (' + actuacionesDrawer.length + "), más reciente arriba</p>" +
      '<div id="drawerActuacionesLista"></div>' +
      '<button type="button" id="drawerCargarMas" class="btn-secondary btn-sm mt-2 hidden">Cargar más</button>';

    document.getElementById("drawerCargarMas").addEventListener("click", function () {
      mostradasDrawer = Math.min(mostradasDrawer + ACTUACIONES_POR_TANDA, actuacionesDrawer.length);
      renderActuacionesDrawer();
    });
    renderActuacionesDrawer();
  }

  document.addEventListener("click", function (e) {
    var boton = e.target.closest(".boton-ver-proceso");
    if (!boton) return;
    e.preventDefault();
    var placa = boton.dataset.placa;
    var idProceso = boton.dataset.idProceso;
    var drawer = document.getElementById("drawerProceso");
    var contenido = document.getElementById("drawerProcesoContenido");
    if (!drawer || !contenido) return;
    contenido.innerHTML = '<p class="text-sm text-zinc-500">Consultando…</p>';
    drawer.classList.remove("hidden");

    var datos = new URLSearchParams();
    datos.set("placa", placa);
    datos.set("id_proceso", idProceso);
    fetch("/rgm/judicial/ver-proceso", { method: "POST", body: datos })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (!resp.ok) {
          contenido.innerHTML = '<p class="text-sm text-zinc-400">No se pudo consultar: ' +
            escaparHtml(resp.error || "error desconocido") + "</p>";
          return;
        }
        abrirDrawerConDatos(placa, idProceso, resp);
      })
      .catch(function () {
        contenido.innerHTML = '<p class="text-sm text-zinc-400">No se pudo consultar (sin conexión).</p>';
      });
  });

  // --- filtro por grupo + buscador por juzgado/ciudad -------------------

  function aplicarFiltroJudicial(panel) {
    var filtro = panel.dataset.filtroActivo || "todos";
    var buscador = panel.querySelector("[data-buscador-judicial]");
    var texto = buscador && buscador.value ? buscador.value.trim().toLowerCase() : "";
    var visibles = 0;
    panel.querySelectorAll(".judicial-tarjeta").forEach(function (tarjeta) {
      var grupo = tarjeta.dataset.grupoJudicial || "otros";
      var lugar = tarjeta.dataset.lugarJudicial || "";
      var visible = (filtro === "todos" || grupo === filtro) && (!texto || lugar.indexOf(texto) !== -1);
      tarjeta.classList.toggle("hidden", !visible);
      if (visible) visibles++;
    });
    var aviso = panel.querySelector("[data-sin-resultados-judicial]");
    if (aviso) aviso.classList.toggle("hidden", visibles !== 0);
  }

  document.addEventListener("click", function (e) {
    var boton = e.target.closest(".boton-filtro-judicial");
    if (!boton) return;
    var panel = boton.closest(".judicial-panel");
    if (!panel) return;
    panel.querySelectorAll(".boton-filtro-judicial").forEach(function (b) { b.classList.remove("is-activo"); });
    boton.classList.add("is-activo");
    panel.dataset.filtroActivo = boton.dataset.filtroJudicial;
    aplicarFiltroJudicial(panel);
  });

  document.addEventListener("input", function (e) {
    var input = e.target.closest("[data-buscador-judicial]");
    if (!input) return;
    var panel = input.closest(".judicial-panel");
    if (panel) aplicarFiltroJudicial(panel);
  });
})();
