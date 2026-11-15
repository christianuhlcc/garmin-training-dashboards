"""Print the data inventory (coverage per canonical table/column + Phase 0 answers) as Markdown.

    make inventory          # same as: .venv/bin/python -m src.inventory
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from src.adapters import source as src


def coverage(dates) -> dict:
    d = sorted({x for x in pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna().dt.date})
    if not d:
        return {"first": "-", "last": "-", "days": 0, "longest_gap": "-"}
    gap, gap_at = 0, None
    for a, b in zip(d, d[1:]):
        if (b - a).days - 1 > gap:
            gap, gap_at = (b - a).days - 1, (a, b)
    gap_s = f"{gap} d ({gap_at[0]} → {gap_at[1]})" if gap_at else "0"
    span = (d[-1] - d[0]).days + 1
    return {"first": d[0], "last": d[-1], "days": f"{len(d)} / {span}", "longest_gap": gap_s}


def table(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "_none_\n"
    cols = cols or list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def fill(df: pd.DataFrame, cols: list[str]) -> str:
    n = len(df)
    rows = []
    for c in cols:
        nn = int(df[c].notna().sum()) if c in df else 0
        label = c
        if df[c].dtype == bool:
            nn, label = int(df[c].sum()), f"{c} (true)"
        rows.append({"column": label, "non-null": f"{nn} / {n}", "%": f"{100 * nn / n:.0f}" if n else "-"})
    return table(rows)


def main():
    cfg = src.config()
    print(f"# Data inventory (generated {datetime.now():%Y-%m-%d %H:%M})\n")
    print(f"Source: `{src.db_path()}` ({src.db_path().stat().st_size / 1e6:.0f} MB), "
          f"FIT files: `{src.fit_dir()}` ({len(list(src.fit_dir().glob('*.zip'))) if src.fit_dir().exists() else 0})\n")
    with src.connect() as con:
        state = dict(con.execute("SELECT key, value FROM sync_state").fetchall())
        runs = con.execute("SELECT started_at, mode, window_start, window_end, api_calls, errors FROM sync_runs "
                           "ORDER BY id DESC LIMIT 3").fetchall()
    print(f"Sync state: {json.dumps(state)}  \nLast runs (UTC): " +
          "; ".join(f"{r[0]} {r[1]} {r[2]}..{r[3]} calls={r[4]} errors={r[5]}" for r in runs) + "\n")

    acts = src.load_activities()
    daily, sleep, hrv, weight = src.load_daily(), src.load_sleep(), src.load_hrv(), src.load_weight()
    sets = src.load_strength_sets()
    vo2 = src.load_vo2max()

    # ---------------------------------------------------------------- coverage
    print("## Coverage per table\n")
    with src.connect() as con:
        def dates_of(sql):
            return [r[0] for r in con.execute(sql)]
        stream_dates = dates_of("SELECT DISTINCT a.calendar_date FROM activities a WHERE EXISTS "
                                "(SELECT 1 FROM activity_records r WHERE r.activity_id = a.activity_id)")
        intraday = {
            "intraday heart rate (2 min)": "heart_rate_samples",
            "intraday body battery (15 min)": "body_battery_samples",
            "intraday HRV readings (5 min, sleep)": "hrv_readings",
            "intraday SpO2 (hourly)": "spo2_samples",
        }
        intraday_rows = {k: (dates_of(f"SELECT DISTINCT calendar_date FROM {t}"),
                             con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for k, t in intraday.items()}
    rows = []
    for name, n, dates in [
        ("activities", len(acts), acts["date"]),
        ("activity_streams (activities with FIT records)", int(acts["has_hr_stream"].sum() | 0), stream_dates),
        ("daily", len(daily), daily["date"]),
        ("sleep", len(sleep), sleep["date"]),
        ("hrv", len(hrv), hrv["date"]),
        ("weight", len(weight), weight["date"]),
        ("vo2max estimates", len(vo2), vo2["date"]),
        ("strength_sets (manual, Apple Notes)", int((sets["source"] == "manual").sum()),
         sets.loc[sets["source"] == "manual", "date"]),
        ("strength_sets (garmin auto-detected)", int((sets["source"] == "garmin").sum()),
         sets.loc[sets["source"] == "garmin", "date"]),
        *[(k, n, d) for k, (d, n) in intraday_rows.items()],
    ]:
        rows.append({"table": name, "rows": n, **coverage(dates)})
    rows[1]["rows"] = len(stream_dates) and int(acts[["has_hr_stream", "has_power_stream", "has_gps_stream"]]
                                                 .any(axis=1).sum())
    print(table(rows, ["table", "rows", "first", "last", "days", "longest_gap"]))
    print("`days` = days with data / days in span.\n")

    # ---------------------------------------------------------------- column fill
    print("## Column fill (canonical columns)\n")
    for name, df, cols in [("activities", acts, src.ACTIVITY_COLS), ("daily", daily, src.DAILY_COLS),
                           ("sleep", sleep, src.SLEEP_COLS), ("hrv", hrv, src.HRV_COLS),
                           ("weight", weight, src.WEIGHT_COLS), ("strength_sets", sets, src.STRENGTH_COLS)]:
        print(f"### {name}\n")
        print(fill(df, cols))

    # ---------------------------------------------------------------- cycling
    print("## Cycling\n")
    cyc = acts[acts["sport"] == "cycling"].copy()
    cyc["month"] = pd.to_datetime(cyc["start_ts_local"]).dt.strftime("%Y-%m")
    ebike_names = [n.lower() for n in cfg["cycling"].get("ebike_names") or []]
    cyc["ebike"] = cyc["sub_sport"].str.contains("e_bike") | cyc["name"].str.lower().apply(
        lambda n: any(e in (n or "") for e in ebike_names))
    print(f"Cycling activities: **{len(cyc)}** ({cyc['date'].min()} → {cyc['date'].max()}), "
          f"of which e-bike: {int(cyc['ebike'].sum())}\n")
    print("By sub_sport:\n")
    print(table([{"sub_sport": k, "n": v, "hours": round(cyc.loc[cyc.sub_sport == k, 'duration_s'].sum() / 3600, 1),
                  "km": round(cyc.loc[cyc.sub_sport == k, 'distance_m'].sum() / 1000)}
                 for k, v in cyc["sub_sport"].value_counts().items()]))
    print("By device:\n")
    print(table([{"device": k, "n": v} for k, v in cyc["device"].fillna("(no FIT file)").value_counts().items()]))
    print("Streams and sensors:\n")
    print(table([
        {"metric": "HR stream", "n": int(cyc.has_hr_stream.sum())},
        {"metric": "power stream", "n": int(cyc.has_power_stream.sum())},
        {"metric": "GPS stream", "n": int(cyc.has_gps_stream.sum())},
        {"metric": "HR from chest strap (sensor listed)", "n": int((cyc.hr_quality == "strap").sum())},
        {"metric": "HR wrist only", "n": int((cyc.hr_quality == "wrist").sum())},
        {"metric": "power meter listed", "n": int(cyc.power_sensor.notna().sum())},
        {"metric": "avg_power in summary", "n": int(cyc.avg_power.notna().sum())},
    ]))
    print("HR sensor manufacturers:", {k: int(v) for k, v in cyc["hr_sensor"].fillna("none (wrist)").value_counts().items()}, "  ")
    print("Power sensor manufacturers:", {k: int(v) for k, v in cyc["power_sensor"].fillna("none").value_counts().items()}, "\n")
    pw = cyc[cyc.has_power_stream | cyc.avg_power.notna()]
    if len(pw):
        print("Rides with power:\n")
        print(table([{"date": r.date, "sub_sport": r.sub_sport, "min": round(r.duration_s / 60),
                      "avg_power": r.avg_power, "power_stream": r.has_power_stream, "power_sensor": r.power_sensor}
                     for r in pw.itertuples()]))
    print("By month × sub_sport (count):\n")
    pivot = cyc.pivot_table(index="month", columns="sub_sport", values="activity_id", aggfunc="count", fill_value=0)
    print(table([{"month": m, **{c: int(pivot.loc[m, c]) for c in pivot.columns}} for m in pivot.index],
                ["month", *pivot.columns]))
    long_min = cfg["cycling"]["long_ride_min_minutes"]
    print(f"Rides with moving time ≥ {long_min} min: "
          f"{int((cyc.moving_s.fillna(cyc.duration_s) >= long_min * 60).sum())}; "
          f"≥ 90 min: {int((cyc.moving_s.fillna(cyc.duration_s) >= 90 * 60).sum())}\n")

    # ---------------------------------------------------------------- strength
    print("## Strength\n")
    st = acts[acts["sub_sport"] == "strength_training"]
    g = sets[sets.source == "garmin"]
    g_active = g[g.set_type == "active"]
    m = sets[sets.source == "manual"]
    with src.connect() as con:
        g_all = pd.read_sql_query("SELECT s.* FROM activity_sets s", con)
        manual_sessions = con.execute("SELECT COUNT(*), SUM(garmin_activity_id IS NOT NULL), MIN(workout_date), "
                                      "MAX(workout_date) FROM manual_workouts").fetchone() \
            if src._has_table("manual_workouts") else (0, 0, None, None)
        cats = {}
        for (raw,) in con.execute("SELECT raw FROM activities WHERE type_key = 'strength_training'"):
            for s in json.loads(raw).get("summarizedExerciseSets") or []:
                cats[s.get("category")] = cats.get(s.get("category"), 0) + (s.get("sets") or 0)
    ga = g_all[g_all.set_type == "active"]
    print(f"Strength activities (Garmin): **{len(st)}** ({st['date'].min()} → {st['date'].max()})  ")
    print(f"Garmin FIT sets (all activities): {len(g_all)} rows, {len(ga)} active; "
          f"with exercise category: {int(ga.category.notna().sum())} ({100 * ga.category.notna().mean():.0f}%); "
          f"weight > 0: {int((ga.weight_kg > 0).sum())} ({100 * (ga.weight_kg > 0).mean():.0f}%)  ")
    print(f"Manual log (Apple Notes): {manual_sessions[0]} sessions ({manual_sessions[2]} → {manual_sessions[3]}), "
          f"{len(m)} sets, {manual_sessions[1]} sessions linked to a Garmin activity  ")
    print(f"Strength activities without a manual session (Garmin sets only): "
          f"{g['activity_id'].nunique()} activities, {len(g_active)} active sets\n")
    print("Garmin auto-detected categories (sets, from activity summaries; UNKNOWN = not recognized):\n")
    print(table([{"category": k, "sets": v} for k, v in sorted(cats.items(), key=lambda x: -x[1])]))
    print("Manual log exercises (canonical name, sets, sessions, raw spellings):\n")
    if len(m):
        ex = m.groupby("exercise").agg(sets=("reps", "size"), sessions=("workout_id", "nunique"),
                                       raw=("exercise_raw", lambda s: ", ".join(sorted(set(s))))).reset_index()
        print(table(ex.sort_values("sets", ascending=False).to_dict("records")))

    # ---------------------------------------------------------------- health
    print("## Health metrics\n")
    def first_last(df, col):
        s = df.loc[df[col].notna(), "date"]
        return {"n": len(s), "first": s.min() if len(s) else "-", "last": s.max() if len(s) else "-"}
    rows = [
        {"metric": "resting HR", **first_last(daily, "resting_hr")},
        {"metric": "steps", **first_last(daily, "steps")},
        {"metric": "stress avg", **first_last(daily, "stress_avg")},
        {"metric": "body battery max/min", **first_last(daily, "body_battery_max")},
        {"metric": "respiration (sleep)", **first_last(daily, "respiration_avg_sleep")},
        {"metric": "SpO2 (sleep)", **first_last(daily, "spo2_avg_sleep")},
        {"metric": "sleep duration", **first_last(sleep, "total_s")},
        {"metric": "sleep stages (deep)", **first_last(sleep, "deep_s")},
        {"metric": "sleep score", **first_last(sleep, "sleep_score")},
        {"metric": "overnight HRV", **first_last(hrv, "hrv_overnight_avg_ms")},
        {"metric": "HRV Garmin baseline", **first_last(hrv, "garmin_baseline_low")},
        {"metric": "VO2max (generic)", **first_last(vo2[vo2.kind == "generic"], "vo2max")},
        {"metric": "VO2max (cycling)", **first_last(vo2[vo2.kind == "cycling"], "vo2max")},
        {"metric": "weight", **first_last(weight, "weight_kg")},
        {"metric": "body fat", **first_last(weight, "body_fat_pct")},
    ]
    print(table(rows))


if __name__ == "__main__":
    main()
