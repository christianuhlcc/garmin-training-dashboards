"""Overview "needs attention" list: a few calm, specific flags computed from the page summaries.

Each check either yields an item or counts as passed. An item has a kind ('body' for health metrics outside their
usual range, tone 'watch' or 'out_of_range'; 'rhythm' for training reminders, tone 'note', drawn untinted), a short
title, a detail line with the numbers and a link to the panel that explains it. Thresholds are deliberately
conservative: small dips are not flags (PRODUCT.md: calm by default, clear when it matters).

The Overview shows only `synthesis`: one sentence that connects body and training, plus one line per area. The
items themselves are listed on each detail page.
"""

from __future__ import annotations

from datetime import date

LONG_RIDE_DUE_DAYS = 21      # long ride every 2-3 weeks
SPLIT_GAP_DAYS = 8           # a split day not trained for more than a week
RIDING_DROP_SHARE = 0.5      # last 7 days below half the previous 4-week average
RIDING_HEAVY_SHARE = 1.3     # last 7 days above 130 % of the previous 4-week average
OK_LABEL = {"hrv": "HRV", "rhr": "resting HR", "sleep_h": "sleep duration", "sleep_score": "sleep score",
            "stress": "stress", "bb_wake": "Body Battery", "resp": "respiration"}
HEALTH_PANEL = {"hrv": "hrv", "rhr": "rhr", "sleep_h": "sleep", "sleep_score": "sleep", "stress": "stress",
                "bb_wake": "stress", "resp": "resp"}


def _fmt(v, digits=0):
    return "–" if v is None else f"{v:.{digits}f}"


def health_items(header: list[dict]) -> tuple[list[dict], list[str]]:
    items, ok = [], []
    for h in header:
        status = h.get("status")
        if status in ("no_data", "no_baseline", None):
            continue
        name = h["label"].split(" (")[0]
        if status in ("watch", "out_of_range"):
            word = "below" if h.get("direction") == "low" else "above"
            unit = f" {h['unit']}" if h.get("unit") else ""
            d = h.get("digits", 0)
            items.append({
                "tone": status, "kind": "body", "area": "health", "href": f"health.html#{HEALTH_PANEL.get(h['key'], 'hrv')}",
                "title": f"{name} {word} your usual range",
                "detail": f"7-day average {_fmt(h.get('avg7'), d)}{unit} · usual {_fmt(h.get('baseline'), d)} ± {_fmt(h.get('sd'), d)}{unit}",
            })
        else:
            ok.append(OK_LABEL.get(h["key"], name))
    return items, ok


def cycling_items(c: dict) -> tuple[list[dict], list[str]]:
    items, ok = [], []
    h7, avg = c.get("hours7"), c.get("hours_week_prev4")
    volume_flagged = False
    if h7 is not None and avg:
        if h7 == 0:
            items.append({"tone": "note", "kind": "rhythm", "area": "cycling", "href": "cycling.html#volume",
                          "title": "No training rides in the last 7 days",
                          "detail": f"the 4 weeks before averaged {avg:.1f} h a week (e-bike excluded)"})
            volume_flagged = True
        elif h7 < RIDING_DROP_SHARE * avg:
            items.append({"tone": "note", "kind": "rhythm", "area": "cycling", "href": "cycling.html#volume",
                          "title": "Riding volume well below your recent weeks",
                          "detail": f"{h7:.1f} h in the last 7 days · the 4 weeks before averaged {avg:.1f} h"})
            volume_flagged = True
        else:
            ok.append("riding volume")
    flag = c.get("effort_flag")
    if flag == "under" and volume_flagged:
        pass  # the volume item above already says it: one line per fact
    elif flag:
        title = ("Last week's riding load was well above your recent range" if flag == "over"
                 else "Riding load below your recent range for two weeks")
        items.append({"tone": "note", "kind": "rhythm", "area": "cycling", "href": "cycling.html#effort",
                      "title": title, "detail": "compared with the 3 weeks before each week · rides only"})
    elif "effort_flag" in c:
        ok.append("weekly effort")
    days = c.get("days_since_long")
    if days is not None:
        if days > LONG_RIDE_DUE_DAYS:
            items.append({"tone": "note", "kind": "rhythm", "area": "cycling", "href": "cycling.html#long",
                          "title": "Long ride overdue",
                          "detail": f"{days} days since the last ride of {c.get('long_min', 150)} min or more · aim for every 2–3 weeks"})
        else:
            ok.append("long-ride rhythm")
    return items, ok


def strength_items(s: dict, today: date) -> tuple[list[dict], list[str]]:
    items, ok = [], []
    gaps = []
    for t, last in (s.get("last_by_type") or {}).items():
        if last is None:
            continue
        days = (today - last).days
        if days > SPLIT_GAP_DAYS:
            gaps.append((t, days, last))
    for t, days, last in sorted(gaps, key=lambda g: -g[1]):
        items.append({"tone": "note", "kind": "rhythm", "area": "strength", "href": "strength.html#sessions",
                      "title": f"No {t} session for {days} days",
                      "detail": f"last one on {last.day} {last.strftime('%b')}"})
    if s.get("last_by_type") and not gaps:
        ok.append("training split")
    stuck = s.get("flat_lifts") or []
    down = s.get("regressing_lifts") or []
    if down:
        items.append({"tone": "note", "kind": "rhythm", "area": "strength", "href": "strength.html#progression",
                      "title": f"{len(down)} lift{'s' if len(down) > 1 else ''} slipping",
                      "detail": ", ".join(down[:4]) + (" …" if len(down) > 4 else "") + " · best of the last 3 sessions below the 3 before"})
    if stuck:
        items.append({"tone": "note", "kind": "rhythm", "area": "strength", "href": "strength.html#progression",
                      "title": f"{len(stuck)} lift{'s' if len(stuck) > 1 else ''} flat for 3 sessions",
                      "detail": ", ".join(stuck[:4]) + (" …" if len(stuck) > 4 else "") + " · consider adding reps before weight"})
    if not stuck and not down:
        ok.append("lift progression")
    return items, ok


def _load_word(c: dict) -> str | None:
    """'lighter' / 'heavier' / 'usual' riding week vs the 4 weeks before, None without history."""
    h7, avg = c.get("hours7"), c.get("hours_week_prev4")
    if h7 is None or not avg:
        return None
    return "lighter" if h7 < RIDING_DROP_SHARE * avg else "heavier" if h7 > RIDING_HEAVY_SHARE * avg else "usual"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _body_phrase(health_header: list[dict], verb: str = "") -> str:
    """'HRV below and resting HR above your usual range' (verb 'is' -> 'HRV is below and ...')."""
    off = [h for h in health_header if h.get("status") in ("watch", "out_of_range")]
    bits = [[OK_LABEL.get(h["key"], h["label"]), "below" if h.get("direction") == "low" else "above"] for h in off]
    if verb:
        bits[0][1] = f"{verb} {bits[0][1]}"
    text = _join([" ".join(b) for b in bits]) + " your usual range"
    return text[0].upper() + text[1:]


def synthesis(health_header: list[dict], body: list[dict], cycling: dict, strength: dict, rhythm: list[dict]) -> str:
    """One calm sentence: is the body off, and does training explain it?"""
    if body:
        lead = _body_phrase(health_header, "is")
        load = _load_word(cycling)
        if load == "lighter":
            return lead + " during a lighter training week, so training load is unlikely to be the reason. Sleep and stress are worth a look."
        if load == "heavier":
            return lead + " after a heavier training week, which fits normal fatigue. An easier day or two should bring it back."
        return lead + " while training is at its usual level. Worth watching for a few more days."
    if rhythm:
        return "Your body is in its usual range. On the training side there " + \
            ("is one thing to note." if len(rhythm) == 1 else f"are {len(rhythm)} things to note.")
    return "Your body is in its usual range and training is on rhythm."


def area_lines(health: dict, cycling: dict, strength: dict, items: list[dict], health_header: list[dict]) -> list[dict]:
    """One row per area for the Overview: status, what is flagged (or that nothing is) and two plain numbers."""
    def flagged(area):
        return [i for i in items if i["area"] == area]
    def lower(t):
        return t[0].lower() + t[1:] if t and not t[:3].isupper() and not t.startswith("HRV") else t
    def what(area, fine):
        f = flagged(area)
        return "; ".join([f[0]["title"]] + [lower(i["title"]) for i in f[1:]]) if f else fine
    hb = flagged("health")
    n_out, n_all = health.get("n_outside", 0), health.get("n_metrics", 0)
    split = strength.get("split") or []
    return [
        {"area": "health", "title": "Body", "href": "health.html",
         "status": max((i["tone"] for i in hb), key=lambda t: t == "out_of_range") if hb else "in_range",
         "line": _body_phrase(health_header) if hb else f"All {n_all} health metrics in your usual range",
         "facts": f"HRV {_fmt(health.get('hrv_avg7'))} ms · resting HR {_fmt(health.get('rhr_avg7'))} bpm, 7-day averages"
                  + (f" · {n_out} of {n_all} metrics outside your usual range" if n_out else "")},
        {"area": "strength", "title": "Strength", "href": "strength.html",
         "status": "note" if flagged("strength") else "in_range",
         "line": what("strength", "Split and lifts on track"),
         "facts": f"{strength.get('sessions_week', 0)} of {2 * len(split)} sessions this week"
                  + (f" ({' + '.join(split)}, twice)" if split else "") + f" · {strength.get('hard_sets7', 0)} hard sets in 7 days"},
        {"area": "cycling", "title": "Cycling", "href": "cycling.html",
         "status": "note" if flagged("cycling") else "in_range",
         "line": what("cycling", "Riding volume and long rides on rhythm"),
         "facts": f"{_fmt(cycling.get('hours7'), 1)} h in 7 days · fitness {_fmt(cycling.get('ctl'), 1)} (CTL)"},
    ]


def build(health_header: list[dict], cycling: dict, strength: dict, today: date, health: dict | None = None) -> dict:
    """{'items', 'ok', 'sentence', 'areas'}: items health first (it frames the rest), then strength, then cycling."""
    hi, hok = health_items(health_header)
    si, sok = strength_items(strength, today)
    ci, cok = cycling_items(cycling)
    items = hi + si + ci
    items.sort(key=lambda x: x["tone"] != "out_of_range")  # stable: out-of-range first, otherwise rubric order
    return {"items": items, "ok": hok + sok + cok,
            "sentence": synthesis(health_header, hi, cycling, strength, si + ci),
            "areas": area_lines(health or {}, cycling, strength, items, health_header)}
