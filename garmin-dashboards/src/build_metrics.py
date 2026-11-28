"""Build metrics: adapters -> metrics -> build/metrics.duckdb + one JSON payload per dashboard.

    make metrics
Payloads: build/data/<page>.json and build/site/data/<page>.js (window.DASH_DATA, so pages open from file://).
"""

from __future__ import annotations

import json
import math
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.adapters import source as src, strava_archive
from src.metrics import attention, common, cycling as cyc, health as hl, readiness as rd, strength as st

# numpy 2 + pandas 2.2 emit this for ordinary Timestamp - Timedelta arithmetic; not actionable here.
warnings.filterwarnings("ignore", message="The 'generic' unit for NumPy timedelta is deprecated")

BUILD = src.PROJECT / "build"
STREAM_CACHE = BUILD / "cache" / "stream_metrics.json"
RANGES = {"6w": 42, "3m": 91, "12m": 365, "all": None}


def log(msg: str):
    print(f"[metrics] {msg}", file=sys.stderr, flush=True)


# =========================================================================== activities + stream metrics

def resting_hr_reference(daily: pd.DataFrame, start=None) -> pd.Series:
    """hr_rest at a date = median of daily resting HR over the trailing 30 days (7.1). Before the first Garmin day
    (Strava-archive rides) the earliest value is carried back: an assumption, stated on the page."""
    s = pd.Series(daily["resting_hr"].values, index=pd.to_datetime(daily["date"])).astype(float)
    first = min(s.index.min(), pd.Timestamp(start)) if start is not None else s.index.min()
    idx = pd.date_range(first, max(s.index.max(), pd.Timestamp(date.today())), freq="D")
    return s.reindex(idx).rolling(30, min_periods=5).median().ffill().bfill()


def enrich_activities(cfg: dict, acts: pd.DataFrame, daily: pd.DataFrame, centers_since=None) -> pd.DataFrame:
    hr_max = cfg["athlete"]["hr_max"]
    z1, z2 = cyc.hr_zone_bounds(cfg)
    hr_rest = resting_hr_reference(daily, pd.to_datetime(acts["date"]).min())
    with src.connect() as con:
        counts = dict(con.execute("SELECT activity_id, COUNT(*) FROM activity_records GROUP BY 1").fetchall())
    counts.update(strava_archive.stream_counts())
    cache = json.loads(STREAM_CACHE.read_text()) if STREAM_CACHE.exists() else {}
    new_cache, computed = {}, 0
    rows = []
    for a in acts.itertuples():
        rest = float(hr_rest.get(pd.Timestamp(a.date), np.nan))
        key = f"{a.activity_id}|{counts.get(a.activity_id, 0)}|" + common.config_hash(
            hr_max, z1, z2, round(rest, 1), cfg["cycling"]["sprint"], cyc.STREAM_VERSION, cfg["cycling"].get("segments"))
        m = cache.get(key)
        if m is None and counts.get(a.activity_id):
            m = cyc.ride_stream_metrics(src.load_activity_streams(a.activity_id), rest, hr_max, z1, z2,
                                        cfg["cycling"]["sprint"], cfg["cycling"].get("segments"))
            m = common.clean(m)
            computed += 1
        m = m or {}
        new_cache[key] = m
        row = {"activity_id": a.activity_id, "hr_rest_ref": rest,
               **{k: v for k, v in m.items() if k not in ("mean_max", "segs", "track")},
               "segs": m.get("segs"), "track": m.get("track")}
        for d, v in (m.get("mean_max") or {}).items():
            row[f"mm_{d}"] = v
        if m.get("trimp") is not None:
            row["load_quality"] = "stream_strap" if a.hr_quality == "strap" else "stream_wrist"
        else:
            row["trimp"] = cyc.trimp_avg(a.avg_hr, a.duration_s, rest, hr_max)
            row["load_quality"] = "avg_hr"
        rows.append(row)
    STREAM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STREAM_CACHE.write_text(json.dumps(new_cache))
    log(f"stream metrics: {computed} computed, {len(rows) - computed} from cache")
    enriched = acts.merge(pd.DataFrame(rows), on="activity_id", how="left")
    enriched["start_ts_local"] = pd.to_datetime(enriched["start_ts_local"])
    enriched["date"] = pd.to_datetime(enriched["date"])
    enriched["week"] = enriched["date"].map(lambda d: pd.Timestamp(common.week_start(d)))
    enriched["end_ts_local"] = enriched["start_ts_local"] + pd.to_timedelta(enriched["duration_s"].fillna(0), unit="s")
    rides = enriched["sport"] == "cycling"
    enriched["ride_type"] = None
    enriched.loc[rides, "ride_type"] = cyc.classify_rides(enriched[rides], cfg, centers_since)
    return enriched


# =========================================================================== helpers

def weekly_index(start, end) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(common.week_start(start)), pd.Timestamp(common.week_start(end)), freq="7D")


def pts(s: pd.Series, digits: int = 2) -> list:
    return common.series_points(s, digits)


def num(v, digits: int = 1):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), digits)


def tile(label: str, value, unit: str = "", delta=None, delta_label: str = "", spark=None, good: str | None = None,
         digits: int = 1, note: str | None = None) -> dict:
    """KPI tile. good = 'up' | 'down' | None decides the delta color."""
    return {"label": label, "value": num(value, digits) if isinstance(value, (int, float, np.floating)) else value,
            "unit": unit, "delta": num(delta, digits), "delta_label": delta_label, "spark": spark or [],
            "good": good, "note": note}


def weekly_spark(s: pd.Series, weeks: int = 12, how: str = "sum") -> list:
    """Last `weeks` ISO weeks of a daily series as a list of numbers."""
    if s.empty:
        return []
    w = s.groupby(s.index.map(lambda d: pd.Timestamp(common.week_start(d)))).agg(how)
    idx = weekly_index(date.today() - timedelta(weeks=weeks - 1), date.today())
    return [num(v, 2) for v in w.reindex(idx).fillna(0 if how == "sum" else np.nan).tolist()]


def write_payload(name: str, payload: dict):
    payload = common.clean(payload)
    (BUILD / "data").mkdir(parents=True, exist_ok=True)
    (BUILD / "site" / "data").mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    (BUILD / "data" / f"{name}.json").write_text(text)
    (BUILD / "site" / "data" / f"{name}.js").write_text(
        f"window.DASH_DATA = window.DASH_DATA || {{}};\nwindow.DASH_DATA[{json.dumps(name)}] = {text};\n")
    log(f"payload {name}: {len(text) / 1e3:.0f} kB")


# =========================================================================== cycling

def build_cycling(cfg: dict, enriched: pd.DataFrame, weight: pd.DataFrame, vo2: pd.DataFrame,
                  today: date, garmin_first_day: date, archive: dict | None = None) -> tuple[dict, dict]:
    c = cfg["cycling"]
    rides = enriched[enriched["sport"] == "cycling"].copy()
    lifting = enriched[enriched["sub_sport"] == "strength_training"]
    rides["hours"] = rides["duration_s"] / 3600
    rides["km"] = rides["distance_m"].fillna(0) / 1000
    non_e = rides[rides["ride_type"] != "ebike"]
    # history starts at the first ride (Strava archive), the weekly budget shared with strength at the first Garmin day
    first_day = min(garmin_first_day, rides["date"].min().date())
    t0, t1 = pd.Timestamp(first_day), pd.Timestamp(today)
    z1, z2 = cyc.hr_zone_bounds(cfg)

    # -- 1 fitness / fatigue / form. Load = TRIMP, or TSS-equivalent once a real FTP is configured (7.3):
    #    with ftp_w set and >= 10 rides having both TSS and TRIMP, fit TSS ~ k * TRIMP (least squares through 0),
    #    use TSS where power exists and k * TRIMP elsewhere. A lower-bound eFTP is never used for TSS.
    power_rides = rides[rides["has_power_stream"] & rides["np"].notna() & rides["trimp"].notna()]
    ftp_cfg = cfg["athlete"].get("ftp_w")
    load_unit, scale = "TRIMP", None
    rides["load"] = rides["trimp"]
    if ftp_cfg and len(power_rides) >= 10:
        tss_vals = power_rides.apply(lambda r: cyc.tss(r.duration_s, r.np, ftp_cfg), axis=1)
        scale = float((tss_vals * power_rides["trimp"]).sum() / (power_rides["trimp"] ** 2).sum())
        rides["load"] = rides["trimp"] * scale
        rides.loc[power_rides.index, "load"] = tss_vals
        load_unit = "TSS-equivalent"
    non_e = rides[rides["ride_type"] != "ebike"]

    def ff(df):
        load = df.groupby("date")["load"].sum()
        load = load.reindex(pd.date_range(t0, t1, freq="D"), fill_value=0.0)
        return common.fitness_fatigue(load)
    ff_ex, ff_in = ff(non_e), ff(rides)
    warmup_end = (t0 + pd.Timedelta(days=42)).date()
    fitness = {
        "unit": load_unit, "scale": num(scale, 3), "warmup_end": warmup_end,
        "quality": {k: int(v) for k, v in non_e["load_quality"].value_counts().items()},
        "variants": {k: {"ctl": pts(v["ctl"]), "atl": pts(v["atl"]), "tsb": pts(v["tsb"]), "load": pts(v["load"], 1)}
                     for k, v in (("excl_ebike", ff_ex), ("incl_ebike", ff_in))},
    }

    # -- 2 weekly volume by ride type
    weeks = weekly_index(first_day, today)
    types = ["commute", "long", "indoor", "other", "ebike"]
    vol = {}
    for metric, col in (("hours", "hours"), ("km", "km"), ("load", "load")):
        g = rides.groupby(["week", "ride_type"])[col].sum().unstack(fill_value=0).reindex(weeks, fill_value=0)
        vol[metric] = {t: [num(x, 2) for x in g[t]] if t in g else [0] * len(weeks) for t in types}
    volume = {"weeks": [w.date() for w in weeks], "types": types, "series": vol,
              "current_week": common.week_start(today)}

    # -- 2b weekly effort range (plan B): rides only, same load series as the fitness chart, e-bike excluded
    effort = cyc.effort_range(non_e.groupby("week")["load"].sum().reindex(weeks, fill_value=0.0), today)
    effort_payload = {"unit": load_unit, "band": list(cyc.EFFORT_BAND), "flag": effort["flag"],
                      "weeks": [{k: (num(v, 1) if k != "week" else v) for k, v in w.items()} for w in effort["weeks"]],
                      "current": ({k: (num(v, 1) if k != "week" else v) for k, v in effort["current"].items()}
                                  if effort["current"] else None)}

    # -- 2c matched commute (plan A): same endpoints, same direction, distance within 10 % of the group median
    labels = tuple(c.get("commute_labels") or ("A", "B"))
    gps = non_e[(non_e["ride_type"] != "indoor") & non_e["start_lat"].notna()]
    groups = cyc.match_routes(gps, labels, centers=cyc.current_endpoints(rides, garmin_first_day))
    matched = {"groups": [{**g, "km": num(g["km"], 1),
                           "rides": [{k: (num(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in g["rides"]],
                           "trend": {k: (num(v, 2) if isinstance(v, float) else v) for k, v in g["trend"].items()}}
                          for g in groups],
               "n_strap": int(sum(r["strap"] for g in groups for r in g["rides"]))}

    # -- 2d best efforts (plan C): outdoor rides, e-bike excluded; time to cover 1/5/10/20 km within one ride
    outdoor = non_e[(non_e["ride_type"] != "indoor") & non_e["has_gps_stream"]].sort_values("start_ts_local")
    dists = [d // 1000 for d in cyc.BEST_DISTANCES_M]
    prs, progression, top3 = {}, {}, {}
    def _eff(r, d):
        t = getattr(r, f"be_{d}")
        return {"time_s": num(t, 1), "kmh": num(d / (t / 3600), 1), "date": r.date.date(), "activity_id": int(r.activity_id),
                "name": r.name}
    for d in dists:
        col = f"be_{d}"
        have = outdoor[outdoor[col].notna()] if col in outdoor else outdoor.iloc[0:0]
        ranked = have.sort_values(col)
        top3[str(d)] = [_eff(r, d) for r in ranked.head(3).itertuples()]
        prs[str(d)] = top3[str(d)][0] if len(ranked) else None
        best, prog = float("inf"), []
        for r in have.itertuples():
            if getattr(r, col) < best:
                best = getattr(r, col)
                prog.append([r.date.date(), num(best, 1)])
        progression[str(d)] = prog
        if prs[str(d)]:
            prs[str(d)]["new_best"] = (today - prs[str(d)]["date"]).days <= 7
        prs[str(d)] and prs[str(d)].update({"n_rides": int(len(have))})
    longest = outdoor.sort_values("distance_m", ascending=False).head(1)
    longest_t = outdoor.sort_values("moving_s", ascending=False).head(1)
    climb = outdoor.sort_values("elev_gain_m", ascending=False).head(1)
    def _rec(df, key, f):
        return None if df.empty or pd.isna(df.iloc[0][key]) else {"value": f(df.iloc[0][key]), "date": df.iloc[0]["date"].date(),
                                                                  "activity_id": int(df.iloc[0]["activity_id"]), "name": df.iloc[0]["name"]}
    best_efforts = {"distances": dists, "prs": prs, "progression": progression, "top3": top3, "n_rides": int(len(outdoor)),
                    "records": {"longest_km": _rec(longest, "distance_m", lambda v: num(v / 1000, 1)),
                                "longest_min": _rec(longest_t, "moving_s", lambda v: num(v / 60, 0)),
                                "climb_m": _rec(climb, "elev_gain_m", lambda v: num(v, 0))},
                    "sample_interval_s": num(float(outdoor["sample_interval_s"].median()), 1) if len(outdoor) else None}

    # -- 2e personal segments (plan D): efforts come from the per-ride stream metrics (config segments)
    seg_payload = []
    by_id = outdoor.set_index("activity_id")
    for i, sg in enumerate(c.get("segments") or []):
        cand = {aid: r.segs[i] for aid, r in by_id.iterrows() if isinstance(r.segs, list) and len(r.segs) > i and r.segs[i]}
        kept, med = cyc.filter_efforts(cand)
        efforts = []
        for aid, e in kept.items():
            r = by_id.loc[aid]
            efforts.append({"date": r["date"].date(), "activity_id": int(aid), "time_s": num(e["time_s"], 1),
                            "kmh": num(e["dist_m"] / e["time_s"] * 3.6, 1),
                            "avg_hr": num(e["avg_hr"], 0) if r["hr_quality"] == "strap" else None,
                            "power": num(e["avg_power"], 0), "gain_m": num(e["gain_m"], 0)})
        efforts.sort(key=lambda e: e["date"])
        pr = min(efforts, key=lambda e: e["time_s"]) if efforts else None
        gains = [e["gain_m"] for e in efforts if e["gain_m"] is not None]
        seg_payload.append({"name": sg["name"], "km": num((med or 0) / 1000, 2), "gain_m": num(float(np.median(gains)), 0) if gains else None,
                            "n": len(efforts), "efforts": efforts, "pr": pr, "latest": efforts[-1] if efforts else None,
                            "median_s": num(float(np.median([e["time_s"] for e in efforts])), 1) if efforts else None,
                            "n_strap": sum(e["avg_hr"] is not None for e in efforts)})

    # -- 3 intensity distribution (strap rides only by decision)
    src_rides = rides[rides["hr_quality"] == "strap"] if c.get("intensity_strap_only", True) else non_e
    src_rides = src_rides[src_rides["ride_type"] != "ebike"]
    zw = src_rides.groupby("week")[["z1_s", "z2_s", "z3_s"]].sum().reindex(weeks, fill_value=0)
    tot = zw.sum(axis=1).replace(0, np.nan)
    last28 = src_rides[src_rides["date"] > t1 - pd.Timedelta(days=28)][["z1_s", "z2_s", "z3_s"]].sum()
    all_t = src_rides[["z1_s", "z2_s", "z3_s"]].sum()
    def split(s):
        t = s.sum()
        return [num(100 * s[k] / t, 1) if t else None for k in ("z1_s", "z2_s", "z3_s")]
    intensity = {
        "weeks": [w.date() for w in weeks],
        "pct": {z: [num(v, 1) for v in (100 * zw[f"{z}_s"] / tot).tolist()] for z in ("z1", "z2", "z3")},
        "hours": [num(v / 3600, 2) for v in zw.sum(axis=1).tolist()],
        "last28": split(last28), "last28_hours": num(last28.sum() / 3600, 1),
        "all_time": split(all_t), "n_rides": int(len(src_rides)),
        "reference": [80, 5, 15], "bounds": [round(z1), round(z2)],
        "source": "chest-strap rides only" if c.get("intensity_strap_only", True) else "all non-e-bike rides",
    }

    # -- 4 aerobic efficiency (steady rides)
    steady = non_e[(non_e["ride_type"] != "indoor") & (non_e["moving_s"] >= 40 * 60)
                   & (non_e["stopped_share"].fillna(1) < 0.15) & non_e["m_per_beat"].notna()].sort_values("date")
    roll = steady.set_index("date")["m_per_beat"].rolling("42D", min_periods=3).median()
    pw_steady = rides[rides["power_per_hr"].notna() & (rides["moving_s"] >= 40 * 60)].sort_values("date")
    efficiency = {
        "speed": [{"date": r.date.date(), "v": num(r.m_per_beat, 3), "id": r.activity_id, "name": r.name,
                   "hr": r.hr_quality} for r in steady.itertuples()],
        "speed_median": pts(roll, 3),
        "power": [{"date": r.date.date(), "v": num(r.power_per_hr, 3), "id": r.activity_id} for r in pw_steady.itertuples()],
        "n": int(len(steady)),
    }

    # -- 5 long rides and decoupling
    long_min = c["long_ride_min_minutes"]
    lr = non_e[non_e["moving_s"].fillna(non_e["duration_s"]) >= long_min * 60].sort_values("date", ascending=False)
    dec = non_e[non_e["dec_decoupling_pct"].notna() & (non_e["moving_s"] >= 90 * 60)].sort_values("date")
    long_rides = {
        "min_minutes": long_min,
        "table": [{"date": r.date.date(), "name": r.name, "min": num(r.moving_s / 60, 0), "km": num(r.km, 1),
                   "elev": num(r.elev_gain_m, 0), "avg_hr": num(r.avg_hr, 0), "hr_quality": r.hr_quality,
                   "decoupling": num(r.dec_decoupling_pct, 1), "dec_method": r.dec_method,
                   "cadence": num(r.avg_cadence, 0), "np": num(r.np, 0)} for r in lr.itertuples()],
        "decoupling": [{"date": r.date.date(), "v": num(r.dec_decoupling_pct, 1), "method": r.dec_method,
                        "min": num(r.moving_s / 60, 0), "name": r.name} for r in dec.itertuples()],
        "days_since_long": (today - lr["date"].max().date()).days if len(lr) else None,
    }

    # -- 6 commute sprints (speed-based: no power on commutes)
    com = rides[rides["ride_type"] == "commute"]
    com_power = com[com["has_power_stream"]]
    sp = com.groupby("week")["sprints_speed"].sum().reindex(weeks, fill_value=0)
    n_com = com.groupby("week").size().reindex(weeks, fill_value=0)
    median_interval = float(com["sample_interval_s"].median()) if len(com) else None
    sprints = {
        "enabled": len(com) > 0,
        "method": "power" if len(com_power) >= 5 else "speed acceleration (experimental)",
        "sample_interval_s": num(median_interval, 1),
        "weeks": [w.date() for w in weeks], "count": [int(x) for x in sp], "commutes": [int(x) for x in n_com],
        "rule": (f"+{c['sprint']['speed_gain_kmh']} km/h within 10 s from below {c['sprint']['speed_start_max_kmh']} km/h, "
                 f"peaking ≥ {c['sprint']['speed_peak_min_kmh']} km/h"),
        "n_commutes": int(len(com)),
    }

    # -- 7 power
    pr = rides[rides["has_power_stream"]].sort_values("date")
    durations = cyc.MEAN_MAX_DURATIONS
    def envelope(df):
        return [[d, num(df[f"mm_{d}"].max(), 0)] for d in durations if f"mm_{d}" in df and df[f"mm_{d}"].notna().any()]
    best20 = pr["mm_1200"] if "mm_1200" in pr else pd.Series(dtype=float)
    eftp = cyc.eftp_series(pr["date"], best20) if len(pr) else pd.Series(dtype=float)
    recent = pr[pr["date"] > t1 - pd.Timedelta(days=90)]
    eftp_now = 0.95 * recent["mm_1200"].max() if len(recent) and "mm_1200" in recent and recent["mm_1200"].notna().any() else None
    eftp_basis = "last 90 days"
    if eftp_now is None and len(eftp):
        eftp_now, eftp_basis = float(eftp.iloc[-1]), f"last power ride ({eftp.index[-1].date()})"
    w_now = float(weight.sort_values("date")["weight_kg"].iloc[-1]) if len(weight) else cfg["athlete"]["bodyweight_fallback_kg"]
    best_rows = []
    for d, label in ((5, "5 s"), (60, "1 min"), (300, "5 min"), (1200, "20 min"), (3600, "60 min")):
        col = f"mm_{d}"
        if col in pr and pr[col].notna().any():
            i = pr[col].idxmax()
            best_rows.append({"label": label, "w": num(pr.loc[i, col], 0), "date": pr.loc[i, "date"].date(),
                              "wkg": num(pr.loc[i, col] / w_now, 2)})
    power = {
        "enabled": len(pr) >= c.get("min_power_rides", 5), "n_rides": int(len(pr)),
        "curves": {"42d": envelope(pr[pr["date"] > t1 - pd.Timedelta(days=42)]),
                   "90d": envelope(pr[pr["date"] > t1 - pd.Timedelta(days=90)]), "all": envelope(pr)},
        "eftp": pts(eftp, 0), "eftp_now": num(eftp_now, 0), "eftp_basis": eftp_basis,
        "eftp_lower_bound": bool(c.get("eftp_is_lower_bound", True)), "ftp_goal": cfg["athlete"]["ftp_goal_w"],
        "ftp_config": cfg["athlete"].get("ftp_w"), "weight_kg": num(w_now, 1),
        "wkg_now": num(eftp_now / w_now, 2) if eftp_now else None, "best": best_rows,
        "rides": [{"date": r.date.date(), "name": r.name, "sub_sport": r.sub_sport, "np": num(r.np, 0),
                   "avg": num(r.avg_power, 0), "min": num(r.duration_s / 60, 0), "sensor": r.power_sensor}
                  for r in pr.itertuples()],
    }

    # -- 8 consistency calendar (12 months, daily load excl. e-bike)
    cal_start = t1 - pd.Timedelta(days=364)
    cal = ff_ex["load"][ff_ex.index >= cal_start]
    calendar = {"start": cal_start.date(), "end": today, "load": pts(cal, 0),
                "rides": non_e[non_e["date"] >= cal_start].groupby("date").size().pipe(lambda s: pts(s.astype(float), 0))}

    # -- 9 year over year (cumulative hours by day of year, excl. e-bike)
    yoy = {}
    for y, g in non_e.groupby(non_e["date"].dt.year):
        if y < today.year - 3:
            continue  # the page draws the last four years; older ones are in the volume chart
        daily_h = g.groupby(g["date"].dt.dayofyear)["hours"].sum()
        last_doy = 366 if y < today.year else today.timetuple().tm_yday
        cum = daily_h.reindex(range(1, last_doy + 1), fill_value=0).cumsum()
        cum_km = g.groupby(g["date"].dt.dayofyear)["km"].sum().reindex(range(1, last_doy + 1), fill_value=0).cumsum()
        yoy[str(y)] = {"hours": [[int(d), num(v, 1)] for d, v in cum.items()],
                       "km": [[int(d), num(v, 0)] for d, v in cum_km.items()]}

    # -- 10 weekly time budget (shared with strength; Overview chart): cycling and e-bike as separate series,
    #    so all riding time is shown while the e-bike stays apart from fitness riding.
    bweeks = weekly_index(garmin_first_day, today)
    ebike = rides[rides["ride_type"] == "ebike"]
    budget = {"weeks": [w.date() for w in bweeks],
              "cycling": [num(x, 2) for x in non_e.groupby("week")["hours"].sum().reindex(bweeks, fill_value=0)],
              "ebike": [num(x, 2) for x in ebike.groupby("week")["hours"].sum().reindex(bweeks, fill_value=0)],
              "lifting": [num(x, 2) for x in (lifting.groupby("week")["duration_s"].sum() / 3600).reindex(bweeks, fill_value=0)],
              "current_week": common.week_start(today)}

    # -- KPIs
    def window(df, days, offset=0):
        return df[(df["date"] > t1 - pd.Timedelta(days=days + offset)) & (df["date"] <= t1 - pd.Timedelta(days=offset))]
    h7, h7p = window(non_e, 7)["hours"].sum(), window(non_e, 7, 7)["hours"].sum()
    r28, r28p = len(window(non_e, 28)), len(window(non_e, 28, 28))
    ctl_now = ff_ex["ctl"].iloc[-1] if len(ff_ex) else None
    ctl_prev = ff_ex["ctl"].iloc[-8] if len(ff_ex) > 8 else None
    daily_h = non_e.groupby("date")["hours"].sum()
    kpis = [
        tile("Hours, last 7 days", h7, "h", h7 - h7p, "vs previous 7 days", weekly_spark(daily_h), "up"),
        tile("Fitness (CTL)", ctl_now, "", (ctl_now - ctl_prev) if ctl_prev is not None else None, "vs 7 days ago",
             [num(v, 1) for v in ff_ex["ctl"].iloc[-84::7].tolist()], "up", note=f"{load_unit} units, e-bike excluded"),
        tile("Rides, last 28 days", r28, "", r28 - r28p, "vs previous 28 days", weekly_spark(non_e.groupby("date").size().astype(float)), "up", 0,
             note="e-bike excluded"),
        tile("Days since long ride", long_rides["days_since_long"], "d", None, "", None, "down", 0,
             note=f"≥ {long_min} min moving"),
    ]
    payload = {"kpis": kpis, "fitness": fitness, "volume": volume, "effort_range": effort_payload, "matched": matched, "best_efforts": best_efforts, "segments": seg_payload, "intensity": intensity, "efficiency": efficiency,
               "long_rides": long_rides, "sprints": sprints, "power": power, "calendar": calendar, "yoy": yoy,
               "history": {"from": first_day, "garmin_from": garmin_first_day, **(archive or {}),
                           "n_before_garmin": int((rides["date"] < pd.Timestamp(garmin_first_day)).sum())},
               "counts": {"rides": int(len(rides)), "ebike": int((rides["ride_type"] == "ebike").sum()),
                          "by_type": {k: int(v) for k, v in rides["ride_type"].value_counts().items()},
                          "strap": int((rides["hr_quality"] == "strap").sum())}}
    summary = {"ctl": num(ctl_now, 1), "ctl_prev": num(ctl_prev, 1), "hours7": num(h7, 1), "eftp": num(eftp_now, 0),
               "hours_week_prev4": num(window(non_e, 28, 7)["hours"].sum() / 4, 2),
               "days_since_long": long_rides["days_since_long"], "long_min": long_min, "effort_flag": effort["flag"]}
    weekly = pd.DataFrame({"hours": non_e.groupby("week")["hours"].sum(), "rides": non_e.groupby("week").size(),
                           "load": non_e.groupby("week")["load"].sum()})
    return payload, {"budget": budget, "summary": summary, "weekly": weekly}


# =========================================================================== weekly overview

def build_weekly(today: date, cyc: pd.DataFrame, lift: pd.DataFrame, health: pd.DataFrame, weeks: int = 12) -> dict:
    """The Overview's 12-week table: one row per ISO week (oldest first), riding, lifting and body side by side.
    Body values are the mean of the days that have a reading; a week with none is None, never 0."""
    idx = weekly_index(today - timedelta(weeks=weeks - 1), today)
    h = health.copy()
    h.index = pd.to_datetime(h.index)
    h["week"] = [pd.Timestamp(common.week_start(d)) for d in h.index]
    body = h.groupby("week")[["hrv", "rhr", "sleep_h", "sleep_score", "stress"]].mean()
    keys = ("hrv", "rhr", "sleep_h", "sleep_score", "stress")
    rows = []
    for w in idx:
        c = cyc.loc[w] if w in cyc.index else None
        l = lift.loc[w] if w in lift.index else None
        b = body.loc[w] if w in body.index else None
        row = {"week": w.date(), "current": w == pd.Timestamp(common.week_start(today)),
               "cycling_h": num(c["hours"], 1) if c is not None else 0.0, "rides": int(c["rides"]) if c is not None else 0,
               "load": num(c["load"], 0) if c is not None else 0,
               "sessions": int(l["sessions"]) if l is not None and not pd.isna(l["sessions"]) else 0,
               "hard_sets": int(l["hard_sets"]) if l is not None and not pd.isna(l["hard_sets"]) else 0}
        for k in keys:
            row[k] = num(b[k], {"sleep_h": 2, "hrv": 1, "rhr": 1}.get(k, 0)) if b is not None else None
        rows.append(row)
    return {"weeks": rows, "load_unit": "TRIMP"}


# =========================================================================== map

def build_map(enriched: pd.DataFrame, meta: dict) -> dict:
    """Heatmap payload: every outdoor ride's simplified, privacy-trimmed line (first/last 200 m dropped), e-bike included
    with a type so the page can toggle it. Indoor and virtual rides have no real route."""
    r = enriched[(enriched["sport"] == "cycling") & (enriched["ride_type"] != "indoor")
                 & enriched["track"].map(lambda t: isinstance(t, list) and len(t) > 0)].sort_values("start_ts_local")
    rides = [{"d": row.date.date(), "t": row.ride_type, "k": num((row.distance_m or 0) / 1000, 1), "p": row.track}
             for row in r.itertuples()]
    return {"meta": meta, "rides": rides, "trim_m": cyc.HEAT_TRIM_M, "tol_m": cyc.HEAT_TOL_M,
            "types": ["commute", "long", "other", "ebike"], "n_indoor_skipped": int((enriched["ride_type"] == "indoor").sum())}


# =========================================================================== strength

def build_strength(cfg: dict, enriched: pd.DataFrame, sets: pd.DataFrame, weight: pd.DataFrame,
                   today: date, first_day: date) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame]:
    scfg = cfg["strength"]
    emap = st.load_exercise_map()
    bw = pd.Series(weight["weight_kg"].values, index=pd.to_datetime(weight["date"])) if len(weight) else pd.Series(dtype=float)
    s = st.prepare_sets(sets, emap, bw, cfg["athlete"]["bodyweight_fallback_kg"])
    mr, unmapped = st.muscle_rows(s, emap)
    for e in unmapped:
        log(f"WARNING unmapped exercise: {e!r} (add it to src/exercise_map.yaml; see make review-exercises)")
    t1 = pd.Timestamp(today)
    regions = emap["regions"]
    region_keys = list(regions)
    weeks = weekly_index(first_day, today)

    # sessions: every Garmin strength activity + manual sessions (all manual ones are linked)
    garmin_sess = enriched[enriched["sub_sport"] == "strength_training"].copy()
    with src.connect() as con:
        mw = pd.read_sql_query("SELECT workout_id, workout_date, title, comments, garmin_activity_id FROM manual_workouts", con) \
            if src._has_table("manual_workouts") else pd.DataFrame(columns=["workout_id", "workout_date", "title", "comments", "garmin_activity_id"])
    mw["workout_date"] = pd.to_datetime(mw["workout_date"])
    stypes = st.session_types(mr, emap) if len(mr) else pd.Series(dtype=object)
    sess_rows = []
    # A note session can own several Garmin activities (watch stopped and restarted): all of them count as logged
    # and their duration / HR / TRIMP are combined into the one session.
    links = src.load_strength_links()
    linked = set(links["activity_id"].dropna().astype("int64"))
    gps = st.garmin_per_session(garmin_sess, links)
    for w in mw.itertuples():
        g = gps.loc[w.workout_id] if w.workout_id in gps.index else None
        ws = s[(s["session"] == w.workout_id) & s["working"]]
        dur = g["duration_s"] if g is not None else None
        sess_rows.append({"session": w.workout_id, "date": w.workout_date, "type": stypes.get(w.workout_id, "other"),
                          "title": w.title, "logged": True, "duration_min": num(dur / 60, 0) if dur else None,
                          "avg_hr": num(g["avg_hr"], 0) if g is not None else None,
                          "trimp": num(g["trimp"], 0) if g is not None else None,
                          "sets": int(len(ws)), "tonnage": num(ws["set_load"].sum(), 0),
                          "sets_per_hour": num(len(ws) / (dur / 3600), 1) if dur else None})
    for g in garmin_sess[~garmin_sess["activity_id"].isin(linked)].itertuples():
        if (g.duration_s or 0) < 5 * 60:
            continue  # false starts
        sess_rows.append({"session": int(g.activity_id), "date": g.date, "type": "unlogged", "title": None,
                          "logged": False, "duration_min": num(g.duration_s / 60, 0), "avg_hr": num(g.avg_hr, 0),
                          "trimp": num(g.trimp, 0), "sets": None, "tonnage": None, "sets_per_hour": None})
    sessions = pd.DataFrame(sess_rows).sort_values("date")
    sessions["week"] = sessions["date"].map(lambda d: pd.Timestamp(common.week_start(d)))

    # -- 1 session calendar
    calendar = [{"date": r.date.date(), "type": r.type, "title": r.title, "min": r.duration_min, "sets": r.sets}
                for r in sessions.itertuples()]

    # -- per-session bests, e1RM small multiples
    sb = st.session_best(s)
    top_ex = s[s["working"]].groupby("exercise").size().sort_values(ascending=False).head(6).index.tolist()
    e1rm_series = {}
    for ex in sorted(sb["exercise"].unique()):
        g = sb[sb["exercise"] == ex].sort_values("date")
        roll = g["e1rm"].rolling(3, min_periods=1).max()
        e1rm_series[ex] = {"points": [[r.date.date(), num(r.e1rm, 1), num(r.top_weight, 1), int(r.top_reps)]
                                      for r in g.itertuples() if not pd.isna(r.e1rm)],
                           "best3": [[d.date(), num(v, 1)] for d, v in zip(g["date"], roll) if not pd.isna(v)],
                           "bodyweight": bool(g["bodyweight_lift"].iloc[0])}

    # -- 3 weekly hard sets per muscle (working sets, primary 1 / secondary 0.5)
    mr["week"] = mr["date"].map(lambda d: pd.Timestamp(common.week_start(d)))
    wk = mr.groupby(["week", "muscle"])["w"].sum().unstack(fill_value=0).reindex(weeks, fill_value=0)
    weekly_sets = {"weeks": [w.date() for w in weeks], "regions": region_keys,
                   "labels": {k: v["label"] for k, v in regions.items()},
                   "values": {m: [num(x, 1) for x in (wk[m] if m in wk else [0] * len(weeks))] for m in region_keys},
                   "band": scfg["weekly_sets_band"], "current_week": common.week_start(today),
                   "logged_from": mw["workout_date"].min().date() if len(mw) else None}
    by_ex_week = mr.groupby(["week", "exercise", "muscle"])["w"].sum().reset_index()
    weekly_sets["by_exercise"] = {
        ex: {m: [[r.week.date(), num(r.w, 1)] for r in g.itertuples()] for m, g in grp.groupby("muscle")}
        for ex, grp in by_ex_week.groupby("exercise")}
    leg_keys = [k for k, v in regions.items() if v["group"] == "legs"]
    legs_8w = mr[(mr["muscle"].isin(leg_keys)) & (mr["date"] > t1 - pd.Timedelta(weeks=8))]["w"].sum()
    legs_note = None
    if legs_8w == 0:
        legs_note = "leg training not started"
    else:
        first_leg = mr[mr["muscle"].isin(leg_keys) & (mr["date"] > t1 - pd.Timedelta(weeks=8))]["date"].min()
        prior = mr[mr["muscle"].isin(leg_keys) & (mr["date"] < first_leg) & (mr["date"] > first_leg - pd.Timedelta(weeks=8))]
        if prior.empty:
            legs_note = f"leg training restarted {first_leg.date()}"

    # -- 4 push / pull balance (4-week rolling sets)
    patt = {k: v.get("pattern") for k, v in emap["exercises"].items()}
    ws = s[s["working"]].assign(pattern=lambda d: d["exercise"].map(patt))
    ws["week"] = ws["date"].map(lambda d: pd.Timestamp(common.week_start(d)))
    push = ws[ws["pattern"].isin(["horizontal_push", "vertical_push"])].groupby("week").size().reindex(weeks, fill_value=0)
    pull = ws[ws["pattern"].isin(["horizontal_pull", "vertical_pull"])].groupby("week").size().reindex(weeks, fill_value=0)
    ratio = push.rolling(4, min_periods=1).sum() / pull.rolling(4, min_periods=1).sum().replace(0, np.nan)
    has_any = (push + pull).rolling(4, min_periods=1).sum() > 0
    balance = {"weeks": [w.date() for w in weeks], "ratio": [num(v, 2) if h else None for v, h in zip(ratio, has_any)],
               "push": [int(x) for x in push], "pull": [int(x) for x in pull]}

    # -- 5 progression table
    prog = []
    for ex, g in sb.groupby("exercise"):
        g = g.sort_values("date")
        last4 = g.tail(4)
        cur = g["e1rm"].iloc[-1]
        before = g[g["date"] <= t1 - pd.Timedelta(days=28)]
        ref = before["e1rm"].iloc[-1] if len(before) else np.nan
        status = st.progression_status(g["trend_e1rm"].tolist())
        if len(g) >= 2 and (g["date"].iloc[-1] - g["date"].iloc[-2]).days > 42:
            status = "restart"  # first session after > 6 weeks off: a trend across the break means nothing
        if pd.isna(cur) and g["e1rm"].isna().all():
            status = "no e1RM"
        prog.append({
            "exercise": ex, "status": status, "sessions": int(len(g)), "last_date": g["date"].iloc[-1].date(),
            "last4": [{"date": r.date.date(), "weight": num(r.top_weight, 1), "reps": int(r.top_reps),
                       "implements": None if pd.isna(r.implements) else int(r.implements),
                       "bw": bool(r.bodyweight_lift)} for r in last4.itertuples()],
            "e1rm": num(cur, 1), "delta_4w": num(cur - ref, 1) if not pd.isna(ref) and not pd.isna(cur) else None,
            "muscles": list((emap["exercises"].get(ex) or {}).get("muscles", {}).keys()),
        })
    order = {"regressing": 0, "flat": 1, "restart": 2, "progressing": 3, "new": 4, "no e1RM": 5}
    prog = sorted(prog, key=lambda r: (order.get(r["status"], 9), -pd.Timestamp(r["last_date"]).value))

    # -- 6 PR board
    prs = []
    w = s[s["working"]]
    for ex, g in w.groupby("exercise"):
        def best(gg):
            if gg.empty:
                return {}
            hw = gg.loc[gg["eff_weight"].idxmax()] if gg["eff_weight"].notna().any() else None
            be = gg.loc[gg["e1rm"].idxmax()] if gg["e1rm"].notna().any() else None
            mr_ = None
            if hw is not None:
                at = gg[gg["eff_weight"] == hw["eff_weight"]]
                mr_ = at.loc[at["reps"].idxmax()]
            return {"heaviest": num(hw["weight_kg"], 1) if hw is not None else None,
                    "heaviest_date": hw["date"].date() if hw is not None else None,
                    "e1rm": num(be["e1rm"], 1) if be is not None else None,
                    "e1rm_date": be["date"].date() if be is not None else None,
                    "reps_at_heaviest": int(mr_["reps"]) if mr_ is not None else None}
        prs.append({"exercise": ex, "bodyweight": bool(g["bodyweight_lift"].iloc[0]),
                    "all": best(g), "d90": best(g[g["date"] > t1 - pd.Timedelta(days=90)])})

    # -- 7 session load & density, weekly tonnage
    tonnage = ws.groupby("week")["set_load"].sum().reindex(weeks, fill_value=0)
    density = {"sessions": [{"date": r.date.date(), "type": r.type, "min": r.duration_min, "avg_hr": r.avg_hr,
                             "sets": r.sets, "sets_per_hour": r.sets_per_hour, "tonnage": r.tonnage}
                            for r in sessions.itertuples()],
               "weeks": [w.date() for w in weeks], "tonnage": [num(x, 0) for x in tonnage]}

    # -- 9 bodyweight
    bodyweight = [[r.date, num(r.weight_kg, 1)] for r in weight.sort_values("date").itertuples()]

    # -- 10 muscle map
    def per_region(series):
        return {m: num(series.get(m, 0.0), 2) for m in region_keys}
    last_sessions = sessions[sessions["logged"]].tail(3)
    round_start = last_sessions["date"].min() if len(last_sessions) else t1
    vol7 = st.weighted_sets(mr, t1 - pd.Timedelta(days=6), t1)
    vol28 = st.weighted_sets(mr, t1 - pd.Timedelta(days=27), t1) / 4
    volround = st.weighted_sets(mr, round_start, t1)
    load7 = mr[mr["date"] > t1 - pd.Timedelta(days=7)].groupby("muscle")["load"].sum()
    wk_load = mr[mr["date"] > t1 - pd.Timedelta(weeks=8)].groupby(["muscle", "week"])["load"].sum().groupby("muscle").max()
    rec = st.recency(mr, t1, scfg.get("recency_half_life_days", 3))
    typ = st.typical_session_value(mr, t1)
    last_trained = mr[mr["w"] >= 1.0].groupby("muscle")["date"].max()  # primary work only (pullovers don't "train" chest)
    top3 = {}
    for m, g in mr[mr["date"] > t1 - pd.Timedelta(days=27)].groupby("muscle"):
        top3[m] = [[e, num(v, 1)] for e, v in g.groupby("exercise")["w"].sum().sort_values(ascending=False).head(3).items()]
    tonnage28 = mr[mr["date"] > t1 - pd.Timedelta(days=27)].groupby("muscle")["load"].sum()
    muscle_map = {
        "regions": {k: {"label": v["label"], "group": v["group"], "view": v["view"]} for k, v in regions.items()},
        "volume": {"7d": per_region(vol7), "28d": per_region(vol28), "round": per_region(volround)},
        "round_start": round_start.date() if hasattr(round_start, "date") else round_start,
        "load": {m: num((load7.get(m, 0.0) / wk_load[m]) if m in wk_load and wk_load[m] else 0.0, 3) for m in region_keys},
        "recency": {m: num(rec.get(m, 0.0) / typ[m], 3) if m in typ and typ[m] else num(0.0 if m not in rec else min(rec[m], 1.0), 3)
                    for m in region_keys},
        "last_trained": {m: last_trained[m].date() for m in last_trained.index},
        "top_exercises": top3, "tonnage_28d": {m: num(v, 0) for m, v in tonnage28.items()},
        "band": scfg["weekly_sets_band"], "legs_note": legs_note,
        "exercise_muscles": {ex: (v.get("muscles") or {}) for ex, v in emap["exercises"].items()},
    }

    # -- KPIs
    this_week = pd.Timestamp(common.week_start(today))
    sess_this_week = sessions[sessions["week"] == this_week]
    logged = sessions[sessions["logged"]]
    split = scfg["split"]
    streak = 0
    wk_cursor = this_week
    types_by_week = logged.groupby("week")["type"].apply(set)
    if not set(split) <= types_by_week.get(this_week, set()):
        wk_cursor = this_week - pd.Timedelta(days=7)  # current week may still complete
    while set(split) <= types_by_week.get(wk_cursor, set()):
        streak += 1
        wk_cursor -= pd.Timedelta(days=7)
    hard7 = int(len(w[w["date"] > t1 - pd.Timedelta(days=7)]))
    hard7p = int(len(w[(w["date"] > t1 - pd.Timedelta(days=14)) & (w["date"] <= t1 - pd.Timedelta(days=7))]))
    new_best = 0
    for ex, g in sb.groupby("exercise"):
        recent = g[g["date"] > t1 - pd.Timedelta(days=28)]["e1rm"].max()
        before = g[g["date"] <= t1 - pd.Timedelta(days=28)]["e1rm"].max()
        if not pd.isna(recent) and (pd.isna(before) or recent > before):
            new_best += 1
    last_sess = sessions["date"].max()
    sets_daily = w.groupby("date").size().astype(float)
    kpis = [
        tile("Sessions this week", len(sess_this_week), f"/ {2 * len(split)}", None,
             f"{len(split)} = one round · {' + '.join(split)}" + (f" · {streak} week{'s' if streak != 1 else ''} in a row with a full round" if streak else ""),
             weekly_spark(sessions.groupby("date").size().astype(float)), "up", 0),
        tile("Hard sets, last 7 days", hard7, "", hard7 - hard7p, "vs previous 7 days", weekly_spark(sets_daily), "up", 0),
        tile("Lifts with a new e1RM best", new_best, "", None, "last 28 days", None, "up", 0),
        tile("Days since last session", (today - last_sess.date()).days if pd.notna(last_sess) else None, "d",
             None, "", None, "down", 0),
    ]
    payload = {"kpis": kpis, "calendar": calendar, "e1rm": e1rm_series, "top_exercises": top_ex,
               "weekly_sets": weekly_sets, "balance": balance, "progression": prog, "prs": prs, "density": density,
               "bodyweight": bodyweight, "muscle_map": muscle_map, "unmapped": unmapped,
               "legs_note": legs_note, "split": split,
               "notes": {"unlogged_sessions": int((~sessions["logged"]).sum()),
                         "dumbbell_convention": "Dumbbell weights are per hand (not doubled).",
                         "bodyweight_kg": num(bw.iloc[-1] if len(bw) else cfg["athlete"]["bodyweight_fallback_kg"], 1)}}
    type_counts = logged[logged["week"] == this_week]["type"].value_counts()
    rounds_week = int(min(type_counts.get(t, 0) for t in split)) if split else 0
    last_by_type = {t: (logged[logged["type"] == t]["date"].max().date() if (logged["type"] == t).any() else None)
                    for t in split}
    summary = {"sessions_week": int(len(sess_this_week)), "hard_sets7": hard7, "streak": streak,
               "rounds_week": rounds_week, "split": split, "last_by_type": last_by_type,
               "flat_lifts": [r["exercise"] for r in prog if r["status"] == "flat"],
               "regressing_lifts": [r["exercise"] for r in prog if r["status"] == "regressing"],
               "next_session": st.next_session(split, logged[["date", "type"]], mr, emap, prog, today)}
    wk = w["date"].map(lambda d: pd.Timestamp(common.week_start(d)))
    summary["_weekly"] = pd.DataFrame({"sessions": logged.groupby("week").size(), "hard_sets": w.groupby(wk).size()})
    return payload, summary, s, mr


# =========================================================================== health

HEALTH_METRICS = [
    # key, label, unit, unfavorable direction, digits
    ("hrv", "HRV (overnight)", "ms", "low", 0),
    ("rhr", "Resting HR", "bpm", "high", 0),
    ("sleep_h", "Sleep duration", "h", "low", 2),
    ("sleep_score", "Sleep score", "", "low", 0),
    ("stress", "Stress (avg)", "", "high", 0),
    ("bb_wake", "Body Battery at wake-up", "", "low", 0),
    ("resp", "Respiration (sleep)", "br/min", "high", 1),
]


def body_battery_at_wake(sleep: pd.DataFrame) -> pd.Series:
    """Body Battery level at wake time from the intraday curve (available from 2026-07-01)."""
    bb = src.load_intraday("body_battery")
    if bb.empty:
        return pd.Series(dtype=float)
    bb = bb.sort_values("ts_local")
    out = {}
    for r in sleep.itertuples():
        if pd.isna(r.wake_ts):
            continue
        near = bb[(bb["ts_local"] >= r.wake_ts - pd.Timedelta(minutes=30)) & (bb["ts_local"] <= r.wake_ts + pd.Timedelta(minutes=30))]
        if len(near):
            out[pd.Timestamp(r.date)] = float(near["value"].max())
    return pd.Series(out, dtype=float)


READINESS_LINKS = {"hrv": "health.html#hrv", "rhr": "health.html#rhr", "sleep": "health.html#sleep-need",
                   "debt": "health.html#sleep-need", "load": "health.html#training-load", "strain": "health.html#strain"}
LOAD_KINDS = ["Cycling", "E-bike", "Strength", "Other"]
LOAD_PANEL_DAYS = 90


def load_kind(a) -> str:
    """Which bar an activity's TRIMP is stacked into on the training-load panel."""
    if getattr(a, "ride_type", None) == "ebike" or str(getattr(a, "sub_sport", "")).startswith("e_bike"):
        return "E-bike"
    if a.sport == "cycling":
        return "Cycling"
    if a.sub_sport == "strength_training":
        return "Strength"
    return "Other"


def build_health(cfg: dict, daily: pd.DataFrame, sleep: pd.DataFrame, hrv: pd.DataFrame, weight: pd.DataFrame,
                 vo2: pd.DataFrame, enriched: pd.DataFrame, today: date) -> tuple[dict, dict, pd.DataFrame]:
    hcfg = cfg["health"]
    bdays, sdays = hcfg["baseline_days"], hcfg["short_window_days"]
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")
    sl = sleep.copy()
    sl["date"] = pd.to_datetime(sl["date"])
    sl = sl.set_index("date")
    hv = hrv.copy()
    hv["date"] = pd.to_datetime(hv["date"])
    hv = hv.set_index("date")
    idx = pd.date_range(d.index.min(), pd.Timestamp(today), freq="D")
    bb_wake = body_battery_at_wake(sleep)
    f = pd.DataFrame(index=idx)
    f["hrv"] = hv["hrv_overnight_avg_ms"]
    f["hrv_status"] = hv["hrv_status"]
    f["garmin_low"], f["garmin_high"] = hv["garmin_baseline_low"], hv["garmin_baseline_high"]
    f["rhr"] = d["resting_hr"]
    f["sleep_h"] = sl["total_s"] / 3600
    f["sleep_score"] = sl["sleep_score"]
    f["stress"] = d["stress_avg"]
    f["bb_max"], f["bb_min"] = d["body_battery_max"], d["body_battery_min"]
    f["bb_wake"] = bb_wake.reindex(idx)
    f["bb_wake_source"] = np.where(f["bb_wake"].notna(), "intraday", None)
    f["bb_wake"] = f["bb_wake"].fillna(f["bb_max"])  # before July 2026: daily max as a proxy
    f["resp"] = d["respiration_avg_sleep"]
    f["spo2"] = d["spo2_avg_sleep"]
    f["steps"] = d["steps"]
    f["im"] = d["intensity_min_moderate"] + 2 * d["intensity_min_vigorous"]
    for c in ("deep_s", "light_s", "rem_s", "awake_s"):
        f[c] = sl[c] / 3600
    f["need_h"] = sl["need_min"] / 60
    f["need_base_h"] = sl["need_baseline_min"] / 60
    for c in ("need_hrv_adj", "need_training_adj", "need_history_adj", "need_nap_adj"):
        f[c] = sl[c]
    f["nap_h"] = sl["nap_s"] / 3600
    f["debt_h"] = rd.sleep_debt(f["sleep_h"], f["need_h"], nap_h=f["nap_h"])
    f["bed_min"] = [hl.clock_minutes(sl["bed_ts"].get(x)) if x in sl.index else np.nan for x in idx]
    f["wake_min"] = [hl.clock_minutes(sl["wake_ts"].get(x)) if x in sl.index else np.nan for x in idx]
    f["mid_min"] = [hl.sleep_midpoint_minutes(sl["bed_ts"].get(x), sl["total_s"].get(x)) if x in sl.index else np.nan for x in idx]

    # baselines (7.7)
    bases = {}
    for key, *_ in HEALTH_METRICS + [("spo2", "", "", "low", 0)]:
        bases[key] = common.rolling_baseline(f[key].dropna(), bdays, sdays, 30).reindex(idx)
        # per-day band for charts: baseline/sd as of each day
        f[f"{key}_base"], f[f"{key}_sd"], f[f"{key}_z"] = bases[key]["baseline"], bases[key]["sd"], bases[key]["z"]
        f[f"{key}_day_z"] = (f[key] - f[f"{key}_base"]) / f[f"{key}_sd"]

    # header: today vs baseline
    header, outside = [], []
    for key, label, unit, unfav, digits in HEALTH_METRICS:
        series = f[key].dropna()
        if series.empty:
            header.append({"key": key, "label": label, "status": "no_data"})
            continue
        last_date = series.index[-1]
        b = bases[key].loc[:pd.Timestamp(today)].dropna(subset=["baseline"])
        z = float(b["z"].iloc[-1]) if len(b) else float("nan")
        status = common.flag(z, unfav)
        direction = None if math.isnan(z) else ("low" if z < 0 else "high")
        if status in ("watch", "out_of_range"):
            outside.append(f"{label} {'low' if z < 0 else 'elevated' if key in ('rhr', 'stress', 'resp') else 'high'}")
        header.append({"key": key, "label": label, "unit": unit, "digits": digits,
                       "value": num(series.iloc[-1], digits), "date": last_date.date(),
                       "avg7": num(f[key].iloc[-sdays:].mean(), digits),
                       "baseline": num(b["baseline"].iloc[-1], digits) if len(b) else None,
                       "sd": num(b["sd"].iloc[-1], digits) if len(b) else None,
                       "z": num(z, 2), "status": status, "direction": direction, "unfavorable": unfav,
                       "spark": [num(v, digits) for v in f[key].iloc[-28:].tolist()],
                       "source": ("intraday at wake time" if key == "bb_wake" and f["bb_wake_source"].iloc[-1] == "intraday" else None)})
    n_metrics = sum(1 for h in header if h.get("status") not in ("no_data", "no_baseline"))
    sentence = (f"{len(outside)} of {n_metrics} metrics outside baseline: " + ", ".join(outside)) if outside \
        else f"All {n_metrics} metrics within their personal baseline."

    def band(key, digits=1):
        return {"value": pts(f[key], digits), "avg7": pts(f[key].rolling(7, min_periods=3).mean(), digits),
                "lo": pts(f[f"{key}_base"] - f[f"{key}_sd"], digits), "hi": pts(f[f"{key}_base"] + f[f"{key}_sd"], digits),
                "base": pts(f[f"{key}_base"], digits),
                "outside": [[x.date(), num(f.loc[x, key], digits)] for x in idx
                            if not pd.isna(f.loc[x, f"{key}_day_z"]) and abs(f.loc[x, f"{key}_day_z"]) > 1]}

    hrv_panel = band("hrv", 0)
    hrv_panel["garmin_lo"], hrv_panel["garmin_hi"] = pts(f["garmin_low"], 0), pts(f["garmin_high"], 0)
    rhr_panel = band("rhr", 0)
    sleep_panel = {"deep": pts(f["deep_s"]), "light": pts(f["light_s"]), "rem": pts(f["rem_s"]),
                   "awake": pts(f["awake_s"]), "avg7": pts(f["sleep_h"].rolling(7, min_periods=3).mean()),
                   "score": pts(f["sleep_score"], 0), "score_avg7": pts(f["sleep_score"].rolling(7, min_periods=3).mean(), 1)}
    reg_sd = hl.regularity_sd(f["mid_min"])
    weekday = f.index.weekday < 5
    regularity = {"bed": pts(f["bed_min"], 0), "wake": pts(f["wake_min"], 0), "mid": pts(f["mid_min"], 0),
                  "sd14": num(reg_sd.dropna().iloc[-1], 0) if reg_sd.notna().any() else None,
                  "sd14_series": pts(reg_sd, 0),
                  "weekday": {"bed": num(f.loc[weekday, "bed_min"].mean(), 0), "wake": num(f.loc[weekday, "wake_min"].mean(), 0),
                              "sleep_h": num(f.loc[weekday, "sleep_h"].mean(), 2)},
                  "weekend": {"bed": num(f.loc[~weekday, "bed_min"].mean(), 0), "wake": num(f.loc[~weekday, "wake_min"].mean(), 0),
                              "sleep_h": num(f.loc[~weekday, "sleep_h"].mean(), 2)}}
    stress_panel = {"stress": pts(f["stress"], 0), "stress_avg7": pts(f["stress"].rolling(7, min_periods=3).mean(), 1),
                    "bb_max": pts(f["bb_max"], 0), "bb_min": pts(f["bb_min"], 0)}
    resp_panel = {"resp": band("resp", 1), "spo2": band("spo2", 1),
                  "has_resp": bool(f["resp"].notna().any()), "has_spo2": bool(f["spo2"].notna().any())}
    weeks = weekly_index(idx.min(), today)
    wkey = f.index.map(lambda x: pd.Timestamp(common.week_start(x)))
    activity_panel = {"weeks": [w.date() for w in weeks],
                      "steps": [num(v, 0) for v in f["steps"].groupby(wkey).mean().reindex(weeks)],
                      "im": [num(v, 0) for v in f["im"].groupby(wkey).sum().reindex(weeks)],
                      "im_reference": 150, "current_week": common.week_start(today)}
    v_panel = {k: [[r.date, num(r.vo2max, 1), r.sub_sport] for r in vo2[vo2["kind"] == k].sort_values("date").itertuples()]
               for k in ("generic", "cycling")}
    wser = pd.Series(weight["weight_kg"].values, index=pd.to_datetime(weight["date"])) if len(weight) else pd.Series(dtype=float)
    ema = hl.weight_ema(wser)
    weight_panel = {"points": pts(wser, 1), "ema": pts(ema, 2), "n": int(len(wser)),
                    "rate_per_week": None if len(wser) < 8 else num((ema.iloc[-1] - ema.iloc[-29]) / 4, 2)}

    # 10 load vs recovery: yesterday's total load vs last night's HRV deviation and sleep score
    load = enriched.groupby("date")["trimp"].sum().reindex(idx, fill_value=0.0)
    yday = load.shift(1)
    hrv_dev = f["hrv"] - f["hrv_base"]
    late = enriched[enriched["end_ts_local"].dt.hour >= 20]
    late_days = set(late["date"])  # session ended after 20:00 -> that night's sleep = next morning's date
    night_after_late = pd.Series([ (x - pd.Timedelta(days=1)) in late_days for x in idx], index=idx)
    def compare(col):
        a, b = f.loc[night_after_late, col].dropna(), f.loc[~night_after_late, col].dropna()
        return {"late_mean": num(a.mean(), 2), "other_mean": num(b.mean(), 2), "n_late": int(len(a)), "n_other": int(len(b)),
                "diff": num(a.mean() - b.mean(), 2) if len(a) and len(b) else None}
    lvr = {"bins": hl.load_bins(yday, hrv_dev, f["sleep_score"]),
           "corr_hrv": hl.correlation(yday, hrv_dev), "corr_sleep": hl.correlation(yday, f["sleep_score"]),
           "late": {"sleep_score": compare("sleep_score"), "bed_min": compare("bed_min")}, "min_n": 20}

    # 11 weekday pattern
    wd = f.groupby(f.index.weekday)[["stress", "sleep_h", "hrv"]].median()
    weekday_panel = {"labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                     "stress": [num(wd["stress"].get(i), 0) for i in range(7)],
                     "sleep_h": [num(wd["sleep_h"].get(i), 2) for i in range(7)],
                     "hrv": [num(wd["hrv"].get(i), 0) for i in range(7)]}

    # 12 strain pattern
    strain = hl.strain_days(f["rhr_day_z"], f["hrv_day_z"], f["resp_day_z"])
    strain_panel = {"days": [x.date() for x in strain[strain].index]}

    # 13 sleep need vs sleep, and today's readiness (last night = today's row; falls back to the newest night)
    f["load"] = load
    f["load_ratio"] = rd.load_ratio(load)
    f["strain"] = strain.reindex(idx, fill_value=False)
    sleep_need_panel = {"sleep": pts(f["sleep_h"], 2), "need": pts(f["need_h"], 2), "need_base": pts(f["need_base_h"], 2), "nap": pts(f["nap_h"].where(f["nap_h"] > 0), 2),
                        "debt": pts(f["debt_h"], 2), "debt_nights": rd.DEBT_NIGHTS, "debt_bounds": list(rd.DEBT_H)}
    readiness = None
    nights = f.index[f["sleep_h"].notna() | f["hrv"].notna()]
    if len(nights):
        x = nights[-1]
        row = f.loc[x]
        val = lambda k: None if pd.isna(row[k]) else float(row[k])
        readiness = rd.assess({"hrv": val("hrv"), "hrv_z": val("hrv_day_z"), "hrv_status": row["hrv_status"] if isinstance(row["hrv_status"], str) else None,
                               "rhr": val("rhr"), "rhr_z": val("rhr_day_z"), "sleep_h": val("sleep_h"), "need_h": val("need_h"),
                               "debt_h": val("debt_h"), "load_ratio": val("load_ratio"), "strain": bool(row["strain"])})
        readiness["date"] = x.date()
        readiness["trend_note"] = rd.trend_note({fa["key"]: fa["status"] for fa in readiness["factors"]},
                                                {h["key"]: h.get("status") for h in header})
        readiness["is_today"] = x.date() == today
        readiness["need_why"] = rd.need_reasons({k: row[k] for k in ("need_hrv_adj", "need_training_adj", "need_history_adj", "need_nap_adj")},
                                                val("need_h"), val("need_base_h"))
        sleep_need_panel["latest"] = {"date": x.date(), "sleep_h": num(row["sleep_h"], 2), "need_h": num(row["need_h"], 2),
                                      "base_h": num(row["need_base_h"], 2), "debt_h": num(row["debt_h"], 2), "why": readiness["need_why"]}
        # every factor row on the Overview links to the Health panel that explains it
        for fa in readiness["factors"]:
            fa["href"] = READINESS_LINKS.get(fa["key"])

    # 14 training load behind the readiness factor: daily TRIMP by kind, 7- and 28-day means, ratio, activities
    load_panel = None
    if len(nights):
        end = nights[-1]
        days = pd.date_range(end - pd.Timedelta(days=LOAD_PANEL_DAYS - 1), end, freq="D")
        e = enriched[(enriched["date"] >= days[0]) & (enriched["date"] <= end)].copy()
        e["kind"] = [load_kind(r) for r in e.itertuples()]
        by_kind = {k: [num(v, 0) for v in e[e["kind"] == k].groupby("date")["trimp"].sum().reindex(days, fill_value=0.0)]
                   for k in LOAD_KINDS}
        acute = load.rolling(rd.ACUTE_DAYS, min_periods=rd.ACUTE_DAYS).mean()
        chronic = load.rolling(rd.CHRONIC_DAYS, min_periods=rd.CHRONIC_DAYS).mean()
        win = e[e["date"] > end - pd.Timedelta(days=rd.CHRONIC_DAYS)].sort_values(["date", "start_ts_local"], ascending=False)
        load_panel = {
            "days": [d.date() for d in days], "kinds": LOAD_KINDS, "by_kind": by_kind,
            "acute": pts(acute.reindex(days), 1), "chronic": pts(chronic.reindex(days), 1),
            "ratio": pts(f["load_ratio"].reindex(days), 2),
            "bounds": list(rd.LOAD_RATIO), "acute_days": rd.ACUTE_DAYS, "chronic_days": rd.CHRONIC_DAYS,
            "min_chronic": rd.MIN_CHRONIC_LOAD,
            "latest": {"date": end.date(), "acute": num(acute.get(end), 1), "chronic": num(chronic.get(end), 1),
                       "ratio": num(f["load_ratio"].get(end), 2),
                       "acute_sum": num(load[end - pd.Timedelta(days=rd.ACUTE_DAYS - 1):end].sum(), 0),
                       "before_sum": num(load[end - pd.Timedelta(days=rd.CHRONIC_DAYS - 1):end - pd.Timedelta(days=rd.ACUTE_DAYS)].sum(), 0)},
            "activities": [{"date": r.date.date(), "name": r.name if isinstance(r.name, str) else None, "kind": r.kind,
                            "min": num((r.duration_s or 0) / 60, 0), "avg_hr": num(r.avg_hr, 0), "trimp": num(r.trimp, 0),
                            "acute": bool(r.date > end - pd.Timedelta(days=rd.ACUTE_DAYS))} for r in win.itertuples()],
        }

    payload = {"header": header, "sentence": sentence, "hrv": hrv_panel, "rhr": rhr_panel, "sleep": sleep_panel,
               "regularity": regularity, "stress": stress_panel, "resp": resp_panel, "activity": activity_panel,
               "vo2max": v_panel, "weight": weight_panel, "load_recovery": lvr, "weekday": weekday_panel,
               "strain": strain_panel, "sleep_need": sleep_need_panel, "readiness": readiness, "training_load": load_panel,
               "baseline_days": bdays, "short_days": sdays,
               "intraday_from": "2026-07-01"}
    summary = {"header": header, "short_days": sdays, "n_metrics": n_metrics, "n_outside": len(outside),
               "hrv_avg7": header[0].get("avg7"), "rhr_avg7": header[1].get("avg7"),
               "hrv": header[0].get("value"), "hrv_status": header[0].get("status"),
               "rhr": header[1].get("value"), "rhr_status": header[1].get("status"), "sentence": sentence,
               "readiness": readiness}
    return payload, summary, f


# =========================================================================== main

def main():
    t_start = time.time()
    cfg = src.config()
    acts, daily, sleep = src.load_activities(), src.load_daily(), src.load_sleep()
    acts, archive = strava_archive.with_archive(acts, cfg)
    log(f"strava archive: {archive}")
    hrv, weight, sets, vo2 = src.load_hrv(), src.load_weight(), src.load_strength_sets(), src.load_vo2max()
    today = date.today()
    data_through = max(pd.to_datetime(daily["date"]).max().date(), pd.to_datetime(acts["date"]).max().date())
    first_day = pd.to_datetime(daily["date"]).min().date()
    enriched = enrich_activities(cfg, acts, daily, first_day)

    cycling_payload, cyc_extra = build_cycling(cfg, enriched, weight, vo2, today, first_day, archive)
    # strength history starts at the first logged set (Fitbod import reaches back before the first Garmin day)
    str_first = min(first_day, pd.to_datetime(sets["date"]).min().date()) if len(sets) else first_day
    strength_payload, str_summary, prepared_sets, mrows = build_strength(cfg, enriched, sets, weight, today, str_first)
    health_payload, health_summary, health_frame = build_health(cfg, daily, sleep, hrv, weight, vo2, enriched, today)
    weekly = build_weekly(today, cyc_extra["weekly"], str_summary.pop("_weekly"), health_frame)
    if health_summary.get("readiness"):  # same dict as in the health payload: the Overview card shows both
        R = health_summary["readiness"]
        R["plan"] = rd.plan(str_summary.get("next_session"), R["verdict"])
        # already trained today? Rides from Garmin + Strava archive; lifting from the note log, else Garmin's session
        now = enriched[enriched["date"] == pd.Timestamp(today)]
        rides = now[now["sport"] == "cycling"][["ride_type", "duration_s", "distance_m"]].to_dict("records")
        lifts = list((str_summary.get("next_session") or {}).get("today_types") or [])
        if not lifts and (now["sub_sport"] == "strength_training").any():
            lifts = ["strength"]
        R["done"] = rd.today_done(rides, lifts, R["verdict"])

    meta = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "data_through": data_through,
            "stale": (today - data_through).days > 3, "today": today, "first_day": first_day,
            "ranges": RANGES, "default_range": "3m"}
    for p in (cycling_payload, strength_payload, health_payload):
        p["meta"] = meta
    attn = attention.build(health_summary.pop("header"), cyc_extra["summary"], str_summary, today, health_summary)
    for area, p in (("cycling", cycling_payload), ("strength", strength_payload), ("health", health_payload)):
        p["attention"] = [i for i in attn["items"] if i["area"] == area]
    reviews = src.load_weekly_reviews()
    index_payload = {"meta": meta, "attention": attn, "cycling": cyc_extra["summary"], "strength": str_summary,
                     "health": health_summary, "budget": cyc_extra["budget"], "weekly": weekly,
                     "last_review": {k: reviews[0][k] for k in ("week", "start", "end")} if reviews else None}
    reviews_payload = {"meta": meta, "reviews": reviews}

    BUILD.mkdir(exist_ok=True)
    db_path = BUILD / "metrics.duckdb"
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    tables = {
        "activities": enriched.drop(columns=["start_lat", "start_lon", "end_lat", "end_lon", "segs", "track"], errors="ignore"),
        "daily_health": health_frame.reset_index(names="date"),
        "strength_sets": prepared_sets, "muscle_rows": mrows,
    }
    for name, df in tables.items():
        df = df.copy()
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v))
        con.register("tmp", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM tmp")
        con.unregister("tmp")
    con.close()

    write_payload("map", build_map(enriched, meta))
    write_payload("cycling", cycling_payload)
    write_payload("strength", strength_payload)
    write_payload("health", health_payload)
    write_payload("index", index_payload)
    write_payload("reviews", reviews_payload)
    log(f"done in {time.time() - t_start:.1f} s (data through {data_through})")


if __name__ == "__main__":
    main()
