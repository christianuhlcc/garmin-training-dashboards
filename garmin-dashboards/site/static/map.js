/* Map: where have I ridden? All outdoor rides as translucent lines; overlaps get denser. */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("map")) return;
  const M = D.data;
  const root = document.getElementById("content");
  if (typeof L === "undefined") { root.innerHTML = '<p class="placeholder">No data: the map library did not load.</p>'; return; }

  const TYPES = { commute: "Commute", long: "Long ride", other: "Other", ebike: "E-bike" };
  const colorOf = (c, t) => ({ commute: c.s[0], long: c.s[1], other: c.s[3], ebike: c.s[4] }[t]);
  const years = [...new Set(M.rides.map((r) => r.d.slice(0, 4)))].sort().reverse();
  const MODES = ["muted", "detailed", "off"];
  const state = { year: "all", types: new Set(M.types), basemap: "muted" };

  // ---- tools
  const tools = D.el("div", { class: "map-tools" });
  const yearSel = D.el("select", { "aria-label": "Year" }, '<option value="all">All years</option>' + years.map((y) => `<option>${y}</option>`).join(""));
  yearSel.addEventListener("change", () => { state.year = yearSel.value; draw(); });
  tools.appendChild(yearSel);
  const chipBtns = {};
  M.types.forEach((t) => {
    const b = D.el("button", { type: "button", "aria-pressed": "true" }, `<span class="swatch"></span>${TYPES[t]}`);
    b.addEventListener("click", () => {
      state.types.has(t) ? state.types.delete(t) : state.types.add(t);
      b.setAttribute("aria-pressed", String(state.types.has(t))); draw();
    });
    chipBtns[t] = b; tools.appendChild(b);
  });
  const baseBtn = D.el("button", { type: "button", title: "Cycle the basemap: muted, detailed, off" });
  const labelBase = () => { baseBtn.textContent = "Basemap: " + state.basemap; };
  baseBtn.addEventListener("click", () => { state.basemap = MODES[(MODES.indexOf(state.basemap) + 1) % MODES.length]; labelBase(); setBasemap(); draw(); });
  labelBase();
  const fitBtn = D.el("button", { type: "button" }, "Show all rides");
  tools.appendChild(baseBtn); tools.appendChild(fitBtn);
  const stats = D.el("span", { class: "small", role: "status" });
  tools.appendChild(stats);
  root.appendChild(tools);

  const box = D.el("div", { class: "map-box", role: "region", "aria-label": "Map of all rides" });
  root.appendChild(box);
  const note = D.el("p", { class: "map-note" },
    `The first and last ${M.trim_m} m of every ride are left out (home, work). Lines are simplified to about ${M.tol_m} m. Indoor and virtual rides have no route and are not shown (${M.n_indoor_skipped}).`);
  root.appendChild(note);
  const tileNote = D.el("p", { class: "map-note", hidden: true });
  root.appendChild(tileNote);

  // ---- decode: parts of [lat1e5, lon1e5, dlat, dlon, ...]
  const decode = (parts) => parts.map((f) => {
    const out = []; let la = f[0], lo = f[1]; out.push([la / 1e5, lo / 1e5]);
    for (let i = 2; i < f.length; i += 2) { la += f[i]; lo += f[i + 1]; out.push([la / 1e5, lo / 1e5]); }
    return out;
  });
  // draw order: e-bike and one-offs first, the routes you ride most often (commute) on top
  const RANK = { ebike: 0, other: 1, long: 2, commute: 3 };
  const lines = M.rides.map((r) => ({ year: r.d.slice(0, 4), type: r.t, km: r.k || 0, ll: decode(r.p) }))
    .sort((a, b) => RANK[a.type] - RANK[b.type]);

  // ---- map
  const map = L.map(box, { zoomControl: true, preferCanvas: true, minZoom: 3, maxZoom: 18 });
  const canvas = L.canvas({ padding: 0.3 });
  const group = L.layerGroup().addTo(map);
  map.attributionControl.setPrefix(false);
  let tiles = null, tileOk = 0, tileErr = 0;
  function setBasemap() {
    if (state.basemap !== "off" && !tiles) {
      // OSM tile policy: browser cache on, no prefetching (keepBuffer small, only when idle), attribution always shown.
      tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, keepBuffer: 1, updateWhenIdle: true, updateWhenZooming: false, crossOrigin: false,
        attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
      });
      tiles.on("tileload", () => { tileOk++; });
      tiles.on("tileerror", () => {
        tileErr++;
        if (!tileOk && tileErr >= 3) {
          tileNote.hidden = false;
          tileNote.textContent = "The basemap tiles did not load. They need a network connection, and OpenStreetMap refuses requests without a referrer, which is what a page opened as a file sends. Run make serve and open http://localhost:8765/map.html. The rides are drawn either way.";
        }
      });
      tiles.addTo(map);
    } else if (state.basemap === "off" && tiles) { map.removeLayer(tiles); tiles = null; tileNote.hidden = true; }
    box.classList.toggle("muted", state.basemap === "muted");
    box.style.background = state.basemap === "off" ? "var(--page)" : "";
  }

  function draw() {
    const c = D.colors();
    group.clearLayers();
    let n = 0, km = 0;
    for (const t of M.types) chipBtns[t].firstChild.style.background = colorOf(c, t);
    const basemap = state.basemap !== "off", muted = state.basemap === "muted";
    for (const r of lines) {
      if (!state.types.has(r.type) || (state.year !== "all" && r.year !== state.year)) continue;
      n++; km += r.km;
      for (const part of r.ll) {
        L.polyline(part, { renderer: canvas, color: colorOf(c, r.type), weight: muted ? 2.4 : basemap ? 2 : 1.6, opacity: muted ? 0.7 : basemap ? 0.4 : 0.55,
          lineCap: "round", lineJoin: "round", interactive: false, smoothFactor: 1 }).addTo(group);
      }
    }
    stats.textContent = `${n} rides · ${Math.round(km).toLocaleString("en")} km`;
  }
  D.onRefresh(draw);

  // start where most riding happens: the median of all points, at city scale
  const all = lines.flatMap((r) => r.ll.flat());
  if (all.length) {
    const med = (a) => a.slice().sort((x, y) => x - y)[Math.floor(a.length / 2)];
    map.setView([med(all.map((p) => p[0])), med(all.map((p) => p[1]))], 11);
    fitBtn.addEventListener("click", () => map.fitBounds(L.latLngBounds(all), { padding: [20, 20] }));
  } else map.setView([48.14, 11.58], 10);
  setBasemap();
  draw();
})();
