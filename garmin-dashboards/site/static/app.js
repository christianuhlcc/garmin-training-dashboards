/* Shared runtime for the dashboards: theme, date range, chart registry, panels, tiles, tables.
   Pages call Dash.init(pageName) and then add panels. Data comes from data/<page>.js (window.DASH_DATA). */
(function () {
  "use strict";
  const D = (window.Dash = {});
  const RANGE_KEY = "dash-range", THEME_KEY = "dash-theme";
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  D.RANGES = { "6w": 42, "3m": 91, "12m": 365, all: null };
  D.charts = [];
  D.hooks = [];

  D.colors = () => ({
    ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), grid: css("--grid"), axis: css("--axis"),
    surface: css("--surface"), surface2: css("--surface-2"), band: css("--band"), neutral: css("--neutral"),
    s: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => css("--s" + i)),
    seq: [0, 1, 2, 3, 4, 5, 6].map((i) => css("--seq-" + i)), over: css("--over"),
    good: css("--good"), warning: css("--warning"), critical: css("--critical"),
  });

  /** Ink or white, whichever reads better on a fill colour (hex or rgb()). */
  D.textOn = (col) => {
    let r, g, b;
    const m = String(col).match(/^#([0-9a-f]{6})$/i);
    if (m) [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    else { const n = String(col).match(/\d+(\.\d+)?/g); if (!n) return css("--ink"); [r, g, b] = n.map(Number); }
    const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return (L + 0.05) / 0.05 >= 1.05 / (L + 0.05) ? "#0b0b0b" : "#ffffff";
  };

  // ------------------------------------------------------------------ formatting
  D.fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
    : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  D.signed = (v, d = 0) => (v === null || v === undefined) ? "" : (v > 0 ? "+" : v < 0 ? "−" : "±") + D.fmt(Math.abs(v), d);
  D.clock = (m) => {
    if (m === null || m === undefined) return "–";
    let x = Math.round(m); x = ((x % 1440) + 1440) % 1440;
    return String(Math.floor(x / 60)).padStart(2, "0") + ":" + String(x % 60).padStart(2, "0");
  };
  D.hours = (h) => {
    if (h === null || h === undefined) return "–";
    const m = Math.round(h * 60); return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
  };
  D.dateLabel = (s) => {
    const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  };
  D.isoDatesToLabels = (t) => String(t).replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (m) => D.dateLabel(m));
  /** "2–8 Nov", "28 Oct – 3 Nov", "29 Dec 2025 – 4 Jan 2026" */
  D.rangeLabel = (a, b) => {
    const A = new Date(a + "T00:00:00"), B = new Date(b + "T00:00:00");
    const mon = (d) => d.toLocaleDateString("en-GB", { month: "short" });
    if (A.getFullYear() !== B.getFullYear()) return D.dateLabel(a) + " – " + D.dateLabel(b);
    if (A.getMonth() === B.getMonth()) return `${A.getDate()}–${B.getDate()} ${mon(B)}`;
    return `${A.getDate()} ${mon(A)} – ${B.getDate()} ${mon(B)}`;
  };
  D.esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ------------------------------------------------------------------ range
  D.weekStart = (iso) => { const d = new Date(iso + "T00:00:00Z"); const wd = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - wd); return d.toISOString().slice(0, 10); };
  /** time-axis min for weekly bars: half a week before the first bar so it clears the y-axis */
  D.weekAxisMin = (start) => D.addDays(D.weekStart(start), -4);
  D.addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  D.rangeStart = () => {
    const days = D.RANGES[D.range];
    const first = D.meta.first_day;
    if (!days) return first;
    const s = D.addDays(D.meta.today, -days + 1);
    return s < first ? first : s;
  };
  D.inRange = (iso) => iso >= D.rangeStart() && iso <= D.meta.today;
  D.filterPts = (pts) => (pts || []).filter((p) => D.inRange(p[0]));

  // ------------------------------------------------------------------ theme
  function applyTheme(t) {
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    const btn = document.getElementById("theme-btn");
    if (btn) {
      const label = t === "dark" ? "Dark" : t === "light" ? "Light" : "Auto";
      const next = t === "dark" ? "light" : t === "light" ? "auto (follows the system)" : "dark";
      btn.innerHTML = `<span class="small-label">Theme</span> ${label}`;
      btn.setAttribute("aria-label", `Theme: ${label}. Switch to ${next}`);
      btn.title = `Switch to ${next}`;
    }
  }

  // ------------------------------------------------------------------ init
  D.init = function (page) {
    D.page = page;
    D.data = (window.DASH_DATA || {})[page];
    if (!D.data) {
      document.getElementById("content").innerHTML = '<p class="placeholder">No data: run <code>make all</code> to build the payloads.</p>';
      return false;
    }
    D.meta = D.data.meta;
    D.range = store.get(RANGE_KEY) || D.meta.default_range || "3m";
    if (!(D.range in D.RANGES)) D.range = "3m";
    applyTheme(store.get(THEME_KEY) || "auto");
    const fresh = document.getElementById("freshness");
    if (fresh) {
      // generated_at = end of the last sync + build on the server ("YYYY-MM-DD HH:MM", Berlin time).
      const [genDay, genTime] = String(D.meta.generated_at || "").split(" ");
      const updated = genDay ? "Updated " + D.dateLabel(genDay) + (genTime ? ", " + genTime : "") : "";
      const through = D.meta.data_through && D.meta.data_through !== genDay
        ? "data through " + D.dateLabel(D.meta.data_through) : "";
      fresh.innerHTML = D.esc([updated, through].filter(Boolean).join(" · ")) +
        (D.meta.stale ? ' · <span class="stale">older than 3 days</span>' : "");
    }
    // "Sync now": only on the hosted site (https). PUT /inbox/sync makes the server sync Garmin + rebuild
    // (deploy/server/logbook.nginx.conf); the browser reuses the site's basic-auth credentials.
    const syncBtn = document.getElementById("sync-btn");
    if (syncBtn && location.protocol === "https:") {
      const status = document.getElementById("sync-status");
      syncBtn.hidden = false;
      syncBtn.addEventListener("click", async () => {
        syncBtn.disabled = true; status.textContent = "Requesting sync…";
        try {
          const r = await fetch("inbox/sync", { method: "PUT", body: "", credentials: "same-origin" });
          status.textContent = r.ok ? "Sync started. Reload (↻) in about 15 seconds."
            : r.status === 429 ? "A sync just ran. Try again in a minute."
            : "Sync request failed (HTTP " + r.status + ").";
        } catch (e) {
          status.textContent = "Sync request failed (offline?).";
        }
        setTimeout(() => { syncBtn.disabled = false; }, 60000);
      });
    }
    // Reload: the Home Screen web app (standalone) has no browser controls.
    const reloadBtn = document.getElementById("reload-btn");
    if (reloadBtn) reloadBtn.addEventListener("click", () => location.reload());
    const seg = document.getElementById("range");
    if (seg) {
      seg.querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.range === D.range));
        b.addEventListener("click", () => {
          D.range = b.dataset.range; store.set(RANGE_KEY, D.range);
          seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
          D.refresh();
        });
      });
    }
    const tb = document.getElementById("theme-btn");
    if (tb) tb.addEventListener("click", () => {
      const cur = store.get(THEME_KEY) || "auto";
      const next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto";
      store.set(THEME_KEY, next); applyTheme(next); D.refresh();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => D.refresh());
    let t; window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => D.charts.forEach((c) => c.inst.resize()), 120); });
    return true;
  };

  D.refresh = function () {
    D.charts.forEach((c) => c.draw());
    D.hooks.forEach((h) => h());
  };
  D.onRefresh = (fn) => { D.hooks.push(fn); fn(); };

  // ------------------------------------------------------------------ DOM helpers
  D.el = (tag, attrs = {}, html) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null) continue;
      if (k === "class") e.className = v; else e.setAttribute(k, v);
    }
    if (html !== undefined) e.innerHTML = html;
    return e;
  };

  /** Panel card: title, one-line caption, tools (Table toggle), body. Returns {panel, body, setCaption}. */
  D.panel = function (parent, o) {
    const p = D.el("section", { class: "panel" + (o.full ? " full" : "") + (o.flat ? " flat" : ""), id: o.id, "aria-labelledby": o.id + "-h" });
    const head = D.el("div", { class: "panel-head" });
    head.appendChild(D.el("h3", { id: o.id + "-h" }, D.esc(o.title)));
    const tools = D.el("div", { class: "tools" });
    head.appendChild(tools);
    p.appendChild(head);
    // caption = one plain-language line; method = how it is computed, collapsed (DESIGN.md: one-line captions)
    const cap = D.el("p", { class: "caption" }, o.caption || "");
    p.appendChild(cap);
    const method = D.el("details", { class: "method" }, '<summary>How it\'s computed</summary><p></p>');
    const setMethod = (h) => { method.querySelector("p").innerHTML = h || ""; method.hidden = !h; };
    setMethod(o.method);
    p.appendChild(method);
    const body = D.el("div", { class: "panel-body" });
    p.appendChild(body);
    parent.appendChild(p);
    const api = { panel: p, body, tools, setCaption: (h) => { cap.innerHTML = h; }, setMethod };
    if (o.table) {
      const tbl = D.el("div", { class: "table-wrap", hidden: "" });
      p.appendChild(tbl);
      const btn = D.el("button", { type: "button", "aria-pressed": "false" }, "Table");
      tools.appendChild(btn);
      const render = () => { if (!tbl.hidden) D.table(tbl, o.table()); };
      btn.addEventListener("click", () => {
        const on = tbl.hidden; tbl.hidden = !on; body.hidden = on;
        btn.setAttribute("aria-pressed", String(on));
        if (on) render(); else D.charts.forEach((c) => c.inst.resize());
      });
      D.hooks.push(render);
    }
    return api;
  };

  /** Regroup the page's panels (by id) into titled sections with a jump list; `more` panels go into a collapsed
      "More charts" section. Panels stay where the page script created them until moved, so charts keep their state. */
  D.arrange = function (root, groups, more = [], opts = {}) {
    const nav = D.el("nav", { class: "jump" + (opts.sticky ? " sticky" : ""), "aria-label": "On this page" });
    const all = [...groups, ...(more.length ? [{ id: "more", title: "More charts" }] : [])];
    nav.innerHTML = all.map((g) => `<a href="#${g.id}">${D.esc(g.title)}</a>`).join("");
    const holders = new Set();
    const take = (id, into) => {
      const el = document.getElementById(id);
      if (!el) return;
      holders.add(el.parentElement);
      into.appendChild(el);
    };
    const lead = root.querySelectorAll(":scope > .kpis, :scope > .health-glance, :scope > .head-note");
    const anchor = lead[lead.length - 1];
    anchor ? anchor.after(nav) : root.prepend(nav);
    let prev = nav;
    for (const g of groups) {
      const sec = D.el("section", { class: "group", id: g.id, "aria-labelledby": g.id + "-gh" });
      sec.appendChild(D.el("h2", { class: "group-title", id: g.id + "-gh" }, D.esc(g.title)));
      if (g.intro) sec.appendChild(D.el("p", { class: "group-intro" }, D.esc(g.intro)));
      const grid = D.el("div", { class: g.layout || "grid" });
      sec.appendChild(grid);
      g.ids.forEach((id) => take(id, grid));
      prev.after(sec); prev = sec;
    }
    if (more.length) {
      const det = D.el("details", { class: "group more", id: "more" });
      const names = more.map((id) => document.getElementById(id + "-h")).filter(Boolean).map((h) => h.textContent);
      det.appendChild(D.el("summary", {}, `<span class="group-title">More charts</span> <span class="small">${D.esc(names.join(", "))}</span>`));
      const grid = D.el("div", { class: "grid" });
      det.appendChild(grid);
      more.forEach((id) => take(id, grid));
      det.addEventListener("toggle", () => { if (det.open) D.charts.forEach((c) => c.inst.resize()); });
      prev.after(det);
    }
    // opt-in: the jump list sticks under the top bar and marks the section in view (aria-current)
    if (opts.sticky) {
      const bar = document.querySelector(".topbar");
      const place = () => { nav.style.top = (bar && getComputedStyle(bar).position === "sticky" ? bar.offsetHeight : 0) + "px"; };
      place(); window.addEventListener("resize", place);
      const links = [...nav.querySelectorAll("a")];
      const secs = links.map((a) => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
      const mark = () => {
        const line = (parseFloat(nav.style.top) || 0) + nav.offsetHeight + 24;
        let cur = null;
        secs.forEach((sec) => { if (sec.getBoundingClientRect().top <= line) cur = sec.id; });
        links.forEach((a) => { if (a.getAttribute("href") === "#" + cur) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
      };
      let raf = 0;
      window.addEventListener("scroll", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(mark); }, { passive: true });
      mark();
    }
    // a link to a panel inside the collapsed section opens it
    const openFor = () => {
      const t = location.hash && document.getElementById(location.hash.slice(1));
      const d = t && t.closest("details");
      if (d && !d.open) { d.open = true; t.scrollIntoView(); }
    };
    window.addEventListener("hashchange", openFor); openFor();
    holders.forEach((h) => { if (h && h !== root && !h.children.length && !h.closest(".group")) h.remove(); });
  };

  /** The page's flagged items (computed in the build, src/metrics/attention.py), first thing under the question.
      Body signals are tinted; training reminders ('note') stay untinted. Nothing flagged = one quiet line. */
  D.attention = function (root) {
    const items = D.data.attention || [];
    if (!items.length) { root.appendChild(D.el("p", { class: "att-none" }, '<span class="chip in_range">On track</span> Nothing flagged here right now.')); return; }
    const sec = D.el("section", { class: "attention", "aria-labelledby": "att-h" });
    sec.appendChild(D.el("h2", { id: "att-h" }, items.length === 1 ? "One thing to look at" : `${items.length} things to look at`));
    const ul = D.el("ul", { class: "att-list" });
    for (const it of items) {
      const li = D.el("li", { class: "att-item " + it.tone });
      li.innerHTML = `<a href="${D.esc(it.href.replace(/^[a-z]+\.html/, ""))}"><span class="att-title">${D.esc(it.title)}</span>` +
        `<span class="att-detail">${D.esc(it.detail)}</span><span class="att-go" aria-hidden="true">↓</span></a>`;
      ul.appendChild(li);
    }
    sec.appendChild(ul);
    root.appendChild(sec);
  };

  D.placeholder = (parent, reason) => parent.appendChild(D.el("p", { class: "placeholder" }, "No data: " + D.esc(reason)));

  /** Table from {cols:[{key,label,num,fmt}], rows:[...]} */
  D.table = function (el, spec) {
    const cols = spec.cols, rows = spec.rows || [];
    let h = '<table class="data"><thead><tr>' + cols.map((c) => `<th class="${c.num ? "num" : ""}">${D.esc(c.label)}</th>`).join("") + "</tr></thead><tbody>";
    for (const r of rows) {
      h += `<tr class="${r._dim ? "dim" : ""}">` + cols.map((c) => {
        let v = c.fmt ? c.fmt(r[c.key], r) : r[c.key];
        if (!c.fmt && typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) v = D.dateLabel(v);  // one date format everywhere
        return `<td class="${c.num ? "num" : ""}${c.wrap ? " wrap" : ""}">${c.html ? v : D.esc(v ?? "–")}</td>`;
      }).join("") + "</tr>";
    }
    if (!rows.length) h += `<tr><td colspan="${cols.length}" class="small">No rows in the selected range.</td></tr>`;
    el.innerHTML = h + "</tbody></table>";
  };

  // ------------------------------------------------------------------ charts
  D.chart = function (parent, build, cls = "") {
    const el = D.el("div", { class: "chart " + cls, role: "img" });
    parent.appendChild(el);
    const inst = echarts.init(el, null, { renderer: "svg" });
    const c = {
      el, inst,
      draw() {
        const opt = build({ c: D.colors(), start: D.rangeStart(), end: D.meta.today });
        if (!opt) return;
        if (opt._aria) el.setAttribute("aria-label", opt._aria);
        delete opt._aria;
        // bars before the time axis' start are drawn clipped against the y-axis instead of hidden: drop them
        const xmin = opt.xAxis && opt.xAxis.type === "time" && opt.xAxis.min;
        if (xmin && Array.isArray(opt.series)) opt.series.forEach((s) => {
          if (s.type !== "bar" || !Array.isArray(s.data)) return;
          s.data = s.data.filter((d) => { const x = Array.isArray(d) ? d[0] : d && d.value ? d.value[0] : null; return !x || x >= xmin; });
        });
        fitLegend(opt, el.clientWidth);
        inst.setOption(opt, true);
      },
    };
    D.charts.push(c);
    c.draw();
    // charts measure their box at init; grids settle afterwards (siblings, wrapping), so follow the box. A width change
    // can change how many rows the legend needs, so redraw (debounced) rather than only resize.
    let lastW = el.clientWidth, t;
    if (window.ResizeObserver) new ResizeObserver(() => {
      inst.resize();
      if (Math.abs(el.clientWidth - lastW) < 8) return;
      lastW = el.clientWidth; clearTimeout(t); t = setTimeout(() => c.draw(), 60);
    }).observe(el);
    return c;
  };

  /** Legends wrap instead of paging (paging hides series). Estimate the rows the legend needs at this width and push
      the plot down by that much, so a wrapped legend never runs into the axis name. */
  function fitLegend(opt, width) {
    const lg = Array.isArray(opt.legend) ? opt.legend[0] : opt.legend;
    if (!lg || lg.show === false || !width) return;
    const names = (lg.data || (opt.series || []).map((x) => x.name)).map((n) => (n && n.name) || n)
      .filter((n) => n && !String(n).startsWith("_"));
    const unique = [...new Set(names)];
    if (!unique.length) return;
    lg.type = "plain";
    const avail = width - 16;
    let rows = 1, x = 0;
    for (const n of unique) {
      const w = 14 + 5 + String(n).length * 6.6 + 10;  // icon + gap + text at 12px + item gap
      if (x && x + w > avail) { rows += 1; x = 0; }
      x += w;
    }
    const grids = Array.isArray(opt.grid) ? opt.grid : opt.grid ? [opt.grid] : [];
    if (rows > 1 && (lg.top === 0 || lg.top === undefined)) {
      grids.forEach((g) => { if (typeof g.top === "number") g.top += (rows - 1) * 20; });
    }
  }

  D.tooltipFormatter = (fmtVal) => (params) => {
    const ps = (Array.isArray(params) ? params : [params]).filter((p) => !String(p.seriesName).startsWith("_"));
    if (!ps.length) return "";
    const x = ps[0].axisValue ?? ps[0].value?.[0];
    let head = typeof x === "number" ? new Date(x).toISOString().slice(0, 10) : x;
    if (/^\d{4}-\d{2}-\d{2}/.test(head)) head = D.dateLabel(head.slice(0, 10));
    let h = `<b>${D.esc(head)}</b>`;
    for (const p of ps) {
      const v = Array.isArray(p.value) ? p.value[1] : p.value;
      if (v === null || v === undefined || v === "-") continue;
      h += `<br>${p.marker}${D.esc(p.seriesName)}: <b>${fmtVal ? fmtVal(v, p) : D.fmt(v, 1)}</b>`;
    }
    return h;
  };

  D.base = function (ctx, extra = {}) {
    const c = ctx.c;
    return Object.assign({
      animation: false,
      textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: c.ink2 },
      grid: { left: 8, right: 16, top: 52, bottom: 8, containLabel: true },  // legend row + axis-name row
      tooltip: {
        trigger: "axis", backgroundColor: c.surface, borderColor: c.axis, borderWidth: 1,
        textStyle: { color: c.ink, fontSize: 12 }, axisPointer: { type: "line", lineStyle: { color: c.axis, width: 1 } },
        formatter: D.tooltipFormatter(),
      },
      legend: { type: "scroll", top: 0, left: 0, right: 0, icon: "roundRect", pageIconSize: 10, pageTextStyle: { color: c.muted }, itemWidth: 14, itemHeight: 8, textStyle: { color: c.ink2, fontSize: 12 },
        data: undefined },
    }, extra);
  };
  D.timeAxis = (ctx, extra = {}) => Object.assign({
    type: "time", min: ctx.start, max: ctx.end,
    axisLine: { lineStyle: { color: ctx.c.axis } }, axisTick: { show: false },
    axisLabel: { color: ctx.c.muted, hideOverlap: true, fontSize: 11 }, splitLine: { show: false },
  }, extra);
  D.valueAxis = (ctx, name, extra = {}) => Object.assign({
    type: "value", name, nameTextStyle: { color: ctx.c.muted, fontSize: 11, align: "left" }, nameGap: 10,
    axisLabel: { color: ctx.c.muted, fontSize: 11 }, splitLine: { lineStyle: { color: ctx.c.grid, width: 1 } },
    axisLine: { show: false }, axisTick: { show: false }, scale: true,
  }, extra);
  D.catAxis = (ctx, data, extra = {}) => Object.assign({
    type: "category", data, axisLine: { lineStyle: { color: ctx.c.axis } }, axisTick: { show: false },
    axisLabel: { color: ctx.c.muted, fontSize: 11, hideOverlap: true },
  }, extra);

  D.line = (name, data, color, extra = {}) => Object.assign({
    type: "line", name, data, showSymbol: false, symbol: "circle", symbolSize: 8, connectNulls: false,
    lineStyle: { width: 2, color, cap: "round", join: "round" }, itemStyle: { color }, emphasis: { disabled: true },
  }, extra);
  D.dots = (name, data, color, ctx, extra = {}) => Object.assign({
    type: "scatter", name, data, symbolSize: 8,
    itemStyle: { color, borderColor: ctx.c.surface, borderWidth: 2 }, emphasis: { scale: 1.4 },
  }, extra);
  D.bar = (name, data, color, extra = {}) => Object.assign({
    type: "bar", name, data, barMaxWidth: 24,
    itemStyle: { color, borderRadius: [4, 4, 0, 0] }, emphasis: { disabled: true },
  }, extra);

  /** Shaded band between lo and hi point lists ([[date, v]]), drawn as two stacked hidden-tooltip lines. */
  D.band = function (lo, hi, color, ctx, name = "_band") {
    const hiMap = new Map(hi.map((p) => [p[0], p[1]]));
    const base = [], diff = [];
    for (const [d, v] of lo) {
      if (!hiMap.has(d)) continue;
      base.push([d, v]); diff.push([d, hiMap.get(d) - v]);
    }
    const stack = name + "-stack";
    return [
      { type: "line", name: "_lo" + name, data: base, stack, showSymbol: false, lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { type: "line", name, data: diff, stack, showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color }, silent: true, tooltip: { show: false } },
    ];
  };

  /** Weekly bars on a time axis; the current (incomplete) week is lighter and hatched. */
  D.weekData = (weeks, values, currentWeek, ctx) => weeks.map((w, i) => {
    const v = values[i];
    if (w === currentWeek) {
      return { value: [w, v], itemStyle: { opacity: 0.45, decal: { symbol: "rect", dashArrayX: [1, 0], dashArrayY: [2, 4], rotation: Math.PI / 4, color: ctx.c.surface } } };
    }
    return [w, v];
  });

  // ------------------------------------------------------------------ KPI tiles
  D.spark = function (vals, color) {
    const v = (vals || []).map((x) => (x === null || x === undefined ? null : Number(x)));
    const nums = v.filter((x) => x !== null);
    if (nums.length < 2) return "";
    const min = Math.min(...nums), max = Math.max(...nums), span = max - min || 1;
    const w = 120, h = 26, step = w / (v.length - 1);
    let d = "", pen = false, last = null;
    v.forEach((x, i) => {
      if (x === null) { pen = false; return; }
      const X = (i * step).toFixed(1), Y = (h - 3 - ((x - min) / span) * (h - 6)).toFixed(1);
      d += (pen ? "L" : "M") + X + " " + Y + " "; pen = true; last = [X, Y];
    });
    const muted = css("--muted");
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">` +
      `<path d="${d}" fill="none" stroke="${muted}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>` +
      (last ? `<circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="${color || css("--s1")}"/>` : "") + "</svg>";
  };

  D.kpis = function (parent, tiles) {
    const wrap = D.el("div", { class: "kpis" });
    for (const t of tiles) {
      const tileEl = D.el("div", { class: "tile" });
      let delta = "";
      if (t.delta !== null && t.delta !== undefined) {
        const good = t.good === "up" ? t.delta > 0 : t.good === "down" ? t.delta < 0 : null;
        // colour only a change that matters (>= 20 % of the previous value), so small dips stay neutral
        const prev = typeof t.value === "number" ? t.value - t.delta : null;
        const big = prev === null || Math.abs(t.delta) >= 0.2 * Math.max(Math.abs(prev), 1);
        const cls = t.delta === 0 || good === null || !big ? "" : good ? "good" : "bad";
        delta = `<span class="delta ${cls}">${D.signed(t.delta, Number.isInteger(t.delta) ? 0 : 1)} ${D.esc(t.delta_label)}</span>`;
      } else if (t.delta_label) delta = `<span class="delta">${D.esc(t.delta_label)}</span>`;
      const value = t.value === null || t.value === undefined ? "–" : (typeof t.value === "number" ? D.fmt(t.value, Number.isInteger(t.value) ? 0 : 1) : D.esc(t.value));
      tileEl.innerHTML = `<span class="label">${D.esc(t.label)}</span>` +
        `<span class="value">${value}<span class="unit">${D.esc(t.unit || "")}</span></span>` + delta +
        (t.note ? `<span class="note">${D.esc(t.note)}</span>` : "") + D.spark(t.spark);
      wrap.appendChild(tileEl);
    }
    parent.appendChild(wrap);
    return wrap;
  };

  // ------------------------------------------------------------------ status chips
  D.STATUS = {
    in_range: ["✓", "In range"], watch: ["!", "Watch"], out_of_range: ["▲", "Out of range"],
    no_baseline: ["·", "No baseline yet"], no_data: ["·", "No data"],
    progressing: ["↗", "Progressing"], flat: ["→", "Flat"], regressing: ["↘", "Regressing"], new: ["·", "New"], restart: ["↺", "Restart"],
    "no e1RM": ["·", "No e1RM"],
  };
  D.chip = (status, suffix) => {
    const [ico, label] = D.STATUS[status] || ["·", status];
    const icon = ico === "·" ? "" : `<span class="ico" aria-hidden="true">${ico}</span>`;  // the dot already marks neutral states
    return `<span class="chip ${D.esc(status)}">${icon}${D.esc(label)}${suffix ? " · " + D.esc(suffix) : ""}</span>`;
  };

  /** Weekly time budget (cycling vs lifting), shown on the Overview. */
  D.budgetOption = function (ctx, B) {
    const c = ctx.c;
    const mk = (name, vals, color) => D.bar(name, B.weeks.map((w, i) => ({ value: [w, vals[i] || null],
      itemStyle: { color, borderRadius: 0, borderColor: c.surface, borderWidth: 1, opacity: w === B.current_week ? 0.45 : 1 } })), color, { stack: "b", barMaxWidth: 18 });
    return D.base(ctx, {
      _aria: "Weekly hours cycling and lifting",
      tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 1) + " h") }),
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "hours", { min: 0, scale: false }),
      series: [mk("Cycling", B.cycling, c.s[0]), mk("Lifting", B.lifting, c.s[1])],
    });
  };

  // shared tooltip box (for non-ECharts graphics like the muscle map)
  D.tip = (() => {
    let box;
    return {
      show(html, x, y) {
        if (!box) { box = D.el("div", { class: "tooltip-box", role: "tooltip" }); document.body.appendChild(box); }
        box.innerHTML = html; box.hidden = false;
        const r = box.getBoundingClientRect();
        box.style.left = Math.min(x + 14, window.innerWidth - r.width - 8) + "px";
        box.style.top = Math.min(y + 14, window.innerHeight - r.height - 8) + "px";
      },
      hide() { if (box) box.hidden = true; },
    };
  })();
})();
