/* Strength: am I progressing on my lifts, and is each muscle group getting enough work?
   Sets come from the hand-written Apple Notes log (source of truth); Garmin only adds duration and HR. */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("strength")) return;
  const S = D.data;
  const M = S.muscle_map;
  const root = document.getElementById("content");
  D.attention(root);
  D.kpis(root, S.kpis);

  let selected = null; // muscle filter
  const filterHooks = [];
  const onFilter = (fn) => { filterHooks.push(fn); fn(); };
  const setFilter = (m) => { selected = selected === m ? null : m; filterHooks.forEach((f) => f()); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && selected) setFilter(selected); });

  const fmtSet = (r) => r.bw ? (r.weight ? `BW+${D.fmt(r.weight, 1)}×${r.reps}` : `BW×${r.reps}`)
    : r.weight === null ? `${r.reps} reps` : `${r.implements === 2 ? "2×" : ""}${D.fmt(r.weight, r.weight % 1 ? 1 : 0)}×${r.reps}`;

  // ================================================================ hero row: muscle map + progression table
  const hero = D.el("div", { class: "hero-row" });
  root.appendChild(hero);

  // ---------------------------------------------------------------- 10 muscle map
  {
    const p = D.panel(hero, { id: "musclemap", title: "Muscle map",
      caption: "Working sets per muscle. Click a region or row to filter the page; Esc clears.",
      method: "A set counts 1 for the primary mover and ½ for each secondary muscle. Hover a region for its numbers." });
    let mode = "volume", win = "7d";
    const modes = D.el("div", { class: "seg", role: "group", "aria-label": "Map mode" });
    [["volume", "Volume"], ["load", "Load"], ["recency", "Recency"]].forEach(([k, l]) => {
      const b = D.el("button", { type: "button", "aria-pressed": String(k === mode) }, l);
      b.addEventListener("click", () => { mode = k; modes.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); winSeg.hidden = k !== "volume"; draw(); });
      modes.appendChild(b);
    });
    const winSeg = D.el("div", { class: "seg", role: "group", "aria-label": "Volume window" });
    [["7d", "7 days"], ["28d", "28 d avg/wk"], ["round", "Current round"]].forEach(([k, l]) => {
      const b = D.el("button", { type: "button", "aria-pressed": String(k === win) }, l);
      b.addEventListener("click", () => { win = k; winSeg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); draw(); });
      winSeg.appendChild(b);
    });
    const ctrl = D.el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px" });
    ctrl.appendChild(modes); ctrl.appendChild(winSeg);
    const pill = D.el("button", { type: "button", class: "filter-pill" });
    pill.addEventListener("click", () => selected && setFilter(selected));
    ctrl.appendChild(pill);
    p.body.appendChild(ctrl);

    const wrap = D.el("div", { class: "mm" });
    const fig = D.el("div", { class: "mm-figure" });
    const tpl = document.getElementById("body-svg");
    fig.innerHTML = tpl ? tpl.innerHTML : "";
    const side = D.el("div");
    const list = D.el("ul", { class: "mm-list", "aria-label": "Muscles ranked" });
    const legend = D.el("div", { class: "mm-legend" });
    const note = D.el("p", { class: "legend-note" });
    side.appendChild(list); side.appendChild(legend); side.appendChild(note);
    wrap.appendChild(fig); wrap.appendChild(side);
    p.body.appendChild(wrap);
    const svg = fig.querySelector("svg");
    if (svg) { svg.removeAttribute("width"); svg.style.width = "100%"; svg.style.maxWidth = "440px"; }
    const band = M.band;

    const legKeys = Object.keys(M.regions).filter((k) => M.regions[k].group === "legs");
    const value = (m) => (mode === "volume" ? M.volume[win][m] : mode === "load" ? M.load[m] : M.recency[m]) || 0;
    const unitTxt = () => (mode === "volume" ? (win === "28d" ? "sets/week (avg of 28 days)" : win === "round" ? `sets since ${D.dateLabel(M.round_start)} (last 3 sessions)` : "sets, last 7 days")
      : mode === "load" ? "load, last 7 days ÷ the muscle's best week in 8 weeks" : "recent-work index (1 ≈ a typical session today, halves every 3 days)");
    function color(m) {
      const c = D.colors(), v = value(m);
      if (!v) return c.seq[0];
      if (mode === "volume") {
        const hi = win === "round" ? band[1] / 2 : band[1];
        if (v > hi) return c.over;
        return c.seq[Math.min(6, 1 + Math.floor((v / hi) * 5.999))];
      }
      const x = Math.min(1, v / (mode === "recency" ? 1.5 : 1));
      return c.seq[Math.min(6, 1 + Math.floor(x * 5.999))];
    }
    function tip(m) {
      const r = M.regions[m], top = (M.top_exercises[m] || []).map(([e, v]) => `${D.esc(e)} (${D.fmt(v, 1)})`).join("<br>");
      return `<b>${D.esc(r.label)}</b><br>Sets 7 d: ${D.fmt(M.volume["7d"][m], 1)} · 28 d avg/wk: ${D.fmt(M.volume["28d"][m], 1)}` +
        `<br>Tonnage 28 d: ${D.fmt(M.tonnage_28d[m] || 0, 0)} kg` +
        `<br>Last trained: ${M.last_trained[m] ? D.dateLabel(M.last_trained[m]) : "never"}` + (top ? `<br><span class="small">Top exercises (28 d):</span><br>${top}` : "");
    }
    function draw() {
      const c = D.colors();
      fig.querySelectorAll(".region").forEach((g) => {
        const m = g.dataset.muscle;
        const isLegs = legKeys.includes(m) && M.legs_note === "leg training not started";
        g.style.fill = isLegs ? c.surface2 : color(m);
        g.classList.toggle("selected", m === selected);
        g.setAttribute("aria-label", `${M.regions[m].label}: ${D.fmt(value(m), 1)}`);
        g.setAttribute("aria-pressed", String(m === selected));
      });
      const keys = Object.keys(M.regions).sort((a, b) => value(b) - value(a));
      const max = Math.max(...keys.map(value), mode === "volume" ? (win === "round" ? band[1] / 2 : band[1]) : 1);
      list.innerHTML = "";
      keys.forEach((m) => {
        const v = value(m);
        const li = D.el("li", { tabindex: "0", role: "button", class: m === selected ? "selected" : "", "aria-pressed": String(m === selected) },
          `<span>${D.esc(M.regions[m].label)}</span><span class="bar" style="width:${(100 * v / max).toFixed(1)}%;background:${color(m)}"></span><span class="val">${D.fmt(v, mode === "volume" ? 1 : 2)}</span>`);
        li.addEventListener("click", () => setFilter(m));
        li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFilter(m); } });
        li.addEventListener("mousemove", (e) => D.tip.show(tip(m), e.clientX, e.clientY));
        li.addEventListener("mouseleave", () => D.tip.hide());
        list.appendChild(li);
      });
      const ramp = `linear-gradient(90deg, ${c.seq.slice(1).join(",")})`;
      legend.innerHTML = mode === "volume"
        ? `<span>0</span><span class="ramp" style="background:${ramp}"></span><span>${win === "round" ? band[1] / 2 : band[1]}</span>` +
          `<span class="sw" style="background:${c.over}"></span><span>above band</span><span class="small mm-target">Weekly target ${band[0]}–${band[1]} sets per muscle</span>`
        : `<span>low</span><span class="ramp" style="background:${ramp}"></span><span>high</span>`;
      note.innerHTML = D.esc(unitTxt()) + (M.legs_note ? `<br><b>${D.esc(D.isoDatesToLabels(M.legs_note[0].toUpperCase() + M.legs_note.slice(1)))}</b>` : "");
      pill.classList.toggle("on", !!selected);
      pill.textContent = selected ? `Filtered: ${M.regions[selected].label} ✕` : "";
    }
    fig.querySelectorAll(".region").forEach((g) => {
      const m = g.dataset.muscle;
      g.addEventListener("click", () => setFilter(m));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFilter(m); } });
      g.addEventListener("mousemove", (e) => D.tip.show(tip(m), e.clientX, e.clientY));
      g.addEventListener("mouseleave", () => D.tip.hide());
      g.addEventListener("focus", () => { const r = g.getBoundingClientRect(); D.tip.show(tip(m), r.right, r.top); });
      g.addEventListener("blur", () => D.tip.hide());
    });
    onFilter(draw);
    D.hooks.push(draw);
  }

  // ---------------------------------------------------------------- 5 progression table
  {
    const p = D.panel(hero, { id: "progression", title: "Progression",
      caption: "Your recent top sets per lift and whether they are still moving up. Stalled lifts first.",
      method: "Top set = heaviest, then most reps, of each of the last 4 sessions. e1RM = estimated one-rep max (Epley; dumbbells per hand, pull-ups include bodyweight), Δ = change vs 4 weeks ago. " +
        "Flat = no e1RM gain across the last 3 sessions; restart = first session after 6+ weeks off." });
    const tbl = D.el("div", { class: "table-wrap", style: "max-height:560px" });
    p.body.appendChild(tbl);
    const draw = () => {
      const rows = S.progression.filter((r) => !selected || r.muscles.includes(selected));
      D.table(tbl, {
        cols: [
          { key: "exercise", label: "Exercise" },
          { key: "status", label: "Status", html: true, fmt: (v) => D.chip(v) },
          { key: "last4", label: "Last 4 top sets →", wrap: true, fmt: (v) => v.map(fmtSet).join(" · ") },
          { key: "e1rm", label: "e1RM kg", num: true, fmt: (v) => D.fmt(v, 1) },
          { key: "delta_4w", label: "Δ 4 wk", num: true, html: true, fmt: (v, r) => {
            // a restart or a new lift has no meaningful 4-week change: a red −48.7 would punish a planned re-entry
            if (v === null || r.status === "restart" || r.status === "new") return '<span class="small" title="no comparable session 4 weeks ago">–</span>';
            const big = Math.abs(v) >= 0.05 * Math.max(r.e1rm || 0, 1);  // colour only changes of 5 % or more
            return `<span style="color:var(${!big ? "--ink-2" : v > 0 ? "--good-text" : "--bad-text"})">${D.signed(v, 1)}</span>`; } },
        ],
        rows: rows.map((r) => Object.assign({ _dim: (new Date(D.meta.today) - new Date(r.last_date)) / 864e5 > 42 }, r)),
      });
    };
    onFilter(draw);
    p.panel.appendChild(D.el("p", { class: "legend-note" }, "Grayed rows: not trained in the last 6 weeks. " + D.esc(S.notes.dumbbell_convention)));
  }

  const grid = D.el("div", { class: "grid" });
  root.appendChild(grid);

  // ---------------------------------------------------------------- 2 e1RM small multiples (+ 9 bodyweight)
  {
    const p = D.panel(grid, { id: "e1rm", title: "Estimated 1RM per lift", full: true,
      caption: "Estimated one-rep max per session for your six most-logged lifts, or the lifts for the muscle selected on the map.",
      method: "Dots = best e1RM of each session (Epley), line = rolling best of the last 3 sessions. " +
        `Pull-ups use bodyweight (${D.fmt(S.notes.bodyweight_kg, 1)} kg, last weigh-in) plus added load.` });
    const box = D.el("div", { style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px" });
    p.body.appendChild(box);
    const charts = [];
    const draw = () => {
      charts.forEach((c) => { c.inst.dispose(); D.charts.splice(D.charts.indexOf(c), 1); });
      charts.length = 0; box.innerHTML = "";
      const list = selected ? Object.keys(S.e1rm).filter((ex) => (M.exercise_muscles[ex] || {})[selected]) : S.top_exercises;
      if (!list.length) { D.placeholder(box, "no logged lift hits this muscle"); return; }
      list.forEach((ex) => {
        const E = S.e1rm[ex];
        if (!E || !E.points.length) return;
        const cell = D.el("div");
        box.appendChild(cell);
        charts.push(D.chart(cell, (ctx) => {
          const c = ctx.c;
          const pts = E.points.filter((x) => D.inRange(x[0]));
          const start = pts.length ? ctx.start : E.points[0][0];
          return D.base(ctx, {
            _aria: `e1RM trend for ${ex}`, legend: { show: false }, grid: { left: 8, right: 12, top: 30, bottom: 6, containLabel: true },
            title: { text: ex + (E.bodyweight ? " (incl. bodyweight)" : "") + " · kg", left: 0, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: c.ink } },
            tooltip: { trigger: "item", backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
              formatter: (q) => q.seriesName === "Session" ? `<b>${D.dateLabel(q.value[0])}</b><br>e1RM ${D.fmt(q.value[1], 1)} kg<br>top set ${q.value[2] === null ? "BW" : D.fmt(q.value[2], 1) + " kg"} × ${q.value[3]}` : `${D.dateLabel(q.value[0])}: best of 3 ${D.fmt(q.value[1], 1)} kg` },
            xAxis: D.timeAxis(ctx, { min: start < ctx.start ? start : ctx.start, splitNumber: 3 }), yAxis: D.valueAxis(ctx, ""),  // small cell: few date ticks
            series: [D.line("Best of 3", E.best3, c.s[0], { step: "end" }), D.dots("Session", E.points, c.s[0], ctx)],
          });
        }, "short"));
      });
    };
    onFilter(draw);
    if (S.bodyweight.length >= 2) {
      const bw = D.el("div", { style: "margin-top:8px" });
      p.body.appendChild(bw);
      D.chart(bw, (ctx) => D.base(ctx, {
        _aria: "Bodyweight entries", legend: { show: false }, grid: { left: 8, right: 12, top: 46, bottom: 6, containLabel: true },
        title: { text: `Bodyweight (${S.bodyweight.length} entries, all shown, for reading strength against weight)`, left: 0, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: ctx.c.ink2 } },
        xAxis: D.timeAxis(ctx, { min: S.bodyweight[0][0] }), yAxis: D.valueAxis(ctx, "kg"),
        series: [D.line("Bodyweight", S.bodyweight, ctx.c.neutral, { showSymbol: true, step: "end" })],
      })).el.style.height = "160px";
    }
  }

  // ---------------------------------------------------------------- 3 weekly hard sets per muscle
  {
    const W = S.weekly_sets;
    const p = D.panel(grid, { id: "weeklysets", title: "Weekly sets per muscle", full: true,
      caption: `Sets per muscle per week against a ${W.band[0]}–${W.band[1]} set target: pale = below, blue = in band, orange = above.`,
      method: `Working sets per ISO week (primary 1, secondary ½). ` +
        `Logged sets start ${D.dateLabel(W.logged_from)}; earlier sessions were tracked in another app (export pending), so older weeks are empty, not zero.` +
        (S.legs_note ? ` Legs: ${D.esc(D.isoDatesToLabels(S.legs_note))}.` : ""),
      table: () => {
        const weeks = W.weeks.filter((w) => w >= D.weekStart(D.rangeStart()) && w >= D.weekStart(W.logged_from));
        return { cols: [{ key: "m", label: "Muscle" }, ...weeks.map((w) => ({ key: w, label: w.slice(5), num: true, fmt: (v) => D.fmt(v, 1) }))],
          rows: W.regions.filter((m) => !selected || m === selected).map((m) => Object.assign({ m: W.labels[m] }, Object.fromEntries(weeks.map((w) => [w, W.values[m][W.weeks.indexOf(w)]])))) };
      } });
    const ch = D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const weeks = W.weeks.filter((w) => w >= D.weekStart(ctx.start) && w >= D.weekStart(W.logged_from));
      const regions = W.regions.filter((m) => !selected || m === selected).slice().reverse();
      const data = [];
      const fill = (v) => (!v ? c.seq[0] : v < W.band[0] ? c.seq[2] : v <= W.band[1] ? c.seq[5] : c.over);
      regions.forEach((m, yi) => weeks.forEach((w, xi) => {
        const v = W.values[m][W.weeks.indexOf(w)] || 0;
        data.push({ value: [xi, yi, v], label: { color: D.textOn(fill(v)) } });  // label readable on its own cell
      }));
      return {
        _aria: "Weekly working sets per muscle group", animation: false,
        grid: { left: 8, right: 16, top: 8, bottom: 44, containLabel: true },
        tooltip: { backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
          formatter: (q) => `${D.esc(W.labels[regions[q.value[1]]])}, week of ${D.dateLabel(weeks[q.value[0]])}: <b>${D.fmt(q.value[2], 1)} sets</b>` + (weeks[q.value[0]] === W.current_week ? " (week in progress)" : "") },
        xAxis: { type: "category", data: weeks.map((w) => w.slice(5)), axisLine: { lineStyle: { color: c.axis } }, axisTick: { show: false }, axisLabel: { color: c.muted, fontSize: 11 } },
        yAxis: { type: "category", data: regions.map((m) => W.labels[m]), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: c.ink2, fontSize: 11 } },
        visualMap: { type: "piecewise", orient: "horizontal", left: 0, bottom: 0, itemWidth: 12, itemHeight: 12, textStyle: { color: c.ink2, fontSize: 11 },
          pieces: [{ value: 0, label: "0", color: c.seq[0] }, { gt: 0, lt: W.band[0], label: `< ${W.band[0]}`, color: c.seq[2] },
            { gte: W.band[0], lte: W.band[1], label: `${W.band[0]}–${W.band[1]} (band)`, color: c.seq[5] }, { gt: W.band[1], label: `> ${W.band[1]}`, color: c.over }] },
        series: [{ type: "heatmap", data, itemStyle: { borderColor: c.surface, borderWidth: 2, borderRadius: 3 },
          label: { show: true, fontSize: 11, formatter: (q) => (q.value[2] ? D.fmt(q.value[2], q.value[2] % 1 ? 1 : 0) : "") } }],
      };
    });
    const resize = () => { const n = selected ? 1 : W.regions.length; ch.el.style.height = (60 + n * 22) + "px"; ch.inst.resize(); ch.draw(); };
    onFilter(resize);
  }

  // ---------------------------------------------------------------- 1 session calendar
  {
    const TYPES = { push: "Push", pull: "Pull", shoulders: "Shoulders", legs: "Legs", core: "Core", unlogged: "Unlogged (Garmin only)", other: "Other" };
    const typeColor = (c, t) => ({ push: c.s[0], pull: c.s[1], shoulders: c.s[2], legs: c.s[3], core: c.s[4] }[t] || c.neutral);
    const p = D.panel(grid, { id: "sessions", title: "Session calendar",
      caption: `One mark per session, coloured by split day (${S.split.join(" → ")}); gray = no sets logged in the notes.`,
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "type", label: "Type", fmt: (v) => TYPES[v] || v }, { key: "title", label: "Note" }, { key: "min", label: "Min", num: true }, { key: "sets", label: "Sets", num: true }],
        rows: S.calendar.filter((r) => D.inRange(r.date)).reverse() }) });
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const start = ctx.start;
      const order = ["push", "pull", "shoulders", "legs", "core", "other", "unlogged"];
      const present = order.filter((t) => S.calendar.some((r) => r.type === t && D.inRange(r.date)));
      return {
        _aria: "Strength sessions by type on a calendar", animation: false,
        legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 10, textStyle: { color: c.ink2, fontSize: 12 }, data: present.map((t) => TYPES[t] || t) },
        tooltip: { backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
          formatter: (q) => `<b>${D.dateLabel(q.value[0])}</b><br>${q.seriesName}${q.data.title ? " · " + D.esc(q.data.title) : ""}<br>${q.data.min ?? "–"} min${q.data.sets ? ", " + q.data.sets + " sets" : ""}` },
        calendar: { range: [start, ctx.end], top: 48, left: 30, right: 10, bottom: 8, cellSize: ["auto", 16], orient: "horizontal",
          itemStyle: { color: c.surface2, borderColor: c.surface, borderWidth: 2 }, splitLine: { show: false }, yearLabel: { show: false },
          dayLabel: { color: c.muted, fontSize: 11, firstDay: 1, nameMap: ["S", "M", "T", "W", "T", "F", "S"] }, monthLabel: { color: c.muted, fontSize: 11 } },
        series: present.map((t) => ({ type: "scatter", coordinateSystem: "calendar", name: TYPES[t] || t, symbol: "roundRect", symbolSize: 12,
          itemStyle: { color: typeColor(c, t) },
          data: S.calendar.filter((r) => r.type === t && D.inRange(r.date)).map((r) => ({ value: [r.date, 1], title: r.title, min: r.min, sets: r.sets })) })),
      };
    }).el.style.height = "230px";
  }

  // ---------------------------------------------------------------- 4 push / pull balance
  {
    const B = S.balance;
    const p = D.panel(grid, { id: "balance", title: "Push / pull balance",
      caption: "Pressing sets ÷ pulling sets over a rolling 4 weeks (isolation work excluded). 1.0 = balanced; above 1 = more pushing than pulling.",
      table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "push", label: "Push sets", num: true }, { key: "pull", label: "Pull sets", num: true }, { key: "r", label: "Ratio (4 wk)", num: true, fmt: (v) => D.fmt(v, 2) }],
        rows: B.weeks.map((w, i) => ({ w, push: B.push[i], pull: B.pull[i], r: B.ratio[i] })).filter((r) => r.r !== null && r.w >= D.weekStart(D.rangeStart())).reverse() }) });
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Push to pull ratio", legend: { show: false },
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "push ÷ pull", { min: 0, scale: false }),
      tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 2)) }),
      series: [D.line("Push/pull ratio", B.weeks.map((w, i) => [w, B.ratio[i]]).filter((x) => x[1] !== null), ctx.c.s[0], { showSymbol: true, symbolSize: 6,
        markLine: { silent: true, symbol: "none", lineStyle: { color: ctx.c.ink2, width: 1, type: "solid" }, label: { formatter: "balanced", color: ctx.c.ink2, position: "insideEndTop" }, data: [{ yAxis: 1 }] } })],
    }), "short");
  }

  // ---------------------------------------------------------------- 6 PR board
  {
    const p = D.panel(grid, { id: "prs", title: "Personal records",
      caption: "All-time and last-90-day bests per exercise.",
      method: "Heaviest working weight (per hand for dumbbells, added load for pull-ups), the reps achieved at that weight, and the best e1RM." });
    const tbl = D.el("div", { class: "table-wrap" });
    p.body.appendChild(tbl);
    const w = (v, r) => (r.bodyweight ? (v ? "BW+" + D.fmt(v, 1) : "BW") : (v === null || v === undefined ? "–" : D.fmt(v, 1)));
    onFilter(() => D.table(tbl, {
      cols: [{ key: "exercise", label: "Exercise" },
        { key: "aw", label: "Heaviest", num: true, fmt: (v, r) => w(r.all.heaviest, r) + (r.all.reps_at_heaviest ? " × " + r.all.reps_at_heaviest : "") },
        { key: "ae", label: "Best e1RM", num: true, fmt: (v, r) => D.fmt(r.all.e1rm, 1) },
        { key: "ad", label: "on", fmt: (v, r) => r.all.e1rm_date || "–" },
        { key: "nw", label: "90 d heaviest", num: true, fmt: (v, r) => (!r.d90.reps_at_heaviest ? "–" : w(r.d90.heaviest, r) + " × " + r.d90.reps_at_heaviest) },
        { key: "ne", label: "90 d e1RM", num: true, fmt: (v, r) => D.fmt(r.d90.e1rm, 1) }],
      rows: S.prs.filter((r) => !selected || (M.exercise_muscles[r.exercise] || {})[selected]).sort((a, b) => (b.all.e1rm || 0) - (a.all.e1rm || 0)),
    }));
  }

  // ---------------------------------------------------------------- 7 session load & density
  {
    const Dn = S.density;
    const p = D.panel(grid, { id: "density", title: "Session load and density",
      caption: "Total weight moved per week; the table adds each session's duration, heart rate and pace.",
      method: "Tonnage = Σ weight × reps of working sets (dumbbells per hand, pull-ups with bodyweight). Duration and average HR come from Garmin.",
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "type", label: "Type" }, { key: "min", label: "Min", num: true }, { key: "avg_hr", label: "Avg HR", num: true },
        { key: "sets", label: "Sets", num: true }, { key: "sets_per_hour", label: "Sets/h", num: true }, { key: "tonnage", label: "Tonnage kg", num: true, fmt: (v) => D.fmt(v, 0) }],
        rows: Dn.sessions.filter((r) => D.inRange(r.date)).reverse() }) });
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Weekly tonnage", legend: { show: false },
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "kg per week", { min: 0, scale: false }),
      tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 0) + " kg") }),
      series: [D.bar("Tonnage", D.weekData(Dn.weeks, Dn.tonnage, S.weekly_sets.current_week, ctx).filter((x) => (Array.isArray(x) ? x[1] : x.value[1])), ctx.c.s[0], { barMaxWidth: 16 })],
    }), "short");
  }

  if (S.unmapped && S.unmapped.length) {
    root.appendChild(D.el("p", { class: "legend-note" }, "Unmapped exercises (no muscle data): " + D.esc(S.unmapped.join(", ")) + " · run make review-exercises"));
  }
  D.arrange(root, [
    { id: "g-now", title: "This week", intro: "Which muscles got work, and which lifts are moving.", layout: "hero-row", ids: ["musclemap", "progression"] },
    { id: "g-trend", title: "Lifts over time", ids: ["e1rm", "weeklysets"] },
    { id: "g-routine", title: "Routine and balance", ids: ["sessions", "balance"] },
  ], ["prs", "density"]);
})();
