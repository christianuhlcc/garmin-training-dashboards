"""Daily readiness: a transparent, rule-based "go / easy / rest" for today (Garmin's Training Readiness and Morning
Report are not available through the Personal API). Plus sleep need vs actual sleep and a rolling sleep debt.

Every factor is rated ok / caution / poor against fixed, documented thresholds, and the verdict is derived from the
ratings, so the Overview can always show *why*. Sleep, HRV and resting HR are dated by the wake-up morning, so today's
row describes last night.
"""

from __future__ import annotations

import math

import pandas as pd

DEBT_NIGHTS = 7
ACUTE_DAYS, CHRONIC_DAYS = 7, 28
MIN_CHRONIC_LOAD = 5.0          # mean daily TRIMP below this: too little history for a load ratio

# thresholds per factor: (caution, poor) boundaries, in the factor's own unit
HRV_Z = (-1.0, -2.0)            # z vs 60-day baseline, low is bad
RHR_Z = (1.0, 2.0)              # z vs 60-day baseline, high is bad
SLEEP_SHARE = (0.90, 0.75)      # last night / sleep need, low is bad
DEBT_H = (3.0, 6.0)             # 7-night debt in hours, high is bad
LOAD_RATIO = (1.3, 1.5)         # 7-day / 28-day mean daily load, high is bad

VERDICTS = {
    "go": ("Go", "Good to train today."),
    "easy": ("Easy", "Some signals are off: keep today light (easy ride, technique, or a short session)."),
    "rest": ("Rest", "Your body is under strain: rest or move gently today."),
    "unknown": ("No data", "Not enough data from last night to judge today."),
}
CORE = ("hrv", "rhr", "strain")  # a poor rating here alone means rest


def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def sleep_debt(sleep_h: pd.Series, need_h: pd.Series, nights: int = DEBT_NIGHTS) -> pd.Series:
    """Rolling sum of (need - sleep) over the last `nights` nights, floored at 0. A long night pays back a short one
    within the window; nights without a sleep record are skipped (not counted as zero sleep)."""
    gap = (need_h - sleep_h)
    s = gap.rolling(nights, min_periods=max(3, nights // 2)).sum()
    return s.clip(lower=0)


def load_ratio(daily_load: pd.Series) -> pd.Series:
    """Acute:chronic ratio of mean daily load (7 vs 28 days, both incl. today). NaN while the chronic mean is tiny."""
    acute = daily_load.rolling(ACUTE_DAYS, min_periods=ACUTE_DAYS).mean()
    chronic = daily_load.rolling(CHRONIC_DAYS, min_periods=CHRONIC_DAYS).mean()
    r = acute / chronic
    return r.where(chronic >= MIN_CHRONIC_LOAD)


def rate(value, bounds: tuple[float, float], low_is_bad: bool) -> str | None:
    if _isnan(value):
        return None
    caution, poor = bounds
    if low_is_bad:
        return "poor" if value < poor else "caution" if value < caution else "ok"
    return "poor" if value > poor else "caution" if value > caution else "ok"


def verdict(ratings: dict[str, str | None]) -> str:
    known = {k: v for k, v in ratings.items() if v is not None}
    if not any(k in known for k in ("hrv", "rhr", "sleep")):
        return "unknown"
    poor = [k for k, v in known.items() if v == "poor"]
    caution = [k for k, v in known.items() if v == "caution"]
    if any(k in CORE for k in poor) or len(poor) >= 2:
        return "rest"
    if poor or len(caution) >= 2:
        return "easy"
    return "go"


def assess(day: dict) -> dict:
    """day: hrv, hrv_z, hrv_status, rhr, rhr_z, sleep_h, need_h, debt_h, load_ratio, strain (bool).
    Returns the verdict and one row per factor with a human-readable value."""
    f = []

    def add(key, label, status, value_text, detail):
        f.append({"key": key, "label": label, "status": status, "value": value_text, "detail": detail})

    hrv_status = rate(day.get("hrv_z"), HRV_Z, low_is_bad=True)
    if hrv_status == "ok" and day.get("hrv_status") in ("LOW", "POOR"):
        hrv_status = "caution"  # Garmin's own 3-week view says low even though last night was near normal
    add("hrv", "HRV", hrv_status,
        None if _isnan(day.get("hrv")) else f"{day['hrv']:.0f} ms",
        None if _isnan(day.get("hrv_z")) else f"{_sd(day['hrv_z'])} your normal" +
        (f", Garmin status {day['hrv_status'].lower()}" if day.get("hrv_status") else ""))
    add("rhr", "Resting HR", rate(day.get("rhr_z"), RHR_Z, low_is_bad=False),
        None if _isnan(day.get("rhr")) else f"{day['rhr']:.0f} bpm",
        None if _isnan(day.get("rhr_z")) else f"{_sd(day['rhr_z'])} your normal")
    share = None if _isnan(day.get("sleep_h")) or _isnan(day.get("need_h")) or not day["need_h"] else day["sleep_h"] / day["need_h"]
    add("sleep", "Last night", rate(share, SLEEP_SHARE, low_is_bad=True),
        None if _isnan(day.get("sleep_h")) else _hm(day["sleep_h"]),
        None if share is None else f"{share * 100:.0f} % of your {_hm(day['need_h'])} need")
    add("debt", "Sleep debt", rate(day.get("debt_h"), DEBT_H, low_is_bad=False),
        None if _isnan(day.get("debt_h")) else ("none" if day["debt_h"] < 1 / 60 else _hm(day["debt_h"])), f"over the last {DEBT_NIGHTS} nights")
    add("load", "Training load", rate(day.get("load_ratio"), LOAD_RATIO, low_is_bad=False),
        None if _isnan(day.get("load_ratio")) else f"{day['load_ratio']:.1f}×",
        "last 7 days vs the 28-day average")
    if day.get("strain"):
        add("strain", "Strain pattern", "poor", "present", "HRV down, resting HR and respiration up together")

    v = verdict({x["key"]: x["status"] for x in f})
    label, text = VERDICTS[v]
    off = [x["label"] for x in f if x["status"] in ("caution", "poor")]
    reason = ("Off: " + ", ".join(off) + ".") if off else ("All signals in range." if v == "go" else "")
    return {"verdict": v, "label": label, "text": text, "reason": reason, "factors": f}


def _sd(z: float) -> str:
    return "at" if abs(z) < 0.05 else f"{abs(z):.1f} SD {'above' if z > 0 else 'below'}"


def _hm(h: float) -> str:
    m = int(round(h * 60))
    return f"{m // 60} h {m % 60:02d} min"


NEED_WHY = {  # Garmin's own reasons for adjusting tonight's sleep need, from sleep.raw.sleepNeed
    ("need_training_adj", "HIGH_ACWR"): "+ training load jumped",
    ("need_training_adj", "HIGH_ACWR_AND_CHRONIC"): "+ training load is high",
    ("need_hrv_adj", "INCREASING"): "+ HRV is low",
    ("need_history_adj", "INCREASING"): "+ recent short sleep",
    ("need_history_adj", "INCREASING_LOW_QUALITY"): "+ recent poor sleep",
    ("need_history_adj", "DECREASING"): "− recent good sleep",
    ("need_history_adj", "DECREASING_HIGH_QUALITY"): "− recent high-quality sleep",
    ("need_nap_adj", "DECREASING"): "− nap",
}


def need_reasons(adj: dict, need_h: float | None, base_h: float | None) -> str | None:
    """'Need 8 h 10 min (usual 7 h 50 min): + HRV is low, − recent good sleep' from Garmin's adjustment codes."""
    if need_h is None:
        return None
    parts = [txt for (k, code), txt in NEED_WHY.items() if adj.get(k) == code]
    head = f"Need {_hm(need_h)}" + (f" (usual {_hm(base_h)})" if base_h and abs(base_h - need_h) > 1e-6 else "")
    return head + (": " + ", ".join(parts) if parts else "")


def plan(nxt: dict | None, verdict_key: str) -> dict | None:
    """Combine the next planned session with today's readiness into one action line."""
    if not nxt:
        return None
    t = nxt["type"].capitalize()
    tgt = " + ".join(m["label"].lower() for m in nxt["muscles"])
    since = "" if nxt["days_since_type"] is None else f"last {nxt['type']} {nxt['days_since_type']} d ago"
    musc = [f"{m['label'].lower()} " + ("never trained" if m["days"] is None else f"{m['days']} d" if m["status"] != "not_recovered" else "trained today")
            + (" ✓" if m["status"] == "recovered" else "") for m in nxt["muscles"]]
    not_rec = [m["label"].lower() for m in nxt["muscles"] if m["status"] == "not_recovered"]
    caution = [m["label"].lower() for m in nxt["muscles"] if m["status"] == "caution"]
    flat = [h["exercise"] for h in nxt["hints"] if h["status"] == "flat"]
    slip = [h["exercise"] for h in nxt["hints"] if h["status"] == "regressing"]

    if nxt["today_types"]:
        status, action = "done", f"Today's {' + '.join(nxt['today_types'])} session is logged; next up after that is {nxt['type']}."
    elif verdict_key == "rest":
        status, action = "skip", f"Skip lifting today; {nxt['type']} moves to tomorrow."
    elif not_rec:
        status, action = "skip", f"{', '.join(not_rec).capitalize()} trained today: do {nxt['type']} tomorrow."
    elif verdict_key == "easy":
        status, action = "light", "Do a lighter version: same lifts, about 2 fewer sets, stop 2 reps short of failure."
    elif caution:
        status, action = "light", f"Train, but go easier on {', '.join(caution)} (worked yesterday)."
    elif verdict_key == "unknown":
        status, action = "go", "Muscles are recovered; no readiness data from last night, so go by feel."
    else:
        status, action = "go", "Train as planned."
    hint = None
    if status == "go" and (flat or slip):
        parts = []
        if flat:
            parts.append(f"{', '.join(flat)} flat for 3 sessions: try +1 rep before adding weight")
        if slip:
            parts.append(f"{', '.join(slip)} slipping: repeat the last weight and aim to match its reps")
        hint = ". ".join(parts) + "."
    return {"type": nxt["type"], "title": t, "targets": tgt, "since": since, "muscles": musc, "status": status,
            "action": action, "hint": hint, "last_of_type": nxt["last_of_type"]}


def trend_note(day_status: dict, week: dict) -> str | None:
    """Explain when last night looks fine but the 7-day average (the Overview's Body card) is still flagged.
    day_status: factor -> rating for last night; week: factor -> 7-day flag (in_range / watch / out_of_range)."""
    names = {"hrv": ("HRV", "low"), "rhr": ("resting HR", "high")}
    lagging = [k for k in ("hrv", "rhr") if week.get(k) in ("watch", "out_of_range") and day_status.get(k) in ("ok", "caution")]
    if not lagging:
        return None
    what = " and ".join(f"7-day {names[k][0]} is still {names[k][1]}" for k in lagging)
    return f"Last night is close to your normal, but your {what}, which is why Body shows Watch. One good night doesn't end a trend: avoid max attempts."
