"""Strength metrics (plan sections 6.2, 7.6, 7.10). Input: canonical strength_sets (manual log is the truth)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

MAP_PATH = Path(__file__).resolve().parents[1] / "exercise_map.yaml"


def load_exercise_map(path: Path = MAP_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def e1rm(weight: float, reps: float) -> float:
    """Epley (7.6), only for 1-12 reps and weight > 0. Dumbbells: per-hand weight, never doubled."""
    if weight is None or reps is None or (isinstance(weight, float) and math.isnan(weight)):
        return float("nan")
    if not (1 <= reps <= 12) or weight <= 0:
        return float("nan")
    return weight * (1 + reps / 30)


TREND_MAX_REPS = 20


def trend_e1rm(weight: float, reps: float) -> float:
    """Epley for the progression *trend* only, up to TREND_MAX_REPS reps. The displayed e1RM stays limited to
    1-12 reps (too inaccurate as an absolute number above that), but for the direction of a trend a 15-rep
    session must count: dropping it made a 3x15 @ 7 kg block look like a regression against older 12-rep sets."""
    if weight is None or reps is None or (isinstance(weight, float) and math.isnan(weight)):
        return float("nan")
    if not (1 <= reps <= TREND_MAX_REPS) or weight <= 0:
        return float("nan")
    return weight * (1 + reps / 30)


def prepare_sets(sets: pd.DataFrame, emap: dict, bodyweight: pd.Series, bw_fallback: float) -> pd.DataFrame:
    """Manual sets only, with effective weight (bodyweight + added for bodyweight lifts), e1RM and a
    working-set flag (7.10: exclude sets below 60% of the session's top weight for that exercise)."""
    s = sets[(sets["source"] == "manual") & (sets["set_type"] == "active")].copy()
    if s.empty:
        return s.assign(eff_weight=[], e1rm=[], trend_e1rm=[], working=[], session=[])
    s["date"] = pd.to_datetime(s["date"])
    s["session"] = s["workout_id"].astype("int64")
    ex = emap["exercises"]
    s["bodyweight_lift"] = s["exercise"].map(lambda e: bool((ex.get(e) or {}).get("bodyweight")))
    bw = bodyweight.dropna().sort_index()
    bw.index = pd.to_datetime(bw.index)
    def bw_at(d):
        prior = bw[bw.index <= d] if len(bw) else bw
        return float(prior.iloc[-1]) if len(prior) else bw_fallback
    s["bw"] = s["date"].map(bw_at)
    s["weight_kg"] = pd.to_numeric(s["weight_kg"], errors="coerce")
    s["reps"] = pd.to_numeric(s["reps"], errors="coerce")
    added = s["weight_kg"].fillna(0.0)
    s["eff_weight"] = np.where(s["bodyweight_lift"], s["bw"] + added, s["weight_kg"].astype(float))
    s["e1rm"] = [e1rm(w, r) for w, r in zip(s["eff_weight"], s["reps"])]
    s["trend_e1rm"] = [trend_e1rm(w, r) for w, r in zip(s["eff_weight"], s["reps"])]
    top = s.groupby(["session", "exercise"])["eff_weight"].transform("max")
    s["working"] = s["eff_weight"].isna() | (s["eff_weight"] >= 0.6 * top)
    s["set_load"] = s["eff_weight"].fillna(0.0) * s["reps"].fillna(0)
    return s


def muscle_rows(s: pd.DataFrame, emap: dict) -> tuple[pd.DataFrame, list[str]]:
    """One row per (working set, muscle) with weight w and weighted load. Returns (rows, unmapped exercises)."""
    ex = emap["exercises"]
    unmapped = sorted({e for e in s["exercise"].dropna().unique() if e not in ex})
    rows = []
    for r in s[s["working"]].itertuples():
        for m, w in ((ex.get(r.exercise) or {}).get("muscles") or {}).items():
            rows.append({"date": r.date, "session": r.session, "exercise": r.exercise, "muscle": m, "w": w,
                         "load": r.set_load * w})
    cols = ["date", "session", "exercise", "muscle", "w", "load"]
    return pd.DataFrame(rows, columns=cols), unmapped


def weighted_sets(mr: pd.DataFrame, start, end) -> pd.Series:
    """weighted_sets[m] = sum of w over working sets in [start, end]."""
    m = mr[(mr["date"] >= pd.Timestamp(start)) & (mr["date"] <= pd.Timestamp(end))]
    return m.groupby("muscle")["w"].sum()


def recency(mr: pd.DataFrame, today, half_life_days: float = 3.0) -> pd.Series:
    """recency[m] = sum of w * 0.5 ** (days_ago / half_life) over past sets (raw, before normalization)."""
    past = mr[mr["date"] <= pd.Timestamp(today)]
    if past.empty:
        return pd.Series(dtype=float)
    days_ago = (pd.Timestamp(today) - past["date"]).dt.days.astype(float)
    return (past["w"] * 0.5 ** (days_ago / half_life_days)).groupby(past["muscle"]).sum()


def typical_session_value(mr: pd.DataFrame, today, weeks: int = 8) -> pd.Series:
    """A muscle's typical single-session weighted sets (median over sessions that hit it, last `weeks` weeks)."""
    w = mr[(mr["date"] > pd.Timestamp(today) - pd.Timedelta(weeks=weeks)) & (mr["date"] <= pd.Timestamp(today))]
    return w.groupby(["muscle", "session"])["w"].sum().groupby("muscle").median()


def session_types(mr: pd.DataFrame, emap: dict) -> pd.Series:
    """Session type = muscle group with the most weighted sets (push / pull / shoulders / legs / core)."""
    groups = {k: v["group"] for k, v in emap["regions"].items()}
    g = mr.assign(group=mr["muscle"].map(groups)).groupby(["session", "group"])["w"].sum()
    return g.groupby(level=0).idxmax().map(lambda t: t[1])


def garmin_per_session(acts: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    """Garmin physiology per note session, combined over all its linked activities (a watch stopped and
    restarted gives one note entry several activities): duration and TRIMP summed, avg HR weighted by duration.
    Index workout_id; columns duration_s, avg_hr, trimp (if acts has it), n_activities."""
    cols = ["duration_s", "avg_hr", "trimp", "n_activities"]
    if links.empty or acts.empty:
        return pd.DataFrame(columns=cols, index=pd.Index([], name="workout_id"))
    keep = [c for c in ("activity_id", "duration_s", "avg_hr", "trimp") if c in acts.columns]
    m = links[["workout_id", "activity_id"]].astype("int64").merge(acts[keep].astype({"activity_id": "int64"}),
                                                                  on="activity_id")
    rows = {}
    for wid, g in m.groupby("workout_id"):
        dur = g["duration_s"].fillna(0)
        hr_w = dur[g["avg_hr"].notna()].sum()
        rows[wid] = {"duration_s": float(dur.sum()) or None,
                     "avg_hr": float((g["avg_hr"] * dur).sum() / hr_w) if hr_w else None,
                     "trimp": float(g["trimp"].sum(min_count=1)) if "trimp" in g and g["trimp"].notna().any() else None,
                     "n_activities": int(len(g))}
    out = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    out.index.name = "workout_id"
    return out


def session_best(s: pd.DataFrame) -> pd.DataFrame:
    """Best e1RM and top set (heaviest, then most reps) per session and exercise."""
    w = s[s["working"]]
    rows = []
    for (sess, ex), g in w.groupby(["session", "exercise"]):
        top = g.sort_values(["eff_weight", "reps"], ascending=False).iloc[0]
        rows.append({"session": sess, "date": g["date"].iloc[0], "exercise": ex,
                     "e1rm": g["e1rm"].max(), "trend_e1rm": g["trend_e1rm"].max(), "top_weight": top["weight_kg"], "top_eff_weight": top["eff_weight"],
                     "top_reps": top["reps"], "implements": top["implements"], "sets": len(g),
                     "reps_total": g["reps"].sum(), "bodyweight_lift": bool(top["bodyweight_lift"])})
    return pd.DataFrame(rows).sort_values(["exercise", "date"]) if rows else pd.DataFrame(
        columns=["session", "date", "exercise", "e1rm", "trend_e1rm", "top_weight", "top_eff_weight", "top_reps", "implements",
                 "sets", "reps_total", "bodyweight_lift"])


def progression_status(e1rms: list[float], tol: float = 0.01) -> str:
    """progressing / flat / regressing from the last sessions' trend e1RM (oldest first, see trend_e1rm).
    Compares the best of the last 3 sessions with the best of the 3 before them; flat = no gain across 3 sessions."""
    vals = [v for v in e1rms if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 4:
        return "new"
    recent, before = max(vals[-3:]), max(vals[-6:-3])
    if recent > before * (1 + tol):
        return "progressing"
    if recent >= before * (1 - 0.03):
        return "flat"
    return "regressing"


RECOVERED_DAYS = 2  # >= 2 days since a muscle's last primary work = recovered; 1 day = caution; same day = not yet


def primary_group(exercise: str, emap: dict) -> str | None:
    """The muscle group an exercise mainly trains (group of its highest-weighted muscle)."""
    muscles = (emap["exercises"].get(exercise) or {}).get("muscles") or {}
    if not muscles:
        return None
    return emap["regions"][max(muscles, key=muscles.get)]["group"]


def next_session(split: list[str], logged: pd.DataFrame, mr: pd.DataFrame, emap: dict, prog: list[dict], today) -> dict | None:
    """What to train next and whether its target muscles are recovered.

    logged: one row per logged session (date, type). Next = the split type after the most recent split session
    (legs / core sessions sit outside the rotation and don't move it). Target muscles = regions of that group;
    recovery counts only *primary* work (muscle weight 1.0), so e.g. pullovers don't mark chest as trained."""
    if not split:
        return None
    t = pd.Timestamp(today)
    rot = logged[logged["type"].isin(split)].sort_values("date")
    nxt = split[(split.index(rot["type"].iloc[-1]) + 1) % len(split)] if len(rot) else split[0]
    last_of = rot[rot["type"] == nxt]["date"].max() if (rot["type"] == nxt).any() else None
    today_types = sorted(set(logged[logged["date"] == t]["type"]))
    primary = mr[mr["w"] >= 1.0]
    muscles = []
    for m, r in emap["regions"].items():
        if r["group"] != nxt:
            continue
        d = primary[primary["muscle"] == m]["date"].max()
        days = None if pd.isna(d) else int((t - d).days)
        status = "recovered" if days is None or days >= RECOVERED_DAYS else "caution" if days == 1 else "not_recovered"
        muscles.append({"key": m, "label": r["label"], "last": None if pd.isna(d) else d.date(), "days": days, "status": status})
    hints = [{"exercise": p["exercise"], "status": p["status"]} for p in prog
             if p["status"] in ("flat", "regressing") and primary_group(p["exercise"], emap) == nxt]
    return {"type": nxt, "last_of_type": None if last_of is None or pd.isna(last_of) else last_of.date(),
            "days_since_type": None if last_of is None or pd.isna(last_of) else int((t - last_of).days),
            "today_types": today_types, "muscles": muscles, "hints": hints}
