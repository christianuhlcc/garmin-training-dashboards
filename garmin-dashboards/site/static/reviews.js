/* Weekly reviews: the AI write-up of each completed week. Kept off the Overview because it is always a week behind
   the live state there. */
(function () {
  "use strict";
  const D = window.Dash;
  if (!D.init("reviews")) return;
  const X = D.data, root = document.getElementById("content");
  const reviews = X.reviews || [];
  const sec = D.el("section", { class: "panel review", "aria-labelledby": "review-h" });
  root.appendChild(sec);
  if (!reviews.length) {
    sec.innerHTML = '<h2 id="review-h">Weekly review</h2><p class="placeholder">No review yet. The daily job writes one for each completed week (weekly_review.py).</p>';
  } else {
    const head = D.el("div", { class: "panel-head" });
    head.appendChild(D.el("h2", { id: "review-h" }, "Weekly review"));
    const sel = D.el("select", { class: "week-select", "aria-label": "Week to show" });
    reviews.forEach((r, i) => sel.appendChild(D.el("option", { value: String(i) }, `${D.esc(r.week.replace(/^\d{4}-/, ""))} · ${D.esc(D.rangeLabel(r.start, r.end))}`)));
    head.appendChild(sel);
    sec.appendChild(head);
    const body = D.el("div");
    sec.appendChild(body);
    const md = (t) => D.esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>");
    const RATING = { good: ["✓", "in_range"], ok: ["", "ok"], watch: ["!", "watch"], concern: ["▲", "out_of_range"] };
    const AREA = { recovery: "Recovery", strength: "Strength", cycling: "Cycling", consistency: "Consistency" };
    const rchip = (x) => { const [ico, cls] = RATING[x.rating] || ["", "no_data"];
      return `<li><span class="chip ${cls}">${ico ? `<span class="ico" aria-hidden="true">${ico}</span>` : ""}${D.esc(AREA[x.area] || x.area)}: ${D.esc(x.rating)}</span>` +
        `<span class="rating-why">${md(x.reason || "")}</span></li>`; };
    const draw = () => {
      const r = reviews[Number(sel.value)], v = r.review;
      const list = (title, items) => items.length ? `<h3>${title}</h3><ul>${items.map((x) => `<li>${md(x)}</li>`).join("")}</ul>` : "";
      body.innerHTML =
        `<p class="caption">Calendar week ${D.esc(D.rangeLabel(r.start, r.end))}, written ${D.esc(D.dateLabel(r.generated_at.slice(0, 10)))}.</p>` +
        `<p class="review-headline">${md(v.headline)}</p>` +
        `<ul class="ratings">${v.ratings.map(rchip).join("")}</ul>` +
        `<p class="prose">${md(v.summary)}</p>` +
        `<div class="grid review-lists"><div>${list("Highlights", v.highlights)}</div><div>${list("Concerns", v.concerns)}</div></div>` +
        (v.recommendations.length ? `<h3>Next week</h3><ol>${v.recommendations.map((x) => `<li><b>${md(x.action)}</b> <span class="why">${md(x.why)}</span></li>`).join("")}</ol>` : "") +
        `<p class="small review-note">Written by ${D.esc(r.model)} on ${D.esc(D.dateLabel(r.generated_at.slice(0, 10)))} from the weekly brief: numbers are computed locally and the model only interprets them. The brief (aggregated week data and strength log) is sent to Anthropic via the Claude Code CLI. Confidence: ${D.esc(v.confidence)}. Descriptive, not medical advice.</p>`;
    };
    sel.addEventListener("change", draw);
    draw();
  }
  root.appendChild(D.el("p", { class: "small footer-note" }, "The live state (last 7 days) is on the Overview; a review describes one finished calendar week, so the two can differ."));
})();
