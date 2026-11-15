/* Cycling: is my cycling fitness coming back, and is my training distributed the way I intend? */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("cycling")) return;
  const C = D.data;
  const root = document.getElementById("content");
  D.attention(root);
  D.kpis(root, C.kpis);
  const grid = D.el("div", { class: "grid" });
  root.appendChild(grid);

  /* A host is one card that groups related blocks under a single heading; the blocks inside are flat panels. */
  const host = (id, title, caption, cls) => {
    const sec = D.el("section", { class: "panel host full", id, "aria-labelledby": id + "-h" });
    if (title) {  // a host that is the only card in its section takes the section's heading instead
      sec.appendChild(D.el("h2", { id: id + "-h" }, D.esc(title)));
      sec.appendChild(D.el("p", { class: "caption" }, D.esc(caption)));
    } else sec.removeAttribute("aria-labelledby");
    const body = D.el("div", { class: "blocks " + cls });
    sec.appendChild(body); grid.appendChild(sec);
    return body;
  };
  const hLoad = host("h-load", "Training load by week", "How much you rode, and whether this week is ordinary for you.", "cols-2");
  const hMix = host("h-mix", null, null, "cols-3");
  const hRoutes = host("h-routes", null, null, "stack");

  const TYPES = { commute: "Commute", long: "Long ride", indoor: "Indoor", other: "Other", ebike: "E-bike" };
  const typeColor = (c, t) => ({ commute: c.s[0], long: c.s[1], indoor: c.s[2], other: c.s[3], ebike: c.s[4] }[t]);
  const hatch = (c) => ({ symbol: "rect", dashArrayX: [1, 0], dashArrayY: [2, 3], rotation: Math.PI / 4, color: c.surface });
  const weekly = (weeks, vals) => weeks.map((w, i) => [w, vals[i]]);
  const weekTable = (weeks, cols) => weeks.map((w, i) => Object.assign({ w }, Object.fromEntries(cols.map(([k, arr]) => [k, arr[i]]))))
    .filter((r) => r.w >= D.weekStart(D.rangeStart())).reverse();

  // ---------------------------------------------------------------- 1 fitness / fatigue / form
  {
    const F = C.fitness;
    let variant = "excl_ebike";
    const q = Object.entries(F.quality || {}).map(([k, v]) => `${v} ${k.replace("stream_", "").replace("avg_hr", "avg-HR only")}`).join(", ");
    const p = D.panel(grid, { id: "fitness", title: "Fitness, fatigue, form", full: true,
      caption: "Fitness builds slowly over weeks, fatigue reacts to the last few days, and form is the gap between them. Bars below = daily load.",
      method: `Fitness = CTL (42-day average of load), fatigue = ATL (7-day), form = TSB (yesterday's fitness − fatigue), from heart-rate load (Banister TRIMP, ${D.esc(F.unit)} units). ` +
        `The first 42 days are hidden as warm-up. HR source of the rides: ${D.esc(q)}; wrist HR on commutes is less reliable. ` +
        (F.unit === "TRIMP" ? "Switches to TSS once a tested FTP is set in config.yaml." : ""),
      table: () => {
        const V = F.variants[variant];
        const m = (a) => new Map(a.map((x) => [x[0], x[1]]));
        const atl = m(V.atl), tsb = m(V.tsb), load = m(V.load);
        return { cols: [{ key: "d", label: "Date" }, { key: "l", label: "Load", num: true }, { key: "ctl", label: "CTL", num: true }, { key: "atl", label: "ATL", num: true }, { key: "tsb", label: "TSB", num: true }],
          rows: D.filterPts(V.ctl).filter((x) => x[0] >= F.warmup_end).reverse().map(([d, ctl]) => ({ d, ctl, atl: atl.get(d), tsb: tsb.get(d), l: load.get(d) })) };
      } });
    const btn = D.el("button", { type: "button", "aria-pressed": "false" }, "Include e-bike");
    p.tools.prepend(btn);
    btn.addEventListener("click", () => { variant = variant === "excl_ebike" ? "incl_ebike" : "excl_ebike"; btn.setAttribute("aria-pressed", String(variant === "incl_ebike")); D.refresh(); });
    D.chart(p.body, (ctx) => {
      const c = ctx.c, V = F.variants[variant];
      const warm = (pts) => pts.filter((x) => x[0] >= F.warmup_end);
      return D.base(ctx, {
        _aria: "Fitness, fatigue and form over time",
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, F.unit, { scale: false }),
        series: [
          D.line("Fitness (CTL)", warm(V.ctl), c.s[0]),
          D.line("Fatigue (ATL)", warm(V.atl), c.s[1], { lineStyle: { width: 1.5, color: c.s[1] } }),
          D.line("Form (TSB)", warm(V.tsb), c.s[2], { areaStyle: { color: c.s[2], opacity: 0.1 },
            markLine: { silent: true, symbol: "none", lineStyle: { color: c.axis, width: 1, type: "solid" }, label: { show: false }, data: [{ yAxis: 0 }] } }),
        ],
      });
    });
    // daily load in its own strip: its scale (single rides up to several hundred) would flatten CTL/ATL on a shared axis
    D.chart(p.body, (ctx) => {
      const c = ctx.c, V = F.variants[variant];
      return D.base(ctx, {
        _aria: "Daily training load", legend: { show: false }, grid: { left: 8, right: 16, top: 22, bottom: 8, containLabel: true },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 0)) }),
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "daily load", { min: 0, scale: false, splitNumber: 2 }),
        series: [D.bar("Daily load", D.filterPts(V.load).filter((x) => x[1] > 0), c.neutral, { barMaxWidth: 6 })],
      });
    }).el.style.height = "120px";
  }

  // ---------------------------------------------------------------- 2 weekly volume by ride type
  {
    const V = C.volume;
    let metric = "hours";
    const unit = { hours: "h", km: "km", load: C.fitness.unit };
    const p = D.panel(hLoad, { flat: true, id: "volume", title: "Weekly volume by ride type",
      caption: "Riding per week by type; e-bike is hatched and the current week is still in progress.",
      method: "Stacked per ISO week. Commute = weekday ride of ~13 km between your two most frequent endpoints; long = ≥ 150 min moving; e-bike is not counted as training load anywhere else.",
      table: () => ({ cols: [{ key: "w", label: "Week of" }, ...V.types.map((t) => ({ key: t, label: TYPES[t], num: true, fmt: (v) => D.fmt(v, metric === "hours" ? 1 : 0) }))],
        rows: weekTable(V.weeks, V.types.map((t) => [t, V.series[metric][t]])) }) });
    const seg = D.el("div", { class: "seg", role: "group", "aria-label": "Volume unit" });
    ["hours", "km", "load"].forEach((m) => {
      const b = D.el("button", { type: "button", "aria-pressed": String(m === metric) }, m);
      b.addEventListener("click", () => { metric = m; seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); D.refresh(); });
      seg.appendChild(b);
    });
    p.tools.prepend(seg);
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      return D.base(ctx, {
        _aria: "Weekly cycling volume by ride type",
        xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, unit[metric], { min: 0, scale: false }),
        series: V.types.map((t) => D.bar(TYPES[t], V.weeks.map((w, i) => {
          const v = V.series[metric][t][i];
          const style = { color: typeColor(c, t), borderRadius: 0, borderColor: c.surface, borderWidth: 1 };
          if (t === "ebike") style.decal = hatch(c);
          if (w === V.current_week) style.opacity = 0.45;
          return { value: [w, v || null], itemStyle: style };
        }), typeColor(c, t), { stack: "v", barMaxWidth: 18 })),
      });
    });
  }

  // ---------------------------------------------------------------- 2b weekly effort range
  {
    const R = C.effort_range;
    const rows = R.weeks.concat(R.current ? [Object.assign({ cur: true }, R.current)] : []);
    const p = D.panel(hLoad, { flat: true, id: "effort", title: "Weekly effort range",
      caption: "Is this week's riding load normal for you right now? Bars inside the pale band are ordinary; only a clear break is worth a thought.",
      method: `Load per ISO week (${D.esc(R.unit)}, rides only, e-bike excluded, same series as the fitness chart). The band is ${Math.round(100 * R.band[0])}–${Math.round(100 * R.band[1])} % of the average of the 3 completed weeks before that week, so it moves with you; the first weeks have no band. ` +
        "The tick on the current week marks the same pace as the last 3 weeks, up to today.",
      table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "load", label: "Load", num: true, fmt: (v) => D.fmt(v, 0) }, { key: "lo", label: "Range from", num: true, fmt: (v) => D.fmt(v, 0) }, { key: "hi", label: "to", num: true, fmt: (v) => D.fmt(v, 0) }],
        rows: rows.filter((r) => r.week >= D.weekStart(D.rangeStart())).map((r) => ({ w: r.week + (r.cur ? " (so far)" : ""), load: r.load, lo: r.lo, hi: r.hi })).reverse() }) });
    if (!rows.some((r) => r.lo !== null)) D.placeholder(p.body, "needs 4 weeks of riding to form a range");
    else D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const inBand = rows.filter((r) => r.lo !== null);
      const color = (r) => (r.lo === null ? c.neutral : r.load > r.hi ? c.over : r.load < r.lo ? c.seq[3] : c.neutral);
      const cur = R.current;
      return D.base(ctx, {
        _aria: "Weekly riding load against your recent range", legend: { show: false },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: (ps) => {
          const w = ps.find((q) => q.seriesName === "Load"); if (!w) return "";
          const r = rows.find((x) => x.week === w.value[0]);
          return `Week of ${D.dateLabel(r.week)}${r.cur ? " (so far)" : ""}<br>Load <b>${D.fmt(r.load, 0)}</b>` + (r.lo !== null ? `<br>Usual range ${D.fmt(r.lo, 0)}–${D.fmt(r.hi, 0)}` : "<br>No range yet");
        } }),
        xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, R.unit, { min: 0, scale: false }),
        series: [
          ...D.band(inBand.map((r) => [r.week, r.lo]), inBand.map((r) => [r.week, r.hi]), c.seq[1], ctx, "_range"),
          D.bar("Load", rows.map((r) => ({ value: [r.week, r.load || null], itemStyle: { color: color(r), borderRadius: [4, 4, 0, 0], opacity: r.cur ? 0.55 : 1 },
            label: r.cur ? { show: true, position: "top", color: c.ink2, fontSize: 11, formatter: "this week so far" } : undefined })), c.neutral, { barMaxWidth: 18, z: 3 }),
          ...(cur && cur.pace_marker !== null ? [{ type: "scatter", name: "Same pace as last 3 weeks", data: [[cur.week, cur.pace_marker]], symbol: "rect", symbolSize: [26, 3], itemStyle: { color: c.ink }, z: 4, silent: true }] : []),
        ],
      });
    });
  }

  // ---------------------------------------------------------------- 2c matched commute
  {
    const M = C.matched;
    const p = D.panel(hRoutes, { flat: true, id: "matched", title: "Commute, same route",
      caption: "Same route, same direction. A shorter moving time at the same effort, or fewer heartbeats per km, means you are fitter.",
      method: "Rides between your two most frequent endpoints (within 400 m), in one direction, with a distance within 10 % of that route's median; detours are dropped, and a route needs at least 6 rides. " +
        "Line = median of the last 5 rides. Heartbeats per km = total beats ÷ distance, so it falls when the same ride costs your heart less; it uses chest-strap rides only because wrist HR drifts. " +
        "Stopped = share of elapsed time not moving (traffic lights), which moves moving time around.",
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "dir", label: "Route" }, { key: "min", label: "Moving min", num: true, fmt: (v) => D.fmt(v, 1) },
        { key: "kmh", label: "km/h", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "bpk", label: "Beats/km", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "avg_hr", label: "Avg HR", num: true },
        { key: "stop", label: "Stopped %", num: true, fmt: (v) => D.fmt(v, 0) }, { key: "hr", label: "HR" }],
        rows: M.groups.flatMap((g) => g.rides.filter((r) => D.inRange(r.date)).map((r) => ({ date: r.date, dir: g.label, min: r.moving_s / 60, kmh: r.kmh, bpk: r.beats_per_km,
          avg_hr: r.avg_hr === null ? null : Math.round(r.avg_hr), stop: r.stop_share === null ? null : 100 * r.stop_share, hr: r.strap ? "strap" : "wrist" }))).sort((a, b) => (a.date < b.date ? 1 : -1)) }) });
    if (!M.groups.length) D.placeholder(p.body, "no route with at least 6 matching rides");
    else {
      const row = D.el("div", { class: "grid" }); p.body.appendChild(row);
      M.groups.forEach((g) => {
        const box = D.el("div"); row.appendChild(box);
        const tr = g.trend, n = g.rides.length;
        const dm = tr.moving_s_delta, parts = [`${g.n} rides, ${D.fmt(g.km, 1)} km`];
        if (dm !== null && tr.prior_n >= 2) parts.push(`latest ${D.fmt(Math.abs(dm) / 60, 1)} min ${dm <= 0 ? "faster" : "slower"} than the previous 8 weeks' median`);
        box.appendChild(D.el("h3", { class: "sub" }, `${g.label}: ${parts.join(" · ")}`));
        const dotTip = (unit, f) => ({ trigger: "item", backgroundColor: "var(--surface)", formatter: (q) => `<b>${D.dateLabel(q.value[0])}</b><br>${f(q.value[1])} ${unit}` });
        D.chart(box, (ctx) => {
          const c = ctx.c;
          return D.base(ctx, {
            _aria: `Moving time per ride, ${g.label}`, legend: { show: false }, grid: { left: 8, right: 16, top: 26, bottom: 8, containLabel: true },
            tooltip: Object.assign(dotTip("min moving", (v) => D.fmt(v, 1)), { backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 } }),
            xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "moving min"),
            series: [D.dots("Ride", g.rides.map((r) => [r.date, r.moving_s / 60]), c.s[0], ctx),
              D.line("Median of last 5", g.rides.filter((r) => r.moving_med5 !== null).map((r) => [r.date, r.moving_med5 / 60]), c.s[0])],
          });
        }, "short");
        const strap = g.rides.filter((r) => r.beats_per_km !== null);
        if (!strap.length) return;
        D.chart(box, (ctx) => {
          const c = ctx.c;
          return D.base(ctx, {
            _aria: `Heartbeats per km, ${g.label}`, legend: { show: false }, grid: { left: 8, right: 16, top: 26, bottom: 8, containLabel: true },
            tooltip: Object.assign(dotTip("beats/km", (v) => D.fmt(v, 1)), { backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 } }),
            xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "beats per km"),
            series: [D.dots("Ride", strap.map((r) => [r.date, r.beats_per_km]), c.s[1], ctx),
              D.line("Median of last 5", g.rides.filter((r) => r.beats_med5 !== null).map((r) => [r.date, r.beats_med5]), c.s[1])],
          });
        }, "short");
      });
      if (!M.n_strap) p.body.appendChild(D.el("p", { class: "collapsed-note" }, "Heartbeats per km is not shown: none of these rides was recorded with a chest strap (wrist HR drifts too much for this comparison)."));
      if (M.n_strap) p.body.appendChild(D.el("p", { class: "collapsed-note" }, `Heartbeats per km uses chest-strap rides only (${M.n_strap} of ${M.groups.reduce((a, g) => a + g.n, 0)} matched rides).`));
    }
  }

  // ---------------------------------------------------------------- 2d best efforts
  {
    const B = C.best_efforts;
    const mmss = (t) => { if (t === null || t === undefined) return "–"; const m = Math.floor(t / 60), s = Math.round(t - 60 * m); return s === 60 ? `${m + 1}:00` : `${m}:${String(s).padStart(2, "0")}`; };
    const cell = (e) => (e ? `${mmss(e.time_s)} · ${D.fmt(e.kmh, 1)} km/h · ${D.dateLabel(e.date)}` : "–");
    const p = D.panel(grid, { id: "efforts", title: "Best efforts", full: true,
      caption: "Your fastest 1, 5, 10 and 20 km within a single ride. GPS speed at about 2–3 s recording; wind, descents and traffic lights are included.",
      method: `Fastest window of each distance inside one outdoor ride (${B.n_rides} rides, e-bike excluded), from the speed stream on a 1-s grid. Windows across a recording gap over 10 s, a sample over 70 km/h or a GPS jump are dropped. ` +
        "Chart = how the best time at each distance improved, shown as average speed so the distances share one axis. For power best efforts see the <a href=\"#power\">Power panel</a>." });
    const split = D.el("div", { class: "split t57" }); p.body.appendChild(split);
    const left = D.el("div"), right = D.el("div"); split.appendChild(left); split.appendChild(right);
    const wrap = D.el("div", { class: "table-wrap compact" });
    left.appendChild(wrap);
    D.table(wrap, { cols: [{ key: "d", label: "Distance" }, { key: "t", label: "Best", num: true, html: true }, { key: "kmh", label: "km/h", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "date", label: "Date" },
      { key: "r2", label: "2nd best", wrap: true }, { key: "r3", label: "3rd best", wrap: true }],
      rows: B.distances.map((d) => { const pr = B.prs[d], t = B.top3[d];
        return { d: d + " km", t: pr ? `${D.esc(mmss(pr.time_s))}${pr.new_best ? ' <span class="chip ok">new best</span>' : ""}` : "–", kmh: pr ? pr.kmh : null, date: pr ? pr.date : null,
          r2: cell(t[1]), r3: cell(t[2]) }; }) });
    const rec = B.records || {};
    const bits = [rec.longest_km && `longest ${D.fmt(rec.longest_km.value, 1)} km (${D.dateLabel(rec.longest_km.date)})`, rec.longest_min && `longest ${D.fmt(rec.longest_min.value / 60, 1)} h moving (${D.dateLabel(rec.longest_min.date)})`,
      rec.climb_m && `biggest climb ${D.fmt(rec.climb_m.value, 0)} m (${D.dateLabel(rec.climb_m.date)})`].filter(Boolean);
    if (bits.length) left.appendChild(D.el("p", { class: "collapsed-note" }, "Records: " + bits.join(" · ") + "."));
    if (B.distances.some((d) => B.progression[d].length > 1)) D.chart(right, (ctx) => {
      const c = ctx.c, cols = [c.s[0], c.s[1], c.s[2], c.s[3]];
      const speed = (d, t) => 3600 * d / t;
      return D.base(ctx, {
        _aria: "How the best time at each distance improved, as average speed",
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 1) + " km/h") }),
        xAxis: D.timeAxis(ctx, { min: "dataMin" }), yAxis: D.valueAxis(ctx, "best average speed, km/h", { min: 0, scale: false }),
        series: B.distances.filter((d) => B.progression[d].length).map((d, i) => {
          const pts = B.progression[d].map(([date, t]) => [date, speed(d, t)]);
          pts.push([D.meta.today, pts[pts.length - 1][1]]);
          return D.line(d + " km", pts, cols[i], { step: "end", showSymbol: true, symbolSize: 7 });
        }),
      });
    });
  }

  // ---------------------------------------------------------------- 2f personal segments
  {
    const S = C.segments;
    const mmss = (t) => { if (t === null || t === undefined) return "–"; const m = Math.floor(t / 60), sec = Math.round(t - 60 * m); return sec === 60 ? `${m + 1}:00` : `${m}:${String(sec).padStart(2, "0")}`; };
    const p = D.panel(hRoutes, { flat: true, id: "segments", title: "Your segments",
      caption: "The stretches of your commute you picked: is your latest time close to your best, and what is typical?",
      method: "An effort = a pass within 30 m of the start followed by a pass within 30 m of the end, in that direction only, with no recording gap over 10 s and a ridden distance within 15 % of the segment's median (a wrong turn is dropped). " +
        "Times are interpolated at the closest points. Traffic lights and wind move times by a minute or more, so compare the dots over several weeks, not single rides. Heart rate is shown for chest-strap rides only. Segments are set in config.yaml.",
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "seg", label: "Segment" }, { key: "t", label: "Time", num: true }, { key: "kmh", label: "km/h", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "hr", label: "Avg HR", num: true }, { key: "w", label: "Avg W", num: true }],
        rows: S.flatMap((g) => g.efforts.filter((e) => D.inRange(e.date)).map((e) => ({ date: e.date, seg: g.name, t: mmss(e.time_s), kmh: e.kmh, hr: e.avg_hr, w: e.power }))).sort((a, b) => (a.date < b.date ? 1 : -1)) }) });
    if (!S.length || !S.some((g) => g.n)) D.placeholder(p.body, "no segments configured, or no ride has passed them yet");
    else {
      const row = D.el("div", { class: "split t3" }); p.body.appendChild(row);
      S.filter((g) => g.n).forEach((g) => {
        const box = D.el("div"); row.appendChild(box);
        const gap = g.latest.time_s - g.pr.time_s;
        box.appendChild(D.el("h3", { class: "sub" }, `${g.name} · ${D.fmt(g.km, 1)} km${g.gain_m !== null ? `, ${Math.round(g.gain_m) > 0 ? "+" : ""}${Math.round(g.gain_m) || 0} m` : ""}`));
        box.appendChild(D.el("p", { class: "collapsed-note" },
          `Best ${mmss(g.pr.time_s)} (${D.dateLabel(g.pr.date)}) · latest ${mmss(g.latest.time_s)} (${D.dateLabel(g.latest.date)}, ${gap <= 1 ? "a new best" : mmss(gap) + " slower than best"}) · typical ${mmss(g.median_s)} · ${g.n} rides`));
        D.chart(box, (ctx) => {
          const c = ctx.c;
          return D.base(ctx, {
            _aria: `Time per effort on ${g.name}`, legend: { show: false }, grid: { left: 8, right: 16, top: 26, bottom: 8, containLabel: true },
            tooltip: { trigger: "item", backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
              formatter: (q) => `<b>${D.dateLabel(q.value[0])}</b><br>${mmss(q.value[1] * 60)} · ${D.fmt(q.data.kmh, 1)} km/h${q.data.hr ? ` · ${q.data.hr} bpm` : ""}` },
            xAxis: D.timeAxis(ctx, { min: g.efforts[0].date }), yAxis: D.valueAxis(ctx, "minutes", { min: 0, scale: false }),
            series: [D.dots("Effort", g.efforts.map((e) => ({ value: [e.date, e.time_s / 60], kmh: e.kmh, hr: e.avg_hr })), c.s[0], ctx, { symbolSize: 9,
              markLine: { silent: true, symbol: "none", lineStyle: { color: c.ink2, width: 1, type: "solid" }, label: { formatter: "best", color: c.ink2, position: "insideEndTop" }, data: [{ yAxis: g.pr.time_s / 60 }] } })],
          });
        }, "short");
      });
    }
  }

  // ---------------------------------------------------------------- 3 intensity distribution
  {
    const I = C.intensity;
    const z = I.bounds;
    const split = I.last28 && I.last28[0] !== null ? I.last28 : null;
    const p = D.panel(hMix, { flat: true, id: "intensity", title: "Intensity distribution (grey-zone check)",
      caption: `How much riding time falls in the middle "grey zone" (${z[0]}–${z[1]} bpm, about Garmin Z3–Z4) instead of easy or hard.`,
      method: `Share of riding time per HR band, ${D.esc(I.source)} (${I.n_rides} rides): easy < ${z[0]} bpm, grey zone ${z[0]}–${z[1]} bpm (tempo to sub-threshold, roughly Garmin Z3–Z4), hard ≥ ${z[1]} bpm. This is the 3-band model of polarized training, not your watch's 5 zones. ` +
        `Bounds from config (default 80 % / 88 % of HRmax). Marker ticks on the bar = polarized reference ≈ ${I.reference.join(" / ")}.`,
      table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "h", label: "Hours", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "z1", label: "Easy %", num: true }, { key: "z2", label: "Grey %", num: true }, { key: "z3", label: "Hard %", num: true }],
        rows: weekTable(I.weeks, [["h", I.hours], ["z1", I.pct.z1], ["z2", I.pct.z2], ["z3", I.pct.z3]]).filter((r) => r.h) }) });
    // summary bar: last 28 days (or all-time when no strap ride in 28 days)
    const sumWrap = D.el("div");
    p.body.appendChild(sumWrap);
    D.chart(sumWrap, (ctx) => {
      const c = ctx.c, s = split || I.all_time, lbl = split ? `Last 28 days (${D.fmt(I.last28_hours, 1)} h)` : "All strap rides (none in the last 28 days)";
      const zc = [c.s[0], c.s[1], c.s[2]];
      let acc = 0;
      const refMarks = [I.reference[0], I.reference[0] + I.reference[1]].map((x) => ({ xAxis: x }));
      return {
        _aria: "Zone split " + lbl, animation: false, grid: { left: 8, right: 16, top: 26, bottom: 4, containLabel: true },
        title: { text: lbl, left: 0, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: c.ink2 } },
        tooltip: { trigger: "item", backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 }, formatter: (q) => `${q.seriesName}: <b>${D.fmt(q.value, 0)} %</b>` },
        xAxis: { type: "value", min: 0, max: 100, show: false }, yAxis: { type: "category", data: [""], show: false },
        series: ["Easy", "Grey zone", "Hard"].map((n, i) => {
          const v = s[i] || 0; acc += v;
          return { type: "bar", name: n, stack: "z", data: [v], barWidth: 22, itemStyle: { color: zc[i], borderColor: c.surface, borderWidth: 2, borderRadius: i === 0 ? [4, 0, 0, 4] : i === 2 ? [0, 4, 4, 0] : 0 },
            label: { show: v >= 9, position: "inside", color: i === 1 ? c.ink : "#fff", fontSize: 11, formatter: `${n.split(" ")[0]} ${Math.round(v)}%` },
            markLine: i === 0 ? { silent: true, symbol: "none", lineStyle: { color: c.ink, width: 1.5, type: "solid" }, label: { show: false }, data: refMarks } : undefined };
        }),
      };
    }, "short").el.style.height = "64px";
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const zc = [c.s[0], c.s[1], c.s[2]];
      return D.base(ctx, {
        _aria: "Weekly share of time in each heart-rate zone",
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 0) + " %") }),
        xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "% of time", { min: 0, max: 100, scale: false }),
        series: ["z1", "z2", "z3"].map((k, i) => D.bar(["Easy", "Grey zone", "Hard"][i], weekly(I.weeks, I.pct[k]).filter((x) => x[1] !== null), zc[i],
          { stack: "z", barMaxWidth: 18, itemStyle: { color: zc[i], borderRadius: 0, borderColor: c.surface, borderWidth: 1 } })),
      });
    });
  }

  // ---------------------------------------------------------------- 4 aerobic efficiency
  {
    const E = C.efficiency;
    const p = D.panel(hMix, { flat: true, id: "efficiency", title: "Aerobic efficiency",
      caption: "Metres per heartbeat on steady rides; a rising line means the same effort takes you further. Read the trend, not single rides." +
        (E.power.length ? " Below: power per bpm." : ""),
      method: `Steady outdoor rides (≥ 40 min moving, < 15 % stopped; ${E.n} rides). Line = 6-week rolling median. Speed depends on route, wind and bike, which is why single rides scatter.`,
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "name", label: "Ride", wrap: true }, { key: "v", label: "m/beat", num: true, fmt: (v) => D.fmt(v, 2) }, { key: "hr", label: "HR source" }],
        rows: E.speed.filter((r) => D.inRange(r.date)).reverse() }) });
    if (E.n < 3) D.placeholder(p.body, `only ${E.n} steady rides`);
    else D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Metres per heartbeat on steady rides",
      tooltip: { trigger: "item", backgroundColor: ctx.c.surface, borderColor: ctx.c.axis, textStyle: { color: ctx.c.ink, fontSize: 12 },
        formatter: (q) => q.data.name ? `<b>${D.dateLabel(q.value[0])}</b><br>${D.esc(q.data.name)}<br>${D.fmt(q.value[1], 2)} m/beat (${q.data.hr} HR)` : `${D.dateLabel(q.value[0])}: median ${D.fmt(q.value[1], 2)}` },
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "m per beat"),
      series: [D.dots("Ride", E.speed.map((r) => ({ value: [r.date, r.v], name: r.name, hr: r.hr })), ctx.c.s[0], ctx),
        D.line("6-week median", E.speed_median, ctx.c.s[0])],
    }), E.power.length ? "short" : "");
    if (E.power.length) D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Power per heartbeat", legend: { show: false }, grid: { left: 8, right: 16, top: 26, bottom: 8, containLabel: true },
      tooltip: { trigger: "item", backgroundColor: ctx.c.surface, borderColor: ctx.c.axis, textStyle: { color: ctx.c.ink, fontSize: 12 }, formatter: (q) => `${D.dateLabel(q.value[0])}: ${D.fmt(q.value[1], 2)} W/bpm` },
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "W per bpm"),
      series: [D.dots("Power / HR", E.power.map((r) => [r.date, r.v]), ctx.c.s[1], ctx)],
    }), "short");
  }

  // ---------------------------------------------------------------- 5 long rides
  {
    const L = C.long_rides;
    const p = D.panel(hMix, { flat: true, id: "long", title: "Long-ride durability",
      caption: "How much your efficiency drops in the second half of long rides; below ~5 % means you hold up well.",
      method: `Table: rides with ≥ ${L.min_minutes} min moving. Chart: HR decoupling on rides ≥ 90 min, the drop in output per heartbeat from the first to the second half. ` +
        "Power-based points are solid; speed-based points (no power meter) are faint because hills, stops and wind distort them." });
    if (!L.decoupling.length) D.placeholder(p.body, "no rides of 90 min or more");
    else D.chart(p.body, (ctx) => {
      const c = ctx.c;
      return D.base(ctx, {
        _aria: "Decoupling per long ride",
        tooltip: { trigger: "item", backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
          formatter: (q) => `<b>${D.dateLabel(q.value[0])}</b> ${D.esc(q.data.name)}<br>${q.data.min} min · decoupling ${D.fmt(q.value[1], 1)} % (${q.data.method})` },
        xAxis: D.timeAxis(ctx, { min: L.decoupling[0].date < ctx.start ? ctx.start : undefined }), yAxis: D.valueAxis(ctx, "% decoupling", { scale: false }),
        series: [
          D.dots("Power-based", L.decoupling.filter((r) => r.method === "power").map((r) => ({ value: [r.date, r.v], name: r.name, min: r.min, method: r.method })), c.s[0], ctx, { symbolSize: 10,
            markLine: { silent: true, symbol: "none", lineStyle: { color: c.ink2, width: 1, type: "solid" }, label: { formatter: "5 %", color: c.ink2, position: "insideEndTop" }, data: [{ yAxis: 5 }] } }),
          D.dots("Speed-based", L.decoupling.filter((r) => r.method === "speed").map((r) => ({ value: [r.date, r.v], name: r.name, min: r.min, method: r.method })), c.s[0], ctx, { symbolSize: 10, itemStyle: { color: c.s[0], opacity: 0.35, borderColor: c.surface, borderWidth: 2 } }),
        ],
      });
    }, "short");
    const tbl = D.el("div", { class: "table-wrap" });
    p.body.appendChild(tbl);
    const LONG_COLS = {
      cols: [{ key: "date", label: "Date" }, { key: "name", label: "Ride" }, { key: "min", label: "Min", num: true }, { key: "km", label: "km", num: true, fmt: (v) => D.fmt(v, 1) },
        { key: "elev", label: "Elev m", num: true }, { key: "avg_hr", label: "Avg HR", num: true }, { key: "decoupling", label: "Decoupl. %", num: true, fmt: (v, r) => v === null ? "–" : D.fmt(v, 1) + (r.dec_method === "speed" ? " (speed)" : "") },
        { key: "np", label: "NP W", num: true }, { key: "cadence", label: "Cadence", num: true }, { key: "hr_quality", label: "HR" }],
    };
    const longRows = L.table.map((r) => Object.assign({ _dim: !D.inRange(r.date) }, r));
    const SHOWN = 8;
    let rest = null;
    if (longRows.length > SHOWN) {
      rest = D.el("details", { class: "more-rows" }, `<summary>All ${longRows.length} long rides</summary>`);
      const restTbl = D.el("div", { class: "table-wrap" }); rest.appendChild(restTbl); p.body.appendChild(rest);
      D.onRefresh(() => D.table(restTbl, Object.assign({ rows: longRows }, LONG_COLS)));
    }
    // the column is one third wide: the last rides show the four columns that matter, the disclosure has all ten
    const COMPACT = { cols: [LONG_COLS.cols[0], LONG_COLS.cols[1], LONG_COLS.cols[2], LONG_COLS.cols[6]] };
    D.onRefresh(() => D.table(tbl, Object.assign({ rows: longRows.slice(0, SHOWN) }, COMPACT)));
  }

  // ---------------------------------------------------------------- 6 commute sprints
  {
    const S = C.sprints;
    const p = D.panel(grid, { id: "sprints", title: "Commute sprint tracker",
      caption: "Short all-out efforts per week on your commutes; counts are approximate.",
      method: `Detected by ${D.esc(S.method)}: ${D.esc(S.rule)}. ` +
        `The watch records about every ${D.fmt(S.sample_interval_s, 0)} s on commutes; HR is too slow for 10–20 s efforts and is not used.`,
      table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "n", label: "Commutes", num: true }, { key: "s", label: "Sprints", num: true }],
        rows: weekTable(S.weeks, [["n", S.commutes], ["s", S.count]]).filter((r) => r.n) }) });
    if (!S.enabled) D.placeholder(p.body, "no commute rides detected");
    else D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Sprints per week on commutes", legend: { show: false },
      tooltip: Object.assign(D.base(ctx).tooltip, { formatter: (ps) => { const i = S.weeks.indexOf(ps[0].value[0]); return `Week of ${D.dateLabel(ps[0].value[0])}<br>${ps[0].value[1]} sprints in ${S.commutes[i]} commutes`; } }),
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "sprints", { min: 0, scale: false, minInterval: 1 }),
      series: [D.bar("Sprints", weekly(S.weeks, S.count).filter((x, i) => S.commutes[i] > 0), ctx.c.s[0], { barMaxWidth: 16 })],
    }), "short");
  }

  // ---------------------------------------------------------------- 7 power
  {
    const P = C.power;
    const p = D.panel(grid, { id: "power", title: "Power", full: true,
      caption: P.enabled
        ? `eFTP ${P.eftp_lower_bound ? "at least " : ""}${D.fmt(P.eftp_now, 0)} W (${D.fmt(P.wkg_now, 2)} W/kg), goal ${P.ftp_goal} W` +
          (P.eftp_lower_bound ? ", a lower bound until a ride contains a maximal 20-min effort." : ".")
        : "",
      method: P.enabled ? `${P.n_rides} rides with a power meter or smart trainer. eFTP = 95 % of the best 20-min power (${D.esc(P.eftp_basis)}), W/kg at ${D.fmt(P.weight_kg, 1)} kg.` : "" });
    if (!P.enabled) {
      p.body.appendChild(D.el("p", { class: "collapsed-note" }, `No data: power panels switch on with ≥ 5 power rides (now ${P.n_rides}).`));
    } else {
      const row = D.el("div", { class: "split t3" }); p.body.appendChild(row);
      const curveBox = D.el("div"), eftpBox = D.el("div");
      row.appendChild(curveBox); row.appendChild(eftpBox);
      const lbl = (s) => (s < 60 ? Math.round(s) + " s" : s < 3600 ? D.fmt(s / 60, s % 60 ? 1 : 0) + " min" : D.fmt(s / 3600, 0) + " h");
      D.chart(curveBox, (ctx) => {
        const c = ctx.c;
        const mk = (name, data, color, w) => D.line(name, data, color, { showSymbol: true, symbolSize: 6, lineStyle: { width: w, color } });
        return D.base(ctx, {
          _aria: "Mean-maximal power curve",
          title: { text: "Mean-max power curve", left: 0, top: 18, textStyle: { fontSize: 12, fontWeight: 600, color: c.ink2 } },
          grid: { left: 8, right: 16, top: 64, bottom: 8, containLabel: true },
          tooltip: Object.assign(D.base(ctx).tooltip, { formatter: (ps) => `<b>${lbl(ps[0].value[0])}</b>` + ps.map((q) => `<br>${q.marker}${q.seriesName}: <b>${q.value[1]} W</b>`).join("") }),
          xAxis: { type: "log", logBase: 10, min: 5, max: 3600, splitNumber: 4, axisLine: { lineStyle: { color: c.axis } }, axisTick: { show: false }, splitLine: { show: false },
            axisLabel: { color: c.muted, fontSize: 11, formatter: (v) => lbl(Math.round(v)) } },
          yAxis: D.valueAxis(ctx, "W", { min: 0, scale: false }),
          series: [mk("All time", P.curves.all, c.neutral, 2), mk("Last 90 days", P.curves["90d"], c.s[0], 2), mk("Last 42 days", P.curves["42d"], c.s[1], 2)],
        });
      });
      D.chart(eftpBox, (ctx) => {
        const c = ctx.c;
        return D.base(ctx, {
          _aria: "Estimated FTP over time",
          title: { text: "eFTP (lower bound) vs goal", left: 0, top: 18, textStyle: { fontSize: 12, fontWeight: 600, color: c.ink2 } },
          grid: { left: 8, right: 16, top: 64, bottom: 8, containLabel: true }, legend: { show: false },
          xAxis: D.timeAxis(ctx, { min: P.eftp.length && P.eftp[0][0] > ctx.start ? D.addDays(P.eftp[0][0], -14) : ctx.start }),
          yAxis: D.valueAxis(ctx, "W", { min: 0, max: Math.max(P.ftp_goal + 20, ...P.eftp.map((x) => x[1] + 20)), scale: false }),
          series: [D.line("eFTP", P.eftp, c.s[0], { step: "end", showSymbol: true,
            markLine: { silent: true, symbol: "none", lineStyle: { color: c.ink2, width: 1, type: "solid" },
              label: { formatter: `goal ${P.ftp_goal} W`, color: c.ink2, position: "insideEndTop" }, data: [{ yAxis: P.ftp_goal }] } })],
        });
      });
      const t = D.el("div", { class: "table-wrap compact" });
      row.appendChild(t);
      D.table(t, { cols: [{ key: "label", label: "Best effort" }, { key: "w", label: "W", num: true }, { key: "wkg", label: "W/kg", num: true, fmt: (v) => D.fmt(v, 2) }, { key: "date", label: "Date" }], rows: P.best });
    }
  }

  // ---------------------------------------------------------------- 8 consistency calendar
  {
    const K = C.calendar;
    const p = D.panel(grid, { id: "calendar", title: "Consistency calendar", full: true,
      caption: "One cell per day, darker = more training load; always the last 12 months, whatever the date range.",
      method: "E-bike rides are excluded. Empty cells = no ride." });
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const data = K.load.filter((x) => x[1] > 0);
      const max = Math.max(1, ...data.map((x) => x[1]));
      return {
        _aria: "Daily training load calendar", animation: false,
        tooltip: { backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 }, formatter: (q) => `${D.dateLabel(q.value[0])}: load ${D.fmt(q.value[1], 0)}` },
        visualMap: { show: true, min: 0, max, orient: "horizontal", left: 0, bottom: 0, itemWidth: 10, itemHeight: 120, text: ["high load", "low"],
          textStyle: { color: c.muted, fontSize: 11 }, inRange: { color: [c.seq[1], c.seq[3], c.seq[5], c.seq[6]] }, calculable: false },
        calendar: { range: [K.start, K.end], top: 22, left: 30, right: 10, cellSize: ["auto", 14],
          itemStyle: { color: c.surface2, borderColor: c.surface, borderWidth: 2 }, splitLine: { show: false },
          yearLabel: { show: false }, dayLabel: { color: c.muted, fontSize: 11, firstDay: 1, nameMap: ["S", "M", "T", "W", "T", "F", "S"] },
          monthLabel: { color: c.muted, fontSize: 11 } },
        series: [{ type: "heatmap", coordinateSystem: "calendar", data }],
      };
    }).el.style.height = "200px";
  }

  // ---------------------------------------------------------------- 9 year over year
  {
    const Y = C.yoy;
    let metric = "hours";
    const years = Object.keys(Y).sort();
    const p = D.panel(grid, { id: "yoy", title: "Year over year",
      caption: "Cumulative riding by day of year, one line per year (e-bike excluded). Last four years; earlier rides come from the Strava archive." });
    const seg = D.el("div", { class: "seg", role: "group", "aria-label": "Unit" });
    ["hours", "km"].forEach((m) => {
      const b = D.el("button", { type: "button", "aria-pressed": String(m === metric) }, m);
      b.addEventListener("click", () => { metric = m; seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); D.refresh(); });
      seg.appendChild(b);
    });
    p.tools.prepend(seg);
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const colors = years.map((y, i) => (i === years.length - 1 ? c.s[0] : i === years.length - 2 ? c.neutral : c.s[i + 2]));
      return D.base(ctx, {
        _aria: "Cumulative riding by day of year", grid: { left: 8, right: 48, top: 52, bottom: 8, containLabel: true },  // room for the year end labels
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: (ps) => `Day ${ps[0].value[0]}` + ps.map((q) => `<br>${q.marker}${q.seriesName}: <b>${D.fmt(q.value[1], 0)} ${metric === "hours" ? "h" : "km"}</b>`).join("") }),
        xAxis: { type: "value", min: 1, max: 366, interval: 30.5, axisLine: { lineStyle: { color: c.axis } }, axisTick: { show: false }, splitLine: { show: false },
          axisLabel: { color: c.muted, fontSize: 11, formatter: (v) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Math.round((v - 1) / 30.5)] || "" } },
        yAxis: D.valueAxis(ctx, metric === "hours" ? "h" : "km", { min: 0, scale: false }),
        series: years.map((y, i) => D.line(y, Y[y][metric], colors[i], { endLabel: { show: true, formatter: y, color: c.ink2, fontSize: 11 } })),
      });
    });
  }

  D.arrange(root, [
    { id: "g-form", title: "Form", intro: "Is fitness coming back, and is this week's riding ordinary for you?", ids: ["fitness", "h-load", "calendar"] },
    { id: "g-train", title: "How you train", intro: "Is the mix of easy and hard right, how efficient are you, and do you hold up on long rides?", ids: ["h-mix"] },
    { id: "g-faster", title: "Getting faster", intro: "Your fastest efforts and your power.", ids: ["efforts", "power"] },
    { id: "g-routes", title: "Same routes", intro: "Your commute and your segments: the rides you repeat are the cleanest fitness signal.", ids: ["h-routes"] },
  ], ["yoy", "sprints"], { sticky: true });
})();
