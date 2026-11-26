"""Weekly brief: one self-contained JSON document describing an ISO week, for the AI weekly review.

Built from the dashboard build artifacts (build/metrics.duckdb + build/data/*.json, so run `make metrics` first) plus the
strength-log titles/comments in ../garmin.db. Every number in it is computed here; the reviewer must not invent any.

    .venv/bin/python -m src.weekly_brief [YYYY-MM-DD (any day of the week)]   -> prints the brief
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import warnings

import duckdb
import numpy as np
import pandas as pd

from src.adapters import source as src
from src.metrics import common
from src.metrics import strength as st

warnings.filterwarnings("ignore", message="The .generic. unit for NumPy timedelta is deprecated")
BUILD = src.PROJECT / "build"
SCHEMA_VERSION = 1
HEALTH = [  # key, label, unit, unfavorable direction
    ("hrv", "Overnight HRV", "ms", "low"), ("rhr", "Resting HR", "bpm", "high"), ("sleep_h", "Sleep duration", "h", "low"),
    ("sleep_score", "Sleep score", "", "low"), ("stress", "Stress avg", "", "high"), ("bb_wake", "Body Battery at wake-up", "", "low"),
    ("resp", "Respiration in sleep", "br/min", "high"), ("spo2", "SpO2 in sleep", "%", "low"),
]


def r(v, d=1):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), d)


def last_complete_week(today: date) -> date:
    """Monday of the most recent ISO week that has fully ended."""
    return common.week_start(today) - timedelta(days=7)


def iso_label(monday: date) -> str:
    y, w, _ = monday.isocalendar()
    return f"{y}-W{w:02d}"


def build_brief(monday: date) -> dict:
    cfg = src.config()
    sunday = monday + timedelta(days=6)
    m0, s0 = pd.Timestamp(monday), pd.Timestamp(sunday)
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    acts = con.execute("SELECT * FROM activities").df()
    acts["date"] = pd.to_datetime(acts["date"])
    dh = con.execute("SELECT * FROM daily_health").df()
    dh["date"] = pd.to_datetime(dh["date"])
    dh = dh.set_index("date")
    sets = con.execute("SELECT * FROM strength_sets").df()
    sets["date"] = pd.to_datetime(sets["date"])
    mr = con.execute("SELECT * FROM muscle_rows").df()
    mr["date"] = pd.to_datetime(mr["date"])
    cyc_p = json.loads((BUILD / "data" / "cycling.json").read_text())
    str_p = json.loads((BUILD / "data" / "strength.json").read_text())
    data_through = cyc_p["meta"]["data_through"]
    in_week = lambda df: df[(df["date"] >= m0) & (df["date"] <= s0)]  # noqa: E731

    # ------------------------------------------------------------------ health
    wk = dh.loc[m0:s0]
    metrics = {}
    for key, label, unit, unfav in HEALTH:
        if key not in wk or wk[key].notna().sum() == 0:
            continue
        z_end = wk[f"{key}_z"].dropna()
        z = float(z_end.iloc[-1]) if len(z_end) else float("nan")
        base = wk[f"{key}_base"].dropna()
        sd = wk[f"{key}_sd"].dropna()
        metrics[key] = {
            "label": label, "unit": unit, "unfavorable_direction": unfav,
            "week_mean": r(wk[key].mean(), 2), "week_min": r(wk[key].min(), 2), "week_max": r(wk[key].max(), 2),
            "baseline_60d": r(base.iloc[-1], 2) if len(base) else None, "baseline_sd": r(sd.iloc[-1], 2) if len(sd) else None,
            "z_7d_vs_baseline_at_week_end": r(z, 2), "flag": common.flag(z, unfav),
            "prev_week_mean": r(dh.loc[m0 - pd.Timedelta(days=7):m0 - pd.Timedelta(days=1), key].mean(), 2),
            "daily": {d.date().isoformat(): r(v, 2) for d, v in wk[key].items()},
        }
    from src.metrics import health as hl
    strain = hl.strain_days(dh["rhr_day_z"], dh["hrv_day_z"], dh["resp_day_z"])
    nights = int(wk["sleep_h"].notna().sum())
    health = {
        "metrics": metrics,
        "sleep": {"nights_recorded": nights, "avg_bedtime": _clock(wk["bed_min"].mean()), "avg_wake": _clock(wk["wake_min"].mean()),
                  "midpoint_sd_min": r(wk["mid_min"].std(), 0),
                  "stages_avg_h": {k: r(wk[f"{k}_s"].mean(), 2) for k in ("deep", "light", "rem", "awake")}},
        "garmin_hrv_status_by_day": {d.date().isoformat(): v for d, v in wk["hrv_status"].items() if isinstance(v, str)},
        "strain_pattern_days": [d.date().isoformat() for d in strain.loc[m0:s0][strain.loc[m0:s0]].index],
        "steps_avg_per_day": r(wk["steps"].mean(), 0),
        "intensity_minutes": {"week": r(wk["im"].sum(), 0), "reference": 150, "note": "moderate + 2 x vigorous (Garmin's rule)"},
    }

    # ------------------------------------------------------------------ cycling
    rides_all = acts[acts["sport"] == "cycling"]
    rides = in_week(rides_all).sort_values("start_ts_local")
    def totals(df):
        d = df[df["ride_type"] != "ebike"]
        return {"rides": int(len(d)), "hours": r(d["duration_s"].sum() / 3600, 2), "km": r(d["distance_m"].fillna(0).sum() / 1000, 1),
                "load_trimp": r(d["trimp"].sum(), 0),
                "by_type_hours": {t: r(g["duration_s"].sum() / 3600, 2) for t, g in df.groupby("ride_type")},
                "ebike_hours": r(df.loc[df["ride_type"] == "ebike", "duration_s"].sum() / 3600, 2)}
    prev = rides_all[(rides_all["date"] >= m0 - pd.Timedelta(days=7)) & (rides_all["date"] < m0)]
    last4 = rides_all[(rides_all["date"] >= m0 - pd.Timedelta(days=28)) & (rides_all["date"] < m0)]
    t4 = totals(last4)
    avg4 = {k: r(v / 4, 2) for k, v in t4.items() if isinstance(v, (int, float)) and v is not None}
    strap = rides[(rides["hr_quality"] == "strap") & (rides["ride_type"] != "ebike")]
    zsum = strap[["z1_s", "z2_s", "z3_s"]].sum()
    ztot = zsum.sum()
    fit = cyc_p["fitness"]["variants"]["excl_ebike"]
    ctl = dict((d, v) for d, v in fit["ctl"]); atl = dict((d, v) for d, v in fit["atl"]); tsb = dict((d, v) for d, v in fit["tsb"])
    long_min = cfg["cycling"]["long_ride_min_minutes"]
    longs = rides_all[(rides_all["ride_type"] != "ebike") & (rides_all["moving_s"] >= long_min * 60) & (rides_all["date"] <= s0)]
    z1, z2 = cyc_p["intensity"]["bounds"]
    cycling = {
        "rides": [{
            "date": x.date.date().isoformat(), "start": str(x.start_ts_local)[11:16], "type": x.ride_type, "name": x.name,
            "minutes": r(x.duration_s / 60, 0), "km": r((x.distance_m or 0) / 1000, 1), "avg_hr": r(x.avg_hr, 0), "max_hr": r(x.max_hr, 0),
            "hr_source": x.hr_quality, "trimp": r(x.trimp, 0),
            "zones_min": {"z1": r(x.z1_s / 60, 0), "z2_grey": r(x.z2_s / 60, 0), "z3": r(x.z3_s / 60, 0)} if pd.notna(x.z1_s) else None,
            "np_w": r(x.np, 0), "avg_power_w": r(x.avg_power, 0), "decoupling_pct": r(x.dec_decoupling_pct, 1),
            "decoupling_method": x.dec_method if isinstance(x.dec_method, str) else None,
            "sprints_detected": int(x.sprints_speed) if x.ride_type == "commute" and pd.notna(x.sprints_speed) else None,
        } for x in rides.itertuples()],
        "totals_excl_ebike": totals(rides), "prev_week_totals": totals(prev), "avg_per_week_prev_4w": avg4,
        "intensity_strap_rides": {"strap_minutes": r(ztot / 60, 0), "z1_pct": r(100 * zsum["z1_s"] / ztot, 0) if ztot else None,
                                  "z2_grey_pct": r(100 * zsum["z2_s"] / ztot, 0) if ztot else None,
                                  "z3_pct": r(100 * zsum["z3_s"] / ztot, 0) if ztot else None,
                                  "zone_bounds_bpm": {"z1_below": z1, "z2_grey": f"{z1}-{z2}", "z3_from": z2},
                                  "polarized_reference_pct": cyc_p["intensity"]["reference"]},
        "fitness_trimp": {"ctl_week_start": r(ctl.get(monday.isoformat())), "ctl_week_end": r(ctl.get(sunday.isoformat())),
                          "atl_week_end": r(atl.get(sunday.isoformat())), "tsb_week_end": r(tsb.get(sunday.isoformat())),
                          "note": "TRIMP units from HR; e-bike excluded"},
        "days_since_long_ride_at_week_end": int((s0 - longs["date"].max()).days) if len(longs) else None,
        "long_ride_definition_min": long_min,
        "eftp_w": cyc_p["power"]["eftp_now"], "eftp_is_lower_bound": cyc_p["power"]["eftp_lower_bound"],
        "ftp_goal_w": cfg["athlete"]["ftp_goal_w"],
    }

    # ------------------------------------------------------------------ strength
    with src.connect() as raw:
        mw = pd.read_sql_query("SELECT workout_id, workout_date, title, comments, garmin_activity_id FROM manual_workouts", raw)
    mw["workout_date"] = pd.to_datetime(mw["workout_date"])
    week_w = mw[(mw["workout_date"] >= m0) & (mw["workout_date"] <= s0)].sort_values("workout_date")
    stypes = {s["date"]: s["type"] for s in str_p["calendar"]}
    prog = {p["exercise"]: p for p in str_p["progression"]}
    links = src.load_strength_links()  # a note session can own several Garmin activities (watch restarted)
    gps = st.garmin_per_session(acts, links)
    sessions = []
    for w in week_w.itertuples():
        g = gps.loc[w.workout_id] if w.workout_id in gps.index else None
        ss = sets[(sets["session"] == w.workout_id)]
        exercises = []
        for ex, e in ss.groupby("exercise", sort=False):
            prior = sets[(sets["exercise"] == ex) & (sets["date"] < w.workout_date) & sets["working"]]
            prev_best = prior[prior["date"] == prior["date"].max()]["e1rm"].max() if len(prior) else np.nan
            exercises.append({
                "exercise": ex, "sets": [_fmt_set(x) for x in e.itertuples()],
                "working_sets": int(e["working"].sum()), "best_e1rm_kg": r(e["e1rm"].max(), 1),
                "previous_session_e1rm_kg": r(prev_best, 1), "to_failure": bool(e["to_failure"].fillna(0).astype(int).max()),
                "status_overall": prog.get(ex, {}).get("status")})
        sessions.append({"date": w.workout_date.date().isoformat(), "type": stypes.get(w.workout_date.date().isoformat()),
                         "note_title": w.title, "note_comments": w.comments,
                         "minutes": r(g["duration_s"] / 60, 0) if g is not None and g["duration_s"] else None,
                         "avg_hr": r(g["avg_hr"], 0) if g is not None else None, "exercises": exercises})
    garmin_str = in_week(acts[acts["sub_sport"] == "strength_training"])
    unlogged = garmin_str[~garmin_str["activity_id"].isin(links["activity_id"].dropna()) & (garmin_str["duration_s"] >= 300)]
    musc = mr[(mr["date"] >= m0) & (mr["date"] <= s0)].groupby("muscle")["w"].sum()
    labels = str_p["weekly_sets"]["labels"]
    split = cfg["strength"]["split"]
    types_done = [s["type"] for s in sessions]
    rounds = min(types_done.count(t) for t in split) if sessions else 0
    prev_sessions = mw[(mw["workout_date"] >= m0 - pd.Timedelta(days=7)) & (mw["workout_date"] < m0)]
    strength = {
        "sessions": sessions, "logged_sessions": len(sessions), "prev_week_logged_sessions": int(len(prev_sessions)),
        "unlogged_garmin_sessions": [{"date": x.date.date().isoformat(), "minutes": r(x.duration_s / 60, 0)} for x in unlogged.itertuples()],
        "full_rounds_completed": rounds, "split": split, "target": "1 round = 3 sessions; a good week = 2 rounds",
        "sets_per_muscle": {labels[m]: r(musc.get(m, 0.0), 1) for m in labels},
        "weekly_sets_band": cfg["strength"]["weekly_sets_band"],
        "tonnage_kg": r(sets[(sets["date"] >= m0) & (sets["date"] <= s0) & sets["working"]]["set_load"].sum(), 0),
        "legs_note": str_p.get("legs_note"),
        "progression_status_all_lifts": {p["exercise"]: p["status"] for p in str_p["progression"] if (pd.Timestamp(p["last_date"]) >= m0 - pd.Timedelta(days=42))},
        "conventions": "Dumbbell weights are per hand ('2x9kg' = a pair of 9 kg). Pull-up e1RM includes bodyweight. e1RM = Epley, 1-12 reps.",
    }

    # ------------------------------------------------------------------ budget + data quality
    lift_h = garmin_str["duration_s"].sum() / 3600
    caveats = []
    wrist = [x for x in cycling["rides"] if x["hr_source"] == "wrist" and x["type"] != "ebike"]
    if wrist:
        caveats.append(f"{len(wrist)} ride(s) used wrist HR only (less reliable on the bike); intensity split uses strap rides only.")
    if nights < 7:
        caveats.append(f"Sleep recorded on {nights} of 7 nights.")
    if len(unlogged):
        caveats.append(f"{len(unlogged)} Garmin strength session(s) have no entry in the notes log (sets unknown).")
    if cycling["eftp_is_lower_bound"]:
        caveats.append("eFTP is a lower bound: no maximal 20-minute effort has been recorded yet.")
    if pd.Timestamp(data_through) < s0:
        caveats.append(f"Data only runs through {data_through}; the week is incomplete.")

    return common.clean({
        "schema_version": SCHEMA_VERSION,
        "week": {"iso": iso_label(monday), "start": monday.isoformat(), "end": sunday.isoformat()},
        "data_through": data_through,
        "athlete": {"age": monday.year - cfg["athlete"]["birth_year"], "sex": cfg["athlete"]["sex"],
                    "height_cm": cfg["athlete"]["height_cm"], "hr_max": cfg["athlete"]["hr_max"],
                    "weight_kg_latest": str_p["notes"]["bodyweight_kg"],
                    "goals": {"strength": cfg["strength"]["goal"], "cycling": f"rebuild fitness, avoid the grey zone (tempo/threshold, about Garmin Z3–Z4), FTP goal {cfg['athlete']['ftp_goal_w']} W",
                              "pattern": f"commute {cfg['cycling']['commute_km']} km each way ~2x/week with short sprints, a long ride every 2-3 weeks, "
                                         f"strength split {' / '.join(split)}"}},
        "health": health, "cycling": cycling, "strength": strength,
        "time_budget_h": {"cycling_excl_ebike": cycling["totals_excl_ebike"]["hours"], "lifting": r(lift_h, 2)},
        "data_caveats": caveats,
    })


def _clock(m):
    if m is None or pd.isna(m):
        return None
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _fmt_set(x) -> str:
    if x.bodyweight_lift:
        base = f"BW+{x.weight_kg:g}kg" if pd.notna(x.weight_kg) and x.weight_kg else "BW"
    elif pd.isna(x.weight_kg):
        base = "no weight"
    else:
        base = f"{'2x' if x.implements == 2 else ''}{x.weight_kg:g}kg"
    return f"{base} x {int(x.reps)}" + (" (failure)" if x.to_failure else "") + ("" if x.working else " (warm-up)")


if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else last_complete_week(date.today())
    print(json.dumps(build_brief(common.week_start(day)), indent=1, ensure_ascii=False))
