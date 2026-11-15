"""Print the median commute track every 500 m, so segment start/end points can be picked without a map.

    .venv/bin/python -m src.segments_suggest

Coordinates go to the terminal only; nothing is written to the build or the site. Output per direction: km mark,
lat/lon, altitude and the gradient over the preceding 500 m. Pick two rows (start, end) and put them in config.yaml
under cycling.segments (see PLAN_cycling_analytics.md, section D).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.adapters import source as src
from src.metrics import cycling as cyc

STEP_M = 500
MAX_RIDES = 12   # most recent rides per direction


def _track(stream: pd.DataFrame) -> pd.DataFrame | None:
    s = stream.dropna(subset=["lat", "lon", "speed"])
    if len(s) < 20:
        return None
    dt = s["t_s"].diff().fillna(0).clip(upper=cyc.BEST_GAP_S)
    return pd.DataFrame({"d": (s["speed"] * dt).cumsum().to_numpy(), "lat": s["lat"].to_numpy(), "lon": s["lon"].to_numpy(),
                         "alt": s["altitude"].to_numpy() if "altitude" in s else np.nan})


def median_track(tracks: list[pd.DataFrame]) -> pd.DataFrame:
    n = int(min(t["d"].iloc[-1] for t in tracks) // STEP_M)
    marks = np.arange(0, n + 1) * STEP_M
    cols = {k: np.nanmedian([np.interp(marks, t["d"], t[k]) for t in tracks], axis=0) for k in ("lat", "lon", "alt")}
    out = pd.DataFrame({"km": marks / 1000, **cols})
    out["gradient_pct"] = out["alt"].diff() / STEP_M * 100
    return out


def main():
    acts = src.load_activities()
    rides = acts[acts["sport"] == "cycling"].copy()
    rides["ride_type"] = cyc.classify_rides(rides, src.config())
    commutes = rides[rides["ride_type"] == "commute"]
    centers = cyc.endpoint_clusters(rides[~rides["sub_sport"].str.contains("e_bike", na=False)])
    if len(centers) < 2 or commutes.empty:
        print("No commutes detected.")
        return
    labels = src.config()["cycling"].get("commute_labels") or ["A", "B"]
    for a, b in ((0, 1), (1, 0)):
        sel = commutes[[cyc._dist_m(r.start_lat, r.start_lon, *centers[a]) < cyc.MATCH_TOL_M for r in commutes.itertuples()]]
        sel = sel.sort_values("start_ts_local").tail(MAX_RIDES)
        tracks = [t for t in (_track(src.load_activity_streams(i)) for i in sel["activity_id"]) if t is not None]
        if len(tracks) < 3:
            continue
        print(f"\n{labels[a]} → {labels[b]}  (median of {len(tracks)} recent commutes)")
        print(f"{'km':>5}  {'lat':>9}  {'lon':>9}  {'alt m':>6}  {'grade %':>7}")
        for r in median_track(tracks).itertuples():
            g = "" if pd.isna(r.gradient_pct) else f"{r.gradient_pct:+.1f}"
            print(f"{r.km:5.1f}  {r.lat:9.5f}  {r.lon:9.5f}  {r.alt:6.0f}  {g:>7}")


if __name__ == "__main__":
    main()
