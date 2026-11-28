/* Overview: status cards (src/metrics/attention.py) plus one chart: weekly training hours, cycling, e-bike and lifting stacked. */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("index")) return;
  const X = D.data, root = document.getElementById("content");
  if (X.meta.stale) root.appendChild(D.el("p", { class: "sentence stale" }, `The newest data is from ${D.esc(D.dateLabel(X.meta.data_through))}, more than 3 days old. Check logs/sync.log.`));

  // readiness for today (src/metrics/readiness.py): verdict + every factor with its rating, so the "why" is visible
  const RD = X.health && X.health.readiness;
  if (RD) {
    const TONE = { go: "in_range", easy: "watch", rest: "out_of_range", unknown: "ok" };
    const ST = { ok: ["in_range", "OK"], caution: ["watch", "Caution"], poor: ["out_of_range", "Poor"] };
    const sec = D.el("section", { class: "readiness " + (TONE[RD.verdict] || "ok"), "aria-labelledby": "rd-h" });
    const when = RD.is_today ? "Today" : `Based on the night to ${D.esc(D.dateLabel(RD.date))}`;
    const DN = RD.done;  // something is already logged today: say so instead of "good to train"
    sec.innerHTML = `<div class="rd-head"><h2 id="rd-h"><span class="rd-when">${when}</span> <span class="rd-verdict">${DN ? "Done" : D.esc(RD.label)}</span></h2>` +
      (DN ? `<p class="rd-text"><b>${D.esc(DN.summary)}</b> ${D.esc(DN.text)}</p>` : `<p class="rd-text">${D.esc(RD.text)}</p>`) + `</div>`;
    if (RD.trend_note) sec.appendChild(D.el("p", { class: "rd-trend" }, D.esc(RD.trend_note)));
    const PL = RD.plan;
    if (PL) {
      const PT = { go: "in_range", light: "watch", skip: "out_of_range", done: "ok" };
      const box = D.el("div", { class: "rd-plan " + (PT[PL.status] || "ok") });
      box.innerHTML = `<div class="rd-plan-head"><span class="rd-plan-label">Next up</span> <b>${D.esc(PL.title)}</b>` +
        `<span class="rd-plan-meta">${D.esc(PL.targets)}${PL.since ? " · " + D.esc(PL.since) : ""}</span></div>` +
        `<div class="rd-plan-muscles">${PL.muscles.map((m) => `<span class="rd-muscle">${D.esc(m)}</span>`).join("")}</div>` +
        `<p class="rd-plan-action"><b>${D.esc(PL.action)}</b>${PL.hint ? " " + D.esc(PL.hint) : ""}</p>`;
      sec.appendChild(box);
    }
    const ul = D.el("ul", { class: "rd-factors" });
    for (const f of RD.factors) {
      const [cls, lab] = ST[f.status] || ["ok", "No data"];
      // each row links to the Health panel that explains it (f.href, from build_metrics.READINESS_LINKS)
      const cells = `<span class="chip ${cls}">${lab}</span><span class="rd-name">${D.esc(f.label)}</span>` +
        `<span class="rd-val">${D.esc(f.value || "–")}</span><span class="rd-detail">${D.esc(f.detail || "")}</span>`;
      ul.appendChild(D.el("li", { class: "rd-factor" }, f.href
        ? `<a class="rd-row" href="${D.esc(f.href)}">${cells}<span class="rd-go" aria-hidden="true">→</span></a>`
        : `<div class="rd-row">${cells}</div>`));
    }
    sec.appendChild(ul);
    if (RD.need_why) sec.appendChild(D.el("p", { class: "small rd-note" }, `Last night's sleep need (Garmin): ${D.esc(RD.need_why)}. ` +
      `<a href="health.html#sleep-need">Sleep vs need →</a>`));
    sec.appendChild(D.el("p", { class: "small rd-note" }, "Rules, not a black box: HRV and resting HR vs your 60-day normal (1 / 2 SD), last night vs sleep need (90 / 75 %), " +
      "7-night sleep debt (3 / 6 h), 7- vs 28-day training load (1.3 / 1.5×). Grouped into body (HRV + resting HR), sleep (last night + debt) and load, so related signals count once. " +
      "Easy if one group is poor or two need caution; load alone never is. Rest on the strain pattern, HRV and resting HR both poor, or body and sleep both poor. " +
      "Next up = the split type after your last push / pull / shoulders session; a muscle counts as recovered 2+ days after its last primary work."));
    root.appendChild(sec);
  }

  const A = X.attention || { sentence: "", areas: [] };
  const sec = D.el("section", { class: "synthesis", "aria-labelledby": "syn-h" });
  sec.appendChild(D.el("h2", { id: "syn-h", class: "visually-hidden" }, "This week"));
  sec.appendChild(D.el("p", { class: "synthesis-lead" }, D.esc(A.sentence)));
  const ul = D.el("ul", { class: "area-list" });
  const CHIP = { in_range: ["in_range", "On track"], watch: ["watch", "Watch"], out_of_range: ["out_of_range", "Out of range"], note: ["ok", "Note"] };
  for (const a of A.areas) {
    const [cls, label] = CHIP[a.status] || CHIP.in_range;
    const li = D.el("li", { class: "area-row " + a.status });
    li.innerHTML = `<a href="${D.esc(a.href)}"><span class="area-name">${D.esc(a.title)}</span>` +
      `<span class="area-chip"><span class="chip ${cls}">${label}</span></span>` +
      `<span class="area-line">${D.esc(a.line)}</span><span class="area-facts">${D.esc(a.facts)}</span>` +
      `<span class="att-go" aria-hidden="true">→</span></a>`;
    ul.appendChild(li);
  }
  sec.appendChild(ul);
  sec.appendChild(D.el("p", { class: "small att-window" }, "Body compares your last 7 days with your usual 60-day range; strength counts this calendar week, cycling the last 7 days."));
  root.appendChild(sec);

  const W = X.weekly, B = X.budget, rows = W.weeks;
  const hoursOf = (w) => { const i = B.weeks.indexOf(w); return i < 0 ? [null, null, null] : [B.cycling[i], B.lifting[i], (B.ebike || [])[i]]; };
  const cur = rows.find((r) => r.current)?.week;
  const first = rows[0].week;
  const p = D.panel(root, { id: "weekly", title: "Weekly training hours",
    caption: "Hours per week, cycling, e-bike and lifting stacked. The current week is still in progress (lighter).",
    method: "Cycling: every non-e-bike ride (Garmin, plus Sigma/Strava rides with no Garmin copy). E-bike: rides recorded as e-bike or on the e-bike named in config.yaml. Lifting: all Garmin strength sessions.",
    table: () => ({ cols: [{ key: "w", label: "Week of" }, { key: "c", label: "Cycling h", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "e", label: "E-bike h", num: true, fmt: (v) => D.fmt(v, 1) }, { key: "l", label: "Lifting h", num: true, fmt: (v) => D.fmt(v, 1) }],
      rows: rows.slice().reverse().map((r) => ({ w: r.week + (r.current ? " (so far)" : ""), c: hoursOf(r.week)[0], e: hoursOf(r.week)[2], l: hoursOf(r.week)[1] })) }) });
  const chart = D.chart(p.body, (ctx) => {
    const c = ctx.c, dim = (w) => w === cur;
    const mk = (n, idx, col) => D.bar(n, rows.map((r) => { const v = hoursOf(r.week)[idx];
      return { value: [r.week, v || null], itemStyle: { color: col, borderRadius: 0, borderColor: c.surface, borderWidth: 1, opacity: dim(r.week) ? 0.45 : 1 } }; }), col, { stack: "h", barMaxWidth: 28 });
    return {
      _aria: "Weekly training hours, cycling, e-bike and lifting, last 12 weeks", legend: { show: false },
      grid: { left: 40, right: 16, top: 16, bottom: 26, containLabel: false },
      tooltip: Object.assign(D.base(ctx).tooltip, { formatter: (ps) => {
        const w = ps[0].value[0], r = rows.find((x) => x.week === w); if (!r) return "";
        const [ch, lh, eh] = hoursOf(w), f = (v) => (v === null || v === undefined ? "–" : D.fmt(v, 1) + " h");
        return `<b>Week of ${D.dateLabel(w)}${r.current ? " (so far)" : ""}</b><br>Cycling ${f(ch)}<br>E-bike ${f(eh)}<br>Lifting ${f(lh)}`; } }),
      xAxis: D.timeAxis(ctx, { min: D.weekAxisMin(first), axisLabel: { color: c.muted, fontSize: 11, hideOverlap: true }, axisLine: { lineStyle: { color: c.axis } } }),
      yAxis: D.valueAxis(ctx, "", { min: 0, splitNumber: 4 }),
      series: [mk("Cycling", 0, c.s[0]), mk("E-bike", 2, c.s[2]), mk("Lifting", 1, c.s[1])],
    };
  }, "strip");
  chart.el.style.height = "280px";
  p.body.insertBefore(D.el("p", { class: "legend-note" }, '<span class="key" style="background:var(--s1)"></span> Cycling hours &nbsp; <span class="key" style="background:var(--s3)"></span> E-bike hours &nbsp; <span class="key" style="background:var(--s2)"></span> Lifting hours'), p.body.firstChild);

  const R = X.last_review;
  if (R) root.appendChild(D.el("p", { class: "review-link" },
    `<a href="reviews.html">Last week's review: ${D.esc(R.week.replace(/^\d{4}-/, ""))} · ${D.esc(D.rangeLabel(R.start, R.end))} <span aria-hidden="true">→</span></a>`));
  root.appendChild(D.el("p", { class: "small footer-note" }, `Built ${D.esc(X.meta.generated_at)} from the local Garmin sync and the Apple Notes strength log.`));
})();
