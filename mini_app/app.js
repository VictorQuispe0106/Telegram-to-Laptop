/* Mini App Ocupabilidad — frontend */
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

let initData = tg.initData || "";
let charts = {};

function toast(msg, tipo = "ok", ms = 3500) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + tipo;
  t.style.display = "block";
  if (window.__toastTimer) clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => (t.style.display = "none"), ms);
}

function haptic(tipo = "light") {
  try {
    if (tipo === "ok") tg.HapticFeedback.notificationOccurred("success");
    else if (tipo === "err") tg.HapticFeedback.notificationOccurred("error");
    else tg.HapticFeedback.impactOccurred("light");
  } catch (e) {}
}

function showTab(cual) {
  document.getElementById("panel-reg").classList.toggle("hidden", cual !== "reg");
  document.getElementById("panel-rep").classList.toggle("hidden", cual !== "rep");
  document.getElementById("tab-reg").classList.toggle("active", cual === "reg");
  document.getElementById("tab-rep").classList.toggle("active", cual === "rep");
  if (cual === "rep") cargarReportes();
}

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, {
    ...opciones,
    headers: { "X-Init-Data": initData, "Content-Type": "application/json" },
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "error del servidor");
  return j;
}

/* ---------- registrar ---------- */
const hoyISO = new Date().toISOString().slice(0, 10);
document.getElementById("fecha").value = hoyISO;

async function actualizarEstado() {
  const fecha = document.getElementById("fecha").value || hoyISO;
  const pill = document.getElementById("estado-pill");
  pill.textContent = "verificando…";
  pill.className = "estado-pill pill-no";
  try {
    const e = await api(`/api/estado?fecha=${fecha}`);
    if (e.registrado) {
      pill.textContent = `✔ ya registrado (${e.actividades} actividades)`;
      pill.className = "estado-pill pill-ok";
    } else {
      pill.textContent = "✘ sin registro para esa fecha";
      pill.className = "estado-pill pill-no";
    }
  } catch (err) {
    pill.textContent = "error: " + err.message;
  }
}
document.getElementById("fecha").addEventListener("change", () => {
  document.getElementById("btn-reg").disabled = true;
  document.getElementById("prev-out").textContent = "";
  actualizarEstado();
});
actualizarEstado();

async function previsualizar() {
  const btn = document.getElementById("btn-prev");
  btn.disabled = true;
  try {
    const fecha = document.getElementById("fecha").value || hoyISO;
    const r = await api("/api/registrar", {
      method: "POST",
      body: JSON.stringify({ fecha, dry_run: true }),
    });
    document.getElementById("prev-out").textContent =
      `📋 ${r.modo}: ${r.actividades} actividades × 18 min · ${r.horas} horas asignadas`;
    document.getElementById("btn-reg").disabled = false;
    haptic();
  } catch (err) {
    toast("❌ " + err.message, "err");
    haptic("err");
  } finally {
    btn.disabled = false;
  }
}

async function registrar() {
  const btn = document.getElementById("btn-reg");
  btn.disabled = true;
  btn.textContent = "⏳ Guardando…";
  try {
    const fecha = document.getElementById("fecha").value || hoyISO;
    const r = await api("/api/registrar", {
      method: "POST",
      body: JSON.stringify({ fecha, dry_run: false }),
    });
    haptic("ok");
    toast(`✅ Registrado: ${r.modo}, ${r.actividades} actividades, ${r.horas}h`, "ok");
    actualizarEstado();
    document.getElementById("prev-out").textContent = "";
  } catch (err) {
    haptic("err");
    toast("❌ " + err.message, "err", 5000);
  } finally {
    btn.textContent = "✅ Registrar en la base";
  }
}

/* ---------- reportes (espejo del original: selector mes/año + 4 charts + heatmap + tabla) ---------- */
const PALETA = ["#2ea6ff", "#31b545", "#f5a623", "#e05b4c", "#9b6dff", "#2ec7c7",
  "#ff7ab2", "#7dd6ff", "#b6e05b", "#ffb457"];
const MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
let mesSel = new Date().getMonth() + 1;
let anioSel = new Date().getFullYear();

function cambiarMes(delta) {
  mesSel += delta;
  if (mesSel > 12) { mesSel = 1; anioSel++; }
  if (mesSel < 1) { mesSel = 12; anioSel--; }
  cargarReportes();
}

function primerNombre(n) { return n.split(" ").slice(0, 2).join(" "); }

async function cargarReportes() {
  try {
    const r = await api(`/api/reportes?mes=${mesSel}&anio=${anioSel}`);
    document.getElementById("lbl-mes").textContent = `${MESES[mesSel]} ${anioSel}`;
    document.getElementById("r-total-horas").textContent = Math.round(r.total_horas);
    document.getElementById("r-analistas").textContent = r.analistas.length;
    document.getElementById("r-mis-dias").textContent = Object.keys(r.dias_usuario).length;

    const nombres = r.analistas.map(a => primerNombre(a.nombre));

    // 1. horas por analista (barra)
    if (charts.horas) charts.horas.destroy();
    charts.horas = new Chart(document.getElementById("ch-horas"), {
      type: "bar",
      data: { labels: nombres,
        datasets: [{ data: r.analistas.map(a => a.total_horas),
          backgroundColor: PALETA, borderRadius: 6 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } } },
    });

    // 2. donut distribución
    if (charts.donut) charts.donut.destroy();
    charts.donut = new Chart(document.getElementById("ch-donut"), {
      type: "doughnut",
      data: { labels: nombres,
        datasets: [{ data: r.analistas.map(a => a.porcentaje),
          backgroundColor: PALETA, borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "right", labels: { color: getComputedStyle(document.body).getPropertyValue("--fg"), font: { size: 10 }, boxWidth: 10 } } } },
    });

    // 3. dias que llenó (barra horizontal, vs dias del mes)
    if (charts.dias) charts.dias.destroy();
    charts.dias = new Chart(document.getElementById("ch-dias"), {
      type: "bar",
      data: { labels: nombres,
        datasets: [
          { label: "registrados", data: r.analistas.map(a => a.dias_registrados),
            backgroundColor: "#31b545", borderRadius: 6 },
          { label: "del mes", data: r.analistas.map(() => r.dias_del_mes),
            backgroundColor: "rgba(255,255,255,.12)", borderRadius: 6 },
        ] },
      options: { responsive: true, maintainAspectRatio: false, indexAxis: "y",
        plugins: { legend: { labels: { color: getComputedStyle(document.body).getPropertyValue("--fg"), font: { size: 10 }, boxWidth: 10 } } } },
    });

    // 4. evolucion diaria (una linea por analista)
    if (charts.evo) charts.evo.destroy();
    const diasMax = r.dias_del_mes;
    const labelsEvo = Array.from({ length: diasMax }, (_, i) => i + 1);
    const colores = {};
    r.analistas.forEach((a, i) => colores[a.nombre] = PALETA[i % PALETA.length]);
    charts.evo = new Chart(document.getElementById("ch-evo"), {
      type: "line",
      data: { labels: labelsEvo,
        datasets: r.analistas.map(a => ({
          label: primerNombre(a.nombre),
          data: labelsEvo.map(d => (r.evolucion[a.nombre] || {})[d] || null),
          borderColor: colores[a.nombre], backgroundColor: colores[a.nombre],
          tension: 0.3, pointRadius: 2, borderWidth: 2,
        })) },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: getComputedStyle(document.body).getPropertyValue("--fg"), font: { size: 10 }, boxWidth: 10 } } } },
    });

    // 5. heatmap propio (completo 8h / parcial / sin registro)
    const hm = document.getElementById("heatmap");
    hm.innerHTML = "";
    const anio = anioSel, mes = mesSel;
    const offset = new Date(anio, mes - 1, 1).getDay(); // 0 domingo
    for (let i = 0; i < offset; i++) hm.insertAdjacentHTML("beforeend", "<div></div>");
    for (let d = 1; d <= diasMax; d++) {
      const h = (r.dias_usuario || {})[d] || 0;
      let cls = "heatmap-dia";
      if (h >= 8) cls += " hm-ok";
      else if (h > 0) cls += " hm-parcial";
      const dow = new Date(anio, mes - 1, d).getDay();
      if (dow === 0 || dow === 6) cls += " hm-domingo";
      hm.insertAdjacentHTML("beforeend", `<div class="${cls}">${d}</div>`);
    }

    // 6. tabla resumen
    const head = document.getElementById("tabla-head");
    head.innerHTML = "<th style='text-align:left;padding:4px'>Analista</th>"
      + "<th>Días</th><th>Horas</th><th>%</th><th>Prom</th><th>Cump</th>";
    const tbody = document.querySelector("#tabla-resumen tbody");
    tbody.innerHTML = r.analistas.map(a =>
      `<tr><td style='text-align:left;padding:4px'>${primerNombre(a.nombre)}</td>`
      + `<td style='text-align:center'>${a.dias_registrados}</td>`
      + `<td style='text-align:center'>${Math.round(a.total_horas)}</td>`
      + `<td style='text-align:center'>${a.porcentaje}%</td>`
      + `<td style='text-align:center'>${a.promedio_dia}</td>`
      + `<td style='text-align:center'>${a.cumplimiento}%</td></tr>`).join("");

    haptic();
  } catch (err) {
    toast("❌ Reportes: " + err.message, "err", 5000);
  }
}
