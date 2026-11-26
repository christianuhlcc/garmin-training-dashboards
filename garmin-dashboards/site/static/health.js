/* Health: is my body recovering normally, and what moves my sleep and recovery?
   Every metric against its own rolling personal baseline; descriptive only, never diagnostic. */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("health")) return;
  const H = D.data;
  const root = document.getElementById("content");
  D.attention(root);
  const B = H.baseline_days, S = H.short_days;

  // ---------------------------------------------------------------- header: four core metrics, the rest in one line
  const CORE = ["hrv", "rhr", "sleep_h", "sleep_score"];
  const fmtM = (m, v) => (m.key === "sleep_h" ? D.hours(v) : D.fmt(v, m.digits));
  const flagged = (m) => m.status === "watch" || m.status === "out_of_range";
  const suffixOf = (m) => (flagged(m) && m.direction ? (m.direction === "low" ? "low" : "high") : null);
  const head = D.el("div", { class: "kpis" });
  for (const m of H.header.filter((x) => CORE.includes(x.key))) {
    const t = D.el("div", { class: "tile" });
    if (m.status === "no_data") { t.innerHTML = `<span class="label">${D.esc(m.label)}</span><span class="note">No data</span>`; head.appendChild(t); continue; }
    t.innerHTML = `<span class="label">${D.esc(m.label)}</span>` +
      `<span class="value">${fmtM(m, m.avg7)}<span class="unit">${m.key === "sleep_h" ? "" : D.esc(m.unit)}</span></span>` +
      `<span>${D.chip(m.status, suffixOf(m))}</span>` +
      `<span class="delta">7-day average · usual ${m.baseline === null ? "–" : fmtM(m, m.baseline)}${m.sd ? " ± " + (m.key === "sleep_h" ? D.fmt(m.sd * 60, 0) + "m" : D.fmt(m.sd, m.digits)) : ""}</span>` +
      `<span class="note">last night ${fmtM(m, m.value)}</span>` + D.spark(m.spark);
    t.title = `${m.label}, last value ${D.dateLabel(m.date)}`;
    head.appendChild(t);
  }
  root.appendChild(head);
  const rest = H.header.filter((x) => !CORE.includes(x.key) && x.status !== "no_data");
  const glance = D.el("p", { class: "health-glance" });
  glance.innerHTML = rest.map((m) => `<span class="glance-item">${D.chip(m.status, suffixOf(m))} ${D.esc(m.label)} <b>${fmtM(m, m.avg7)}${m.unit ? " " + D.esc(m.unit) : ""}</b>` +
    `<span class="small"> usual ${m.baseline === null ? "–" : fmtM(m, m.baseline)}</span></span>`).join("");
  root.appendChild(glance);
  root.appendChild(D.el("p", { class: "small head-note" }, D.esc(H.sentence) +
    `. Values are ${S}-day averages compared with the ${B} days before; a chip turns to Watch when that average sits more than 1 SD from your usual on the unfavourable side.`));

  const grid = D.el("div", { class: "grid" });
  root.appendChild(grid);
  const strainMarks = (ctx) => ({
    silent: true, itemStyle: { color: ctx.c.band },
    data: H.strain.days.filter(D.inRange).map((d) => [{ xAxis: d }, { xAxis: D.addDays(d, 1) }]),
  });

  function baselineChart(parent, key, P, color, unit, digits, extra = {}) {
    return D.chart(parent, (ctx) => {
      const c = ctx.c;
      const series = [
        ...D.band(P.lo, P.hi, c.band, ctx),
        D.line(`${B}-day baseline`, P.base, c.muted, { lineStyle: { width: 1, color: c.muted }, itemStyle: { color: c.muted } }),
        D.dots("Night value", P.value, color, ctx, { symbolSize: 6, itemStyle: { color, opacity: 0.55, borderColor: c.surface, borderWidth: 1 } }),
        D.line("7-day average", P.avg7, color, { markArea: strainMarks(ctx) }),
        // ring only the recent nights that drive the current status; older excursions are history, not news
        D.dots("Outside band, last 7 nights", P.outside.filter((x) => x[0] > D.addDays(D.meta.today, -S)), "transparent", ctx, { symbolSize: 11, itemStyle: { color: "transparent", borderColor: c.ink2, borderWidth: 1.5 } }),
      ];
      if (extra.garmin) {
        series.push(D.line("Garmin balanced range", P.garmin_lo, c.ink2, { lineStyle: { width: 1, type: "dashed", color: c.ink2 }, step: "end" }));
        series.push(D.line("_garmin_hi", P.garmin_hi, c.ink2, { lineStyle: { width: 1, type: "dashed", color: c.ink2 }, step: "end" }));
      }
      return D.base(ctx, {
        _aria: `${key} with ${B}-day personal baseline band`,
        legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 8, textStyle: { color: c.ink2, fontSize: 12 },
          data: ["Night value", "7-day average", `${B}-day baseline`, ...(extra.garmin ? ["Garmin balanced range"] : []), "Outside band, last 7 nights"] },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, digits) + " " + unit) }),
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, unit), series,
      });
    });
  }

  // ---------------------------------------------------------------- 1 HRV
  {
    const p = D.panel(grid, { id: "hrv", title: "Overnight HRV",
      caption: "Your nightly HRV against your usual range (gray band); higher is better. Ringed dots = recent nights outside the band.",
      method: `Dots = nightly average, line = 7-day average, gray band = ${B}-day baseline ± 1 SD, dashed = Garmin's own balanced range. Ringed: nights in the last 7 outside the band. Shaded days: the strain pattern (see Connections).`,
      table: () => ({ cols: [{ key: "d", label: "Night (wake date)" }, { key: "v", label: "HRV ms", num: true }],
        rows: D.filterPts(H.hrv.value).reverse().map((p) => ({ d: p[0], v: p[1] })) }) });
    if (!H.hrv.value.length) D.placeholder(p.body, "no overnight HRV recorded");
    else baselineChart(p.body, "HRV", H.hrv, D.colors().s[0], "ms", 0, { garmin: true });
  }
  // ---------------------------------------------------------------- 2 resting HR
  {
    const p = D.panel(grid, { id: "rhr", title: "Resting heart rate",
      caption: "Your resting heart rate against your usual range; lower is better.",
      method: `Same construction as HRV: dots = daily value, line = 7-day average, gray band = ${B}-day baseline ± 1 SD.`,
      table: () => ({ cols: [{ key: "d", label: "Date" }, { key: "v", label: "Resting HR bpm", num: true }],
        rows: D.filterPts(H.rhr.value).reverse().map((p) => ({ d: p[0], v: p[1] })) }) });
    baselineChart(p.body, "Resting HR", H.rhr, D.colors().s[1], "bpm", 0);
  }

  // ---------------------------------------------------------------- 3 sleep duration & stages + score
  {
    const p = D.panel(grid, { id: "sleep", title: "Sleep duration and stages", full: true,
      caption: "Sleep stages per night with the 7-day average of total sleep; sleep score below.",
      method: "Stacked bars per night, dated by the wake-up morning: deep, light, REM and awake, in hours.",
      table: () => {
        const m = (arr) => new Map(arr.map((x) => [x[0], x[1]]));
        const deep = m(H.sleep.deep), light = m(H.sleep.light), rem = m(H.sleep.rem), awake = m(H.sleep.awake), score = m(H.sleep.score);
        return { cols: [{ key: "d", label: "Night" }, { key: "t", label: "Total", num: true, fmt: D.hours }, { key: "deep", label: "Deep", num: true, fmt: D.hours },
          { key: "light", label: "Light", num: true, fmt: D.hours }, { key: "rem", label: "REM", num: true, fmt: D.hours }, { key: "awake", label: "Awake", num: true, fmt: D.hours }, { key: "s", label: "Score", num: true }],
          rows: D.filterPts(H.sleep.deep).reverse().map(([d]) => ({ d, deep: deep.get(d), light: light.get(d), rem: rem.get(d), awake: awake.get(d),
            t: (deep.get(d) || 0) + (light.get(d) || 0) + (rem.get(d) || 0), s: score.get(d) })) };
      } });
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const st = (name, data, color) => D.bar(name, D.filterPts(data), color, { stack: "s", barMaxWidth: 14,
        itemStyle: { color, borderRadius: 0, borderColor: c.surface, borderWidth: 0.5 } });
      return D.base(ctx, {
        _aria: "Sleep stages per night",
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.hours(v)) }),
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "hours", { min: 0, scale: false }),
        series: [st("Deep", H.sleep.deep, c.s[0]), st("Light", H.sleep.light, c.neutral), st("REM", H.sleep.rem, c.s[1]),
          st("Awake", H.sleep.awake, c.s[2]), D.line("7-day avg total", H.sleep.avg7, c.ink, { z: 5 })],
      });
    }, "tall");
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      return D.base(ctx, {
        _aria: "Sleep score", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "sleep score", { max: 100 }),
        series: [D.dots("Sleep score", D.filterPts(H.sleep.score), c.s[0], ctx, { symbolSize: 6, itemStyle: { color: c.s[0], opacity: 0.55, borderColor: c.surface, borderWidth: 1 } }),
          D.line("7-day average", H.sleep.score_avg7, c.s[0])],
      });
    }, "short");
  }

  // ---------------------------------------------------------------- 3b sleep vs need, sleep debt
  if (H.sleep_need) {
    const N = H.sleep_need, L = N.latest;
    const p = D.panel(grid, { id: "sleep-need", title: "Sleep vs need", full: true,
      caption: L ? `Last night ${D.hours(L.sleep_h)}. ${L.why ? D.esc(L.why) : "Need " + D.hours(L.need_h)}. ${N.debt_nights}-night sleep debt <b>${L.debt_h >= 1 / 60 ? D.hours(L.debt_h) : "none"}</b>.`
        : "Sleep per night against Garmin's sleep need.",
      method: `Sleep need is Garmin's nightly estimate (usual need, adjusted for HRV, training load, recent sleep and naps). ` +
        `Sleep debt = the sum of (need − sleep) over the last ${N.debt_nights} nights, floored at zero, so a long night pays back a short one. ` +
        `Debt above ${N.debt_bounds[0]} h counts as caution and above ${N.debt_bounds[1]} h as poor in today's readiness on the Overview.`,
      table: () => {
        const need = new Map(N.need.map((x) => [x[0], x[1]])), debt = new Map(N.debt.map((x) => [x[0], x[1]]));
        return { cols: [{ key: "d", label: "Night" }, { key: "s", label: "Sleep", num: true, fmt: D.hours }, { key: "n", label: "Need", num: true, fmt: D.hours },
          { key: "g", label: "Gap", num: true, fmt: (v) => (v === null || v === undefined ? "–" : (v > 0 ? "−" : "+") + D.hours(Math.abs(v))) },
          { key: "debt", label: "7-night debt", num: true, fmt: D.hours }],
          rows: D.filterPts(N.sleep).reverse().map(([d, v]) => ({ d, s: v, n: need.get(d), g: need.get(d) == null || v == null ? null : need.get(d) - v, debt: debt.get(d) })) };
      } });
    D.chart(p.body, (ctx) => {
      const c = ctx.c, need = new Map(N.need.map((x) => [x[0], x[1]]));
      const bars = D.filterPts(N.sleep).map(([d, v]) => ({ value: [d, v],
        itemStyle: { color: need.get(d) != null && v < need.get(d) * 0.9 ? c.s[3] : c.s[0], borderRadius: [2, 2, 0, 0] } }));
      return D.base(ctx, {
        _aria: "Sleep per night against sleep need", legend: { show: false },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.hours(v)) }),
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "hours", { min: 0, scale: false }),
        series: [D.bar("Sleep", bars, c.s[0], { barMaxWidth: 14 }),
          D.line("Sleep need", D.filterPts(N.need), c.ink, { z: 5, step: "middle" }),
          D.line("Usual need", D.filterPts(N.need_base), c.muted, { z: 4, lineStyle: { type: "dashed", width: 1, color: c.muted } })],
      });
    }, "tall");
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      return D.base(ctx, {
        _aria: "Seven-night sleep debt", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
        xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, `${N.debt_nights}-night debt, h`, { min: 0, scale: false }),
        series: [D.line("Sleep debt", D.filterPts(N.debt), c.s[3], { areaStyle: { opacity: 0.15 },
          markLine: { silent: true, symbol: "none", label: { position: "insideStartTop", color: c.muted, fontSize: 11 },
            lineStyle: { color: c.axis, type: "dashed" },
            data: [{ yAxis: N.debt_bounds[0], label: { formatter: `caution ${N.debt_bounds[0]} h` } }, { yAxis: N.debt_bounds[1], label: { formatter: `poor ${N.debt_bounds[1]} h` } }] } })],
      });
    }, "short");
    p.body.insertBefore(D.el("p", { class: "legend-note" }, '<span class="key" style="background:var(--s1)"></span> Sleep &nbsp; ' +
      '<span class="key" style="background:var(--s4)"></span> Sleep under 90 % of need &nbsp; — Sleep need &nbsp; ┄ Usual need'), p.body.firstChild);
  }

  // ---------------------------------------------------------------- 3c training load (the readiness "Training load" factor)
  if (H.training_load) {
    const T = H.training_load, L = T.latest, [cau, poor] = T.bounds;
    const word = L.ratio === null ? "" : L.ratio >= poor ? " (poor)" : L.ratio >= cau ? " (caution)" : " (OK)";
    const top = T.activities.filter((a) => a.acute).sort((a, b) => (b.trimp || 0) - (a.trimp || 0)).slice(0, 3)
      .map((a) => `${D.esc(D.dateLabel(a.date))} ${D.esc(a.kind.toLowerCase())} ${D.fmt(a.trimp, 0)}`);
    const p = D.panel(grid, { id: "training-load", title: "Training load: last 7 vs 28 days", full: true,
      caption: L.ratio === null ? `Not enough history yet for a ratio (needs a ${T.chronic_days}-day average of at least ${T.min_chronic}).`
        : `Last ${T.acute_days} days: <b>${D.fmt(L.acute, 0)}</b> a day, <b>${D.fmt(L.ratio, 1)}×</b> your ${T.chronic_days}-day average of ${D.fmt(L.chronic, 0)}${word}. ` +
          `That is ${D.fmt(L.acute_sum, 0)} in ${T.acute_days} days against ${D.fmt(L.before_sum, 0)} in the ${T.chronic_days - T.acute_days} days before.` +
          (top.length ? ` Biggest: ${top.join(", ")}.` : ""),
      method: `Load = TRIMP per activity (Banister): minutes × heart-rate reserve × 0.64·e<sup>1.92 × HRR</sup>, from the HR stream when there is one, ` +
        `otherwise from average HR. Roughly: an easy hour ≈ 20, a hard hour ≈ 100. Every activity with heart rate counts, including e-bike rides and walks. ` +
        `Ratio = mean daily load of the last ${T.acute_days} days ÷ mean of the last ${T.chronic_days} days (both include today; rest days count as 0). ` +
        `Today's readiness on the Overview rates it caution from ${cau}× and poor from ${poor}×. It measures how sudden an increase is, not how hard ` +
        `the training is: after a few easy weeks even normal training reads high, and it settles once the ${T.chronic_days}-day average catches up.`,
      table: () => ({ cols: [{ key: "date", label: "Date" }, { key: "name", label: "Activity" }, { key: "kind", label: "Kind" },
        { key: "min", label: "Min", num: true }, { key: "avg_hr", label: "Avg HR", num: true }, { key: "trimp", label: "TRIMP", num: true },
        { key: "w", label: `In last ${T.acute_days} days` }],
        rows: T.activities.map((a) => ({ ...a, name: a.name || "–", w: a.acute ? "yes" : "" })) }) });
    const KC = (c) => ({ "Cycling": c.s[0], "E-bike": c.s[2], "Strength": c.s[1], "Other": c.muted });
    const axis = (ctx) => D.timeAxis(ctx, { min: T.days[0], max: T.days[T.days.length - 1] });
    D.chart(p.body, (ctx) => {
      const c = ctx.c, col = KC(c);
      return D.base(ctx, {
        _aria: `Daily training load by kind over the last ${T.days.length} days, with ${T.acute_days}- and ${T.chronic_days}-day averages`, legend: { show: false },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 0)) }),
        xAxis: axis(ctx), yAxis: D.valueAxis(ctx, "TRIMP per day", { min: 0, scale: false }),
        series: [...T.kinds.map((k) => D.bar(k, T.days.map((d, i) => [d, T.by_kind[k][i] || null]), col[k],
            { stack: "load", barMaxWidth: 10, itemStyle: { color: col[k], borderRadius: 0 } })),
          D.line(`${T.acute_days}-day average`, T.acute, c.ink, { z: 5 }),
          D.line(`${T.chronic_days}-day average`, T.chronic, c.muted, { z: 4, lineStyle: { type: "dashed", width: 2, color: c.muted } })],
      });
    }, "tall");
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      return D.base(ctx, {
        _aria: `Ratio of the ${T.acute_days}-day to the ${T.chronic_days}-day training load`, legend: { show: false },
        grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
        tooltip: Object.assign(D.base(ctx).tooltip, { formatter: D.tooltipFormatter((v) => D.fmt(v, 2) + "×") }),
        xAxis: axis(ctx), yAxis: D.valueAxis(ctx, `${T.acute_days}- / ${T.chronic_days}-day ratio`, { min: 0, scale: false }),
        series: [D.line("Ratio", T.ratio, c.s[3], { areaStyle: { opacity: 0.12 },
          markLine: { silent: true, symbol: "none", label: { position: "insideStartTop", color: c.muted, fontSize: 11 },
            lineStyle: { color: c.axis, type: "dashed" },
            data: [{ yAxis: cau, label: { formatter: `caution ${cau}×`, position: "insideStartBottom" } }, { yAxis: poor, label: { formatter: `poor ${poor}×` } }] } })],
      });
    }, "short");
    p.body.insertBefore(D.el("p", { class: "legend-note" }, T.kinds.map((k, i) =>
      `<span class="key" style="background:${["var(--s1)", "var(--s3)", "var(--s2)", "var(--muted)"][i]}"></span> ${k}`).join(" &nbsp; ") +
      ` &nbsp; — ${T.acute_days}-day average &nbsp; ┄ ${T.chronic_days}-day average`), p.body.firstChild);
  }

  // ---------------------------------------------------------------- 4 sleep regularity
  {
    const R = H.regularity;
    const p = D.panel(grid, { id: "regularity", title: "Sleep regularity", full: true,
      caption: `Weekdays ${D.clock(R.weekday.bed)} → ${D.clock(R.weekday.wake)} (${D.hours(R.weekday.sleep_h)}), weekends ${D.clock(R.weekend.bed)} → ${D.clock(R.weekend.wake)} (${D.hours(R.weekend.sleep_h)}); your sleep midpoint varies by <b>${R.sd14 === null ? "–" : R.sd14 + " min"}</b>.`,
      method: "Each bar runs from bedtime (top) to wake time (bottom). The variation is the standard deviation of the sleep midpoint over the last 14 nights; lower = more regular.",
      table: () => {
        const wake = new Map(R.wake.map((x) => [x[0], x[1]]));
        return { cols: [{ key: "d", label: "Wake date" }, { key: "b", label: "Bedtime", fmt: D.clock }, { key: "w", label: "Wake", fmt: D.clock }],
          rows: D.filterPts(R.bed).reverse().map(([d, b]) => ({ d, b, w: wake.get(d) })) };
      } });
    D.chart(p.body, (ctx) => {
      const c = ctx.c;
      const wake = new Map(R.wake.map((x) => [x[0], x[1]]));
      const data = D.filterPts(R.bed).filter((x) => wake.has(x[0])).map(([d, b]) => [d, b, wake.get(d)]);
      return D.base(ctx, {
        _aria: "Bed and wake times per night", legend: { show: false }, grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
        tooltip: { trigger: "item", backgroundColor: c.surface, borderColor: c.axis, textStyle: { color: c.ink, fontSize: 12 },
          formatter: (p) => `<b>${D.dateLabel(p.value[0])}</b><br>Bed ${D.clock(p.value[1])} → wake ${D.clock(p.value[2])}` },
        xAxis: D.timeAxis(ctx),
        yAxis: D.valueAxis(ctx, "", { inverse: true, scale: false, interval: 120,
          min: (v) => Math.floor((v.min - 30) / 120) * 120, max: (v) => Math.ceil((v.max + 30) / 120) * 120,
          axisLabel: { color: c.muted, fontSize: 11, formatter: D.clock } }),
        series: [{
          type: "custom", name: "Sleep window", data,
          renderItem: (params, api) => {
            const x = api.coord([api.value(0), api.value(1)]), y2 = api.coord([api.value(0), api.value(2)]);
            const w = Math.max(2, Math.min(8, (params.coordSys.width / Math.max(1, data.length)) * 0.6));
            return { type: "rect", shape: { x: x[0] - w / 2, y: x[1], width: w, height: y2[1] - x[1], r: w / 2 },
              style: { fill: c.s[0], opacity: 0.85 } };
          },
          encode: { x: 0, y: [1, 2] },
        }],
      });
    });
  }

  // ---------------------------------------------------------------- 5 stress & body battery
  {
    const p = D.panel(grid, { id: "stress", title: "Stress and Body Battery", full: true,
      caption: "Daily stress (top) and how far Body Battery recharged and drained each day (bottom).",
      method: "Stress = Garmin's daily average (0–100), line = 7-day average. Body Battery bars run from the day's lowest to highest value.",
      table: () => {
        const mx = new Map(H.stress.bb_max.map((x) => [x[0], x[1]])), mn = new Map(H.stress.bb_min.map((x) => [x[0], x[1]]));
        return { cols: [{ key: "d", label: "Date" }, { key: "s", label: "Stress avg", num: true }, { key: "mx", label: "BB max", num: true }, { key: "mn", label: "BB min", num: true }],
          rows: D.filterPts(H.stress.stress).reverse().map(([d, s]) => ({ d, s, mx: mx.get(d), mn: mn.get(d) })) };
      } });
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Daily average stress", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "stress", { min: 0 }),
      series: [D.bar("Stress avg", D.filterPts(H.stress.stress), ctx.c.neutral, { barMaxWidth: 8 }), D.line("7-day average", H.stress.stress_avg7, ctx.c.s[1])],
    }), "short");
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Body Battery daily range", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "Body Battery", { min: 0, max: 100, scale: false }),
      series: [...D.band(D.filterPts(H.stress.bb_min), D.filterPts(H.stress.bb_max), ctx.c.seq[2], ctx, "_bb"),
        D.line("Max (recharged to)", H.stress.bb_max, ctx.c.s[0], { lineStyle: { width: 1.5, color: ctx.c.s[0] } }),
        D.line("Min (drained to)", H.stress.bb_min, ctx.c.s[0], { lineStyle: { width: 1, color: ctx.c.s[0], opacity: 0.6 } })],
    }), "short");
  }

  // ---------------------------------------------------------------- 6 respiration & SpO2
  {
    const p = D.panel(grid, { id: "resp", title: "Respiration and SpO2 during sleep",
      caption: `Nightly averages with ${B}-day baseline band. SpO2 is a wrist estimate.`,
      table: () => {
        const sp = new Map(H.resp.spo2.value.map((x) => [x[0], x[1]]));
        return { cols: [{ key: "d", label: "Night" }, { key: "r", label: "Respiration br/min", num: true }, { key: "s", label: "SpO2 %", num: true }],
          rows: D.filterPts(H.resp.resp.value).reverse().map(([d, r]) => ({ d, r, s: sp.get(d) })) };
      } });
    if (!H.resp.has_resp && !H.resp.has_spo2) D.placeholder(p.body, "no respiration or SpO2 data");
    else {
      if (H.resp.has_resp) baselineChart(p.body, "Respiration", H.resp.resp, D.colors().s[2], "br/min", 1).el.classList.add("short");
      if (H.resp.has_spo2) baselineChart(p.body, "SpO2", H.resp.spo2, D.colors().s[0], "%", 0).el.classList.add("short");
      D.charts.slice(-2).forEach((ch) => ch.inst.resize());
    }
  }

  // ---------------------------------------------------------------- 7 activity baseline
  {
    const A = H.activity;
    const p = D.panel(grid, { id: "activity", title: "Daily activity",
      caption: `Steps per day (top) and intensity minutes per week against the ${A.im_reference}-minute reference (bottom).`,
      method: "Steps are averaged per day within each week. Intensity minutes = moderate + 2 × vigorous, as Garmin counts them. The current week is hatched.",
      table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "s", label: "Steps/day", num: true, fmt: (v) => D.fmt(v) }, { key: "i", label: "Intensity min", num: true }],
        rows: A.weeks.map((w, i) => ({ w, s: A.steps[i], i: A.im[i] })).filter((r) => r.w >= D.addDays(D.rangeStart(), -6)).reverse() }) });
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Average steps per day by week", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "steps/day", { min: 0, scale: false }),
      series: [D.bar("Steps per day", D.weekData(A.weeks, A.steps, A.current_week, ctx), ctx.c.s[0], { barMaxWidth: 16 })],
    }), "short");
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Weekly intensity minutes", legend: { show: false }, grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(ctx.start) }), yAxis: D.valueAxis(ctx, "min/week", { min: 0, scale: false }),
      series: [D.bar("Intensity minutes", D.weekData(A.weeks, A.im, A.current_week, ctx), ctx.c.s[2], { barMaxWidth: 16,
        markLine: { silent: true, symbol: "none", lineStyle: { color: ctx.c.ink2, width: 1, type: "solid" },
          label: { color: ctx.c.ink2, formatter: A.im_reference + " min/week", position: "insideStartTop" }, data: [{ yAxis: A.im_reference }] } })],
    }), "short");
  }

  // ---------------------------------------------------------------- 8 VO2max
  {
    const V = H.vo2max;
    const p = D.panel(grid, { id: "vo2", title: "VO2max estimate",
      caption: "Garmin's VO2max estimate, a device estimate rather than a lab value.",
      method: "Points on the days a new value was recorded. Generic = from walking/running; cycling = from rides with power.",
      table: () => ({ cols: [{ key: "d", label: "Date" }, { key: "k", label: "Kind" }, { key: "v", label: "VO2max", num: true }, { key: "s", label: "Sub-sport" }],
        rows: [...V.generic.map((x) => ({ d: x[0], k: "generic", v: x[1], s: x[2] })), ...V.cycling.map((x) => ({ d: x[0], k: "cycling", v: x[1], s: x[2] }))]
          .filter((r) => D.inRange(r.d)).sort((a, b) => b.d.localeCompare(a.d)) }) });
    D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "VO2max estimates", tooltip: Object.assign(D.base(ctx).tooltip, { trigger: "item", formatter: (q) => `<b>${D.dateLabel(q.value[0])}</b><br>${q.marker}${q.seriesName === "_generic_pts" ? "Generic" : q.seriesName}: <b>${D.fmt(q.value[1], 1)}</b>` }),
      xAxis: D.timeAxis(ctx), yAxis: D.valueAxis(ctx, "ml/kg/min"),
      series: [D.line("Generic", V.generic.map((x) => [x[0], x[1]]), ctx.c.s[0], { step: "end", showSymbol: true }),
        D.dots("Cycling", V.cycling.map((x) => [x[0], x[1]]), ctx.c.s[1], ctx, { symbolSize: 10 })],
    }), "short");
  }

  // ---------------------------------------------------------------- 9 weight
  {
    const W = H.weight;
    const p = D.panel(grid, { id: "weight", title: "Weight",
      caption: W.n < 8 ? `Only ${W.n} manual entries, so the trend line and weekly rate aren't meaningful yet. All entries are shown, whatever the date range.`
        : `Points = weigh-ins, line = exponential moving average (α = 0.1). Weekly rate: ${D.signed(W.rate_per_week, 2)} kg/week.`,
      table: () => ({ cols: [{ key: "d", label: "Date" }, { key: "v", label: "kg", num: true }], rows: W.points.slice().reverse().map(([d, v]) => ({ d, v })) }) });
    if (!W.n) D.placeholder(p.body, "no weight entries");
    else D.chart(p.body, (ctx) => D.base(ctx, {
      _aria: "Weight entries", legend: { show: false }, xAxis: D.timeAxis(ctx, { min: W.n < 8 ? D.meta.first_day : ctx.start }),
      yAxis: D.valueAxis(ctx, "kg"),
      series: [D.dots("Weigh-in", W.points, ctx.c.s[0], ctx, { symbolSize: 9 }), ...(W.n >= 8 ? [D.line("Trend (EMA)", W.ema, ctx.c.s[0])] : [])],
    }), "short");
  }

  // ---------------------------------------------------------------- 10 load vs recovery
  {
    const L = H.load_recovery;
    const G = L.bins.groups;
    const r = (c) => (c.r === null ? "n/a" : "r = " + D.fmt(c.r, 2));
    const late = L.late;
    const lateTxt = late.sleep_score.n_late >= L.min_n
      ? ` After sessions ending after 20:00 (${late.sleep_score.n_late} nights) the sleep score averaged ${D.fmt(late.sleep_score.late_mean, 1)} vs ${D.fmt(late.sleep_score.other_mean, 1)} on other nights, and bedtime was ${D.signed(late.bed_min.diff, 0)} min later.`
      : "";
    const range = (g) => (g.lo === null ? "load 0" : `load ${g.lo}–${g.hi}`);
    const p = D.panel(grid, { id: "lvr", title: "Training load vs recovery", full: true,
      caption: (L.bins.finding ? D.esc(L.bins.finding) + " " : "Not enough nights per group yet to compare. ") + "Correlation, not cause.",
      method: `Yesterday's total training load (TRIMP from all activities) against last night's HRV deviation from your baseline and sleep score. ` +
        `Training days are split into four equal-sized groups by load; groups with fewer than ${L.bins.min_n} nights are not compared. ` +
        `Across all days: HRV ${r(L.corr_hrv)} (n = ${L.corr_hrv.n}), sleep score ${r(L.corr_sleep)} (n = ${L.corr_sleep.n}).` + lateTxt,
      table: () => ({ cols: [{ key: "label", label: "Yesterday" }, { key: "rng", label: "Load" }, { key: "n", label: "Nights", num: true },
        { key: "hrv", label: "HRV vs baseline, ms", num: true, fmt: (v) => D.signed(v, 1) }, { key: "sleep", label: "Sleep score", num: true, fmt: (v) => D.fmt(v, 1) }],
        rows: G.map((g) => ({ ...g, rng: range(g) })) }) });
    const row = D.el("div", { class: "grid" }); p.body.appendChild(row);
    const cats = G.map((g) => g.label.replace(" quarter", ""));
    // fixed, honest scales (not zoomed): HRV ±5 ms around baseline, sleep score ±10 around rest days, widened if needed
    const vals = (k) => G.map((g) => g[k]).filter((v) => v !== null);
    const span = (k, center, half) => { const v = vals(k); const h = 5 * Math.ceil(Math.max(half, ...v.map((x) => Math.abs(x - center))) / 5); return { min: center - h, max: center + h, interval: 5 }; };
    const restSleep = 5 * Math.round((G[0].sleep ?? 80) / 5);
    const mini = (key, yName, color, fmt, ax) => D.chart(row, (ctx) => D.base(ctx, {
      _aria: `Next-night ${yName} after rest days and after training days by load quarter: ` + G.map((g) => `${g.label} ${g[key] === null ? "too few nights" : fmt(g[key])}`).join(", "),
      legend: { show: false }, grid: { left: 8, right: 16, top: 30, bottom: 8, containLabel: true },
      title: { text: yName, left: 0, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: ctx.c.ink2 } },
      tooltip: { trigger: "axis", backgroundColor: ctx.c.surface, borderColor: ctx.c.axis, textStyle: { color: ctx.c.ink, fontSize: 12 },
        formatter: (ps) => { const g = G[ps[0].dataIndex]; return `${D.esc(g.label)} (${range(g)}, ${g.n} nights): <b>${g[key] === null ? "too few nights" : fmt(g[key])}</b>`; } },
      xAxis: D.catAxis(ctx, cats, { boundaryGap: true }),
      yAxis: D.valueAxis(ctx, "", Object.assign({ scale: false, axisLabel: { color: ctx.c.muted, fontSize: 11, formatter: (v) => fmt(v) } }, ax)),
      series: [D.dots(yName, G.map((g, i) => ({ value: g[key], itemStyle: { color: i === 0 ? ctx.c.muted : color, borderColor: ctx.c.surface, borderWidth: 2 } })), color, ctx, { symbolSize: 11 })],
    }), "short");
    mini("hrv", "Next-night HRV vs baseline (ms)", D.colors().s[0], (v) => D.signed(v, 0), span("hrv", 0, 5));
    mini("sleep", "Next-night sleep score", D.colors().s[2], (v) => D.fmt(v, 0), span("sleep", restSleep, 10));
  }

  // ---------------------------------------------------------------- 11 weekday pattern
  {
    const Wd = H.weekday;
    const p = D.panel(grid, { id: "weekday", title: "Weekday pattern",
      caption: (() => {
        const span = (a, f) => { const v = a.filter((x) => x !== null); return v.length ? `${f(Math.min(...v))}–${f(Math.max(...v))}` : "–"; };
        return `Axes are zoomed: the whole week spans only stress ${span(Wd.stress, (v) => D.fmt(v, 0))}, sleep ${span(Wd.sleep_h, D.hours)} and HRV ${span(Wd.hrv, (v) => D.fmt(v, 0))} ms.`;
      })(),
      method: "Median per day of week over all data; sleep is the night ending that morning. Mon–Fri are work days; weekend dots are lighter.",
      table: () => ({ cols: [{ key: "d", label: "Day" }, { key: "s", label: "Stress", num: true }, { key: "h", label: "Sleep", num: true, fmt: D.hours }, { key: "v", label: "HRV ms", num: true }],
        rows: Wd.labels.map((d, i) => ({ d, s: Wd.stress[i], h: Wd.sleep_h[i], v: Wd.hrv[i] })) }) });
    const row = D.el("div", { class: "grid", style: "grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))" }); p.body.appendChild(row);
    // dot plot: the spread between weekdays is small, so bars from zero would hide it and a truncated bar would lie
    const mini = (title, vals, color, fmt, minInterval) => D.chart(row, (ctx) => D.base(ctx, {
      _aria: title + " by weekday", legend: { show: false }, grid: { left: 8, right: 8, top: 28, bottom: 8, containLabel: true },
      title: { text: title, left: 0, top: 0, textStyle: { fontSize: 12, fontWeight: 600, color: ctx.c.ink2 } },
      tooltip: { trigger: "axis", backgroundColor: ctx.c.surface, borderColor: ctx.c.axis, textStyle: { color: ctx.c.ink, fontSize: 12 },
        formatter: (ps) => `${ps[0].name}: <b>${fmt(ps[0].value)}</b>` },
      xAxis: D.catAxis(ctx, Wd.labels, { boundaryGap: true }),
      yAxis: D.valueAxis(ctx, "", { scale: true, minInterval, axisLabel: { color: ctx.c.muted, fontSize: 11, formatter: (v) => fmt(v) } }),
      series: [D.dots(title, vals.map((v, i) => ({ value: v, itemStyle: { color, opacity: i >= 5 ? 0.6 : 1, borderColor: ctx.c.surface, borderWidth: 2 } })), color, ctx, { symbolSize: 11 })],
    }), "short");
    mini("Stress", Wd.stress, D.colors().s[1], (v) => D.fmt(v, 0), 1);
    mini("Sleep", Wd.sleep_h, D.colors().s[0], (v) => D.hours(v), 0.1);
    mini("HRV", Wd.hrv, D.colors().s[2], (v) => D.fmt(v, 0) + " ms", 1);
  }

  // ---------------------------------------------------------------- 12 strain pattern
  {
    const p = D.panel(grid, { id: "strain", title: "Strain pattern",
      caption: "Runs of days where heart rate, HRV and breathing all looked strained at once. Not a diagnosis.",
      method: "Resting HR above its band, HRV below it and respiration above it on the same day, for 2+ consecutive days. This often shows up with illness, heavy fatigue, alcohol or travel. Flagged days are shaded on the HRV and resting-HR charts." });
    const draw = () => {
      p.body.innerHTML = "";
      const days = H.strain.days.filter(D.inRange);
      if (!days.length) { p.body.appendChild(D.el("p", { class: "collapsed-note" }, `No flagged days in the selected range (${H.strain.days.length} in all data).`)); return; }
      // group consecutive days into runs
      const runs = []; let cur = null;
      for (const d of days) { if (cur && D.addDays(cur[1], 1) === d) cur[1] = d; else { cur = [d, d]; runs.push(cur); } }
      const ul = D.el("ul");
      runs.reverse().forEach(([a, b]) => ul.appendChild(D.el("li", {}, `${D.dateLabel(a)}${a !== b ? " – " + D.dateLabel(b) : ""}`)));
      p.body.appendChild(ul);
    };
    D.onRefresh(draw);
  }
  D.arrange(root, [
    { id: "g-recovery", title: "Recovery", intro: "Each night against your own 60-day range.", ids: ["hrv", "rhr", "sleep", "sleep-need", "training-load", "regularity"] },
    { id: "g-connections", title: "Connections", intro: "What moves your recovery: training load, daily stress, strain and the day of the week.", ids: ["lvr", "stress", "strain", "weekday"] },
    { id: "g-longterm", title: "Long-term", ids: ["activity", "vo2", "weight", "resp"] },
  ]);
})();
