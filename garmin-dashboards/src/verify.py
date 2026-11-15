"""Spot-check: dashboard values next to raw source values for the last 5 rides and the last 5 nights.

    make verify
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd

from src.adapters import source as src

BUILD = src.PROJECT / "build"


def main():
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    m = con.execute("""SELECT activity_id, date, name, ride_type, duration_s, distance_m, avg_hr, trimp, load_quality,
                              z1_s, z2_s, z3_s, np FROM activities WHERE sport = 'cycling' ORDER BY start_ts_local DESC LIMIT 5""").df()
    with src.connect() as raw:
        r = pd.read_sql_query(f"""SELECT activity_id, duration_s AS raw_duration_s, distance_m AS raw_distance_m,
                                         avg_hr AS raw_avg_hr, (SELECT COUNT(*) FROM activity_records x WHERE x.activity_id = a.activity_id) AS raw_samples
                                  FROM activities a WHERE activity_id IN ({",".join(map(str, m.activity_id))})""", raw)
    rides = m.merge(r, on="activity_id")
    print("Last 5 rides: dashboard (metrics.duckdb) vs raw (garmin.db)")
    for x in rides.itertuples():
        ok = abs(x.duration_s - x.raw_duration_s) < 1 and abs((x.distance_m or 0) - (x.raw_distance_m or 0)) < 1
        print(f"  {str(x.date)[:10]} {x.name[:28]:28} type={x.ride_type:8} min {x.duration_s/60:6.1f} | raw {x.raw_duration_s/60:6.1f}  "
              f"km {(x.distance_m or 0)/1000:5.1f} | raw {(x.raw_distance_m or 0)/1000:5.1f}  avgHR {x.avg_hr:.0f} | raw {x.raw_avg_hr:.0f}  "
              f"TRIMP {x.trimp:5.1f} ({x.load_quality}, {x.raw_samples} samples)  zones h {x.z1_s/3600:.2f}/{x.z2_s/3600:.2f}/{x.z3_s/3600:.2f}  "
              f"{'OK' if ok else 'MISMATCH'}")

    h = json.loads((BUILD / "data" / "health.json").read_text())
    hrv = dict((d, v) for d, v in h["hrv"]["value"])
    rhr = dict((d, v) for d, v in h["rhr"]["value"])
    deep = dict(h["sleep"]["deep"]); light = dict(h["sleep"]["light"]); rem = dict(h["sleep"]["rem"])
    with src.connect() as raw:
        nights = pd.read_sql_query("""SELECT s.calendar_date, s.sleep_s, s.score, h.last_night_avg, d.hr_resting
                                      FROM sleep s LEFT JOIN hrv h USING (calendar_date) LEFT JOIN daily_summary d USING (calendar_date)
                                      ORDER BY s.calendar_date DESC LIMIT 5""", raw)
    print("\nLast 5 nights: dashboard (health payload) vs raw (garmin.db)")
    for n in nights.itertuples():
        d = n.calendar_date
        total = (deep.get(d, 0) or 0) + (light.get(d, 0) or 0) + (rem.get(d, 0) or 0)
        ok = abs(total * 3600 - n.sleep_s) < 120 and hrv.get(d) == n.last_night_avg and rhr.get(d) == n.hr_resting
        print(f"  {d}  sleep h {total:5.2f} | raw {n.sleep_s/3600:5.2f}   HRV {hrv.get(d)} | raw {n.last_night_avg}   "
              f"RHR {rhr.get(d)} | raw {n.hr_resting}   {'OK' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
