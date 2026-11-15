"""Cycling metrics (plan sections 6.1, 7.1-7.5). Pure functions on arrays/DataFrames plus per-ride stream metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

MEAN_MAX_DURATIONS = [5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600]
TRIMP_MAX_DT_S = 30  # a sample stands for at most 30 s (smart recording gaps, pauses)
STREAM_VERSION = 4   # bump when ride_stream_metrics gains/changes a field: part of the stream-cache key


# --------------------------------------------------------------------------- zones / load

def hr_zone_bounds(cfg: dict) -> tuple[float, float]:
    hr_max = cfg["athlete"]["hr_max"]
    z = cfg["cycling"].get("zones_hr") or {}
    return (z.get("z1_upper_bpm") or 0.80 * hr_max, z.get("z2_upper_bpm") or 0.88 * hr_max)


def _dt_minutes(t_s) -> np.ndarray:
    t = np.asarray(t_s, dtype=float)
    if len(t) == 0:
        return t
    dt = np.diff(t, prepend=t[0])
    if len(dt) > 1:
        dt[0] = dt[1]  # first sample stands for one typical interval
    return np.clip(dt, 0, TRIMP_MAX_DT_S) / 60.0


def trimp_stream(t_s, hr, hr_rest: float, hr_max: float) -> float:
    """Banister TRIMP (7.1), male coefficients: sum dt * HRr * 0.64 * exp(1.92 * HRr)."""
    h = np.asarray(hr, dtype=float)
    dt = _dt_minutes(t_s)
    hrr = np.clip((h - hr_rest) / (hr_max - hr_rest), 0, 1)
    contrib = dt * hrr * 0.64 * np.exp(1.92 * hrr)
    return float(np.nansum(contrib))


def trimp_avg(avg_hr: float, duration_s: float, hr_rest: float, hr_max: float) -> float:
    """Same formula with one 'sample' of average HR over the duration (load_quality = avg_hr)."""
    if avg_hr is None or duration_s is None or (isinstance(avg_hr, float) and math.isnan(avg_hr)):
        return float("nan")
    hrr = min(max((avg_hr - hr_rest) / (hr_max - hr_rest), 0.0), 1.0)
    return duration_s / 60.0 * hrr * 0.64 * math.exp(1.92 * hrr)


def zone_seconds(t_s, hr, z1_upper: float, z2_upper: float) -> tuple[float, float, float]:
    """Time (s) in the 3-zone model: easy (z1) < z1_upper <= grey zone (z2) < z2_upper <= hard (z3); the 3-band polarized model, not Garmin's 5 zones."""
    h = np.asarray(hr, dtype=float)
    dt = _dt_minutes(t_s) * 60
    ok = ~np.isnan(h)
    z1 = float(dt[ok & (h < z1_upper)].sum())
    z2 = float(dt[ok & (h >= z1_upper) & (h < z2_upper)].sum())
    z3 = float(dt[ok & (h >= z2_upper)].sum())
    return z1, z2, z3


# --------------------------------------------------------------------------- 1-s grid + power

def to_1s(stream: pd.DataFrame, cols=("hr", "power", "speed", "cadence"), limit: int = 5) -> pd.DataFrame:
    """Resample an irregular stream onto a 1-s grid. Values carry forward at most `limit` s; longer gaps stay NaN."""
    if stream.empty:
        return pd.DataFrame(columns=list(cols))
    s = stream.copy()
    s["t"] = s["t_s"].round().astype(int)
    s = s.drop_duplicates("t", keep="last").set_index("t")
    grid = s.reindex(range(int(s.index.min()), int(s.index.max()) + 1))
    out = pd.DataFrame(index=grid.index)
    for c in cols:
        if c in grid:
            if c == "speed":
                out[c] = grid[c].interpolate(limit=limit, limit_area="inside")
            else:
                out[c] = grid[c].ffill(limit=limit)
    return out


def normalized_power(power_1s) -> float:
    """NP (7.3): 30-s rolling mean, ^4, mean, 4th root. Gaps count as 0 W."""
    p = pd.Series(np.asarray(power_1s, dtype=float)).fillna(0.0)
    if len(p) < 30:
        return float("nan")
    r = p.rolling(30).mean().dropna()
    return float(np.mean(r ** 4) ** 0.25)


def tss(duration_s: float, np_w: float, ftp: float) -> float:
    """TSS = (duration_s * NP * IF) / (FTP * 3600) * 100, IF = NP / FTP."""
    if not ftp or not np_w or math.isnan(np_w):
        return float("nan")
    intensity = np_w / ftp
    return duration_s * np_w * intensity / (ftp * 3600) * 100


def mean_max(power_1s, durations=MEAN_MAX_DURATIONS) -> dict[int, float]:
    p = pd.Series(np.asarray(power_1s, dtype=float)).fillna(0.0)
    return {d: float(p.rolling(d).mean().max()) for d in durations if len(p) >= d}


# --------------------------------------------------------------------------- decoupling / efficiency

def decoupling(grid: pd.DataFrame, use_power: bool) -> dict | None:
    """Split moving time into halves (7.4). EF = NP / avg HR (power) or avg speed / avg HR.
    decoupling_pct = (EF_first - EF_second) / EF_first * 100."""
    if grid.empty or "hr" not in grid:
        return None
    moving = (grid["speed"] > 1.0) if ("speed" in grid and grid["speed"].notna().any()) \
        else (grid.get("power", pd.Series(0, index=grid.index)).fillna(0) > 0)
    g = grid[moving & grid["hr"].notna()]
    if len(g) < 600:
        return None
    half = len(g) // 2
    parts = (g.iloc[:half], g.iloc[half:])
    efs = []
    for part in parts:
        hr = part["hr"].mean()
        if use_power:
            efs.append(normalized_power(part["power"]) / hr)
        else:
            efs.append(part["speed"].mean() / hr)
    if not efs[0] or math.isnan(efs[0]):
        return None
    return {"ef_first": efs[0], "ef_second": efs[1], "decoupling_pct": (efs[0] - efs[1]) / efs[0] * 100,
            "method": "power" if use_power else "speed"}


# --------------------------------------------------------------------------- sprints

def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    segs, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            segs.append((start, i - 1))
            start = None
    if start is not None:
        segs.append((start, len(mask) - 1))
    return segs


def _merge(segs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in segs:
        if out and a - out[-1][1] < gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def sprints_power(power_1s, threshold_w: float, min_s: int = 8, merge_gap_s: int = 20) -> int:
    """Efforts whose 10-s mean power exceeds the threshold for >= min_s s, merged when < merge_gap_s apart."""
    p = pd.Series(np.asarray(power_1s, dtype=float)).fillna(0.0)
    m = (p.rolling(10, min_periods=5, center=True).mean() > threshold_w).to_numpy()
    segs = [s for s in _segments(m) if s[1] - s[0] + 1 >= min_s]
    return len(_merge(segs, merge_gap_s))


def sprints_speed(speed_1s_mps, gain_kmh: float = 10, start_max_kmh: float = 15, window_s: int = 10,
                  merge_gap_s: int = 20, peak_min_kmh: float = 30) -> int:
    """Acceleration events: speed rises >= gain_kmh within window_s, starting below start_max_kmh, and the
    effort reaches >= peak_min_kmh within 20 s. The peak condition keeps ordinary pull-aways from traffic lights
    (0 -> ~25 km/h) from counting as sprints."""
    v = pd.Series(np.asarray(speed_1s_mps, dtype=float) * 3.6)
    # max over the next window_s seconds (i+1 .. i+window_s), and over the next 20 s for the peak
    ahead = v.rolling(window_s, min_periods=1).max().shift(-window_s)
    peak = v.rolling(20, min_periods=1).max().shift(-20)
    hits = ((v < start_max_kmh) & (ahead - v >= gain_kmh) & (peak >= peak_min_kmh)).fillna(False).to_numpy()
    return len(_merge(_segments(hits), merge_gap_s))


# --------------------------------------------------------------------------- per-ride stream metrics

def ride_stream_metrics(stream: pd.DataFrame, hr_rest: float, hr_max: float, z1: float, z2: float,
                        sprint_cfg: dict | None = None, segments: list[dict] | None = None) -> dict:
    """Everything derived from one activity's stream; cached by the build per activity + config hash."""
    out: dict = {"n_samples": int(len(stream))}
    if stream.empty:
        return out
    hr = stream["hr"]
    if hr.notna().any():
        out["trimp"] = trimp_stream(stream["t_s"], hr, hr_rest, hr_max)
        out["z1_s"], out["z2_s"], out["z3_s"] = zone_seconds(stream["t_s"], hr, z1, z2)
        out["beats"] = float(np.nansum(hr.to_numpy(dtype=float) * _dt_minutes(stream["t_s"])))
    be = best_efforts(stream)
    out.update({f"be_{d // 1000}": v for d, v in be.items() if v is not None})
    if segments:
        out["segs"] = [segment_efforts(stream, sg) for sg in segments]
    track = heat_track(stream)
    if track:
        out["track"] = track
    out["sample_interval_s"] = float(stream["t_s"].diff().median()) if len(stream) > 1 else None
    grid = to_1s(stream)
    has_power = "power" in stream and stream["power"].notna().sum() > 60
    if has_power:
        out["np"] = normalized_power(grid["power"])
        out["avg_power_1s"] = float(grid["power"].fillna(0).mean())
        out["mean_max"] = mean_max(grid["power"])
    if "speed" in grid:
        moving = grid["speed"] > 1.0
        out["moving_1s"] = int(moving.sum())
        out["stopped_share"] = float(1 - moving.mean()) if len(grid) else None
        if moving.any() and grid.loc[moving, "hr"].notna().any():
            out["m_per_beat"] = float(grid.loc[moving, "speed"].mean() / (grid.loc[moving, "hr"].mean() / 60))
        sc = sprint_cfg or {}
        out["sprints_speed"] = sprints_speed(grid["speed"].to_numpy(), sc.get("speed_gain_kmh", 10),
                                             sc.get("speed_start_max_kmh", 15), 10, sc.get("merge_gap_s", 20),
                                             sc.get("speed_peak_min_kmh", 30))
    if has_power and grid["hr"].notna().any() and grid["hr"].mean() > 0:
        out["power_per_hr"] = out["avg_power_1s"] / float(grid["hr"].mean())
    dec = decoupling(grid, use_power=has_power)
    if dec:
        out.update({f"dec_{k}": v for k, v in dec.items()})
    return out


# --------------------------------------------------------------------------- classification

def _dist_m(lat1, lon1, lat2, lon2) -> float:
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return float("inf")
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def endpoint_clusters(rides: pd.DataFrame, cell_m: float = 200) -> list[tuple[float, float]]:
    """The two most frequent start/end locations (rounded to ~cell_m), at least 1 km apart: home and work.
    Coordinates never leave the build; only the resulting labels go into the site payload."""
    pts = pd.concat([rides[["start_lat", "start_lon"]].set_axis(["lat", "lon"], axis=1),
                     rides[["end_lat", "end_lon"]].set_axis(["lat", "lon"], axis=1)]).dropna()
    if pts.empty:
        return []
    lat_step = cell_m / 111_000
    lon_step = cell_m / (111_000 * math.cos(math.radians(float(pts["lat"].median()))))
    cells = pts.assign(cl=(pts["lat"] / lat_step).round(), co=(pts["lon"] / lon_step).round())
    counts = cells.groupby(["cl", "co"]).agg(n=("lat", "size"), lat=("lat", "mean"), lon=("lon", "mean")) \
        .sort_values("n", ascending=False)
    centers: list[tuple[float, float]] = []
    for row in counts.itertuples():
        if all(_dist_m(row.lat, row.lon, c[0], c[1]) > 1000 for c in centers):
            centers.append((row.lat, row.lon))
        if len(centers) == 2:
            break
    return centers


def current_endpoints(rides: pd.DataFrame, since=None) -> list[tuple[float, float]]:
    """Home and work as of `since` (earlier years may have had other places): clusters from non-e-bike rides."""
    r = rides[~rides["sub_sport"].str.contains("e_bike", na=False)]
    if since is not None:
        r = r[pd.to_datetime(r["start_ts_local"]) >= pd.Timestamp(since)]
    return endpoint_clusters(r)


def classify_rides(rides: pd.DataFrame, cfg: dict, centers_since=None) -> pd.Series:
    """ebike > indoor > commute > long > other (6.1). Endpoints are taken from rides since `centers_since`."""
    c = cfg["cycling"]
    names = [n.lower() for n in c.get("ebike_names") or []]
    centers = current_endpoints(rides, centers_since)
    km = c["commute_km"] * 1000
    out = []
    for r in rides.itertuples():
        name = (r.name or "").lower()
        if "e_bike" in (r.sub_sport or "") or any(n in name for n in names):
            out.append("ebike")
        elif (r.sub_sport in ("indoor_cycling", "virtual_ride")) or not r.has_gps_stream:
            out.append("indoor")
        elif (pd.Timestamp(r.start_ts_local).weekday() < 5 and r.distance_m and 0.75 * km <= r.distance_m <= 1.25 * km
              and len(centers) == 2 and _is_commute(r, centers)):
            out.append("commute")
        elif (r.moving_s or r.duration_s or 0) >= c["long_ride_min_minutes"] * 60:
            out.append("long")
        else:
            out.append("other")
    return pd.Series(out, index=rides.index)


def _is_commute(r, centers, tol_m: float = 400) -> bool:
    a, b = centers
    s_a = _dist_m(r.start_lat, r.start_lon, *a) < tol_m
    s_b = _dist_m(r.start_lat, r.start_lon, *b) < tol_m
    e_a = _dist_m(r.end_lat, r.end_lon, *a) < tol_m
    e_b = _dist_m(r.end_lat, r.end_lon, *b) < tol_m
    return (s_a and e_b) or (s_b and e_a)


def eftp_series(ride_dates: pd.Series, best20: pd.Series, window_days: int = 90) -> pd.Series:
    """eFTP per power-ride date: 95% of the best 20-min power in the trailing window (labelled an estimate)."""
    df = pd.DataFrame({"date": pd.to_datetime(ride_dates), "p20": best20}).dropna().sort_values("date")
    out = {}
    for d in pd.to_datetime(df["date"].unique()):
        w = df[(df["date"] > d - pd.Timedelta(days=window_days)) & (df["date"] <= d)]
        out[pd.Timestamp(d)] = 0.95 * w["p20"].max()
    return pd.Series(out, dtype=float)


# --------------------------------------------------------------------------- weekly effort range (plan B)

EFFORT_BAND = (0.75, 1.25)
EFFORT_PRIOR_WEEKS = 3
EFFORT_OVER_FACTOR = 1.5      # attention: a completed week above 1.5 x the upper bound
EFFORT_UNDER_WEEKS = 2        # attention: this many completed weeks in a row below the lower bound


def effort_range(weekly_load: pd.Series, today, band=EFFORT_BAND) -> dict:
    """Weekly load vs the athlete's own recent range. `weekly_load` is indexed by week start (Monday), zero-filled,
    and runs up to the current week. Range for week w = mean of the 3 completed weeks before w x band; no range when
    fewer than 3 prior weeks exist or they carry no load. Also returns the attention flag for the last completed week."""
    s = weekly_load.astype(float)
    cur = pd.Timestamp(today).normalize() - pd.Timedelta(days=pd.Timestamp(today).weekday())
    weeks = []
    for i, (w, load) in enumerate(s.items()):
        prior = s.iloc[i - EFFORT_PRIOR_WEEKS:i] if i >= EFFORT_PRIOR_WEEKS else None
        base = float(prior.mean()) if prior is not None and prior.sum() > 0 else None
        weeks.append({"week": pd.Timestamp(w).date(), "load": float(load),
                      "lo": base * band[0] if base else None, "hi": base * band[1] if base else None,
                      "complete": pd.Timestamp(w) < cur})
    current = next((w for w in weeks if not w["complete"]), None)
    pace = None
    if current and current["lo"] is not None:
        frac = (pd.Timestamp(today).weekday() + 1) / 7
        pace = (current["lo"] + current["hi"]) / 2 / ((band[0] + band[1]) / 2) * frac  # mean of prior 3 weeks x elapsed fraction
    done = [w for w in weeks if w["complete"]]
    flag = None
    # judged on the most recent completed weeks only: a week without a range means no verdict, never an older week's
    if done and done[-1]["lo"] is not None and done[-1]["load"] > EFFORT_OVER_FACTOR * done[-1]["hi"]:
        flag = "over"
    elif len(done) >= EFFORT_UNDER_WEEKS and all(w["lo"] is not None and w["load"] < w["lo"] for w in done[-EFFORT_UNDER_WEEKS:]):
        flag = "under"
    return {"weeks": [{k: v for k, v in w.items() if k != "complete"} for w in weeks if w["complete"]],
            "current": ({"week": current["week"], "load": current["load"], "lo": current["lo"], "hi": current["hi"],
                         "pace_marker": pace} if current else None),
            "flag": flag}


# --------------------------------------------------------------------------- matched commute (plan A)

MATCH_TOL_M = 400          # start and end within this of the same endpoint
MATCH_DIST_BAND = 0.10     # distance within +-10 % of the group's median drops detours
MATCH_MIN_RIDES = 6
MATCH_ROLL = 5             # rolling median over the last 5 rides
MATCH_PRIOR_DAYS = 56      # "previous 8 weeks"


def match_routes(rides: pd.DataFrame, labels: tuple[str, str] = ("A", "B"), tol_m: float = MATCH_TOL_M,
                 centers: list[tuple[float, float]] | None = None) -> list[dict]:
    """Group rides that go between the same two endpoints in the same direction (plan A). `rides` must already
    exclude e-bike and indoor rides; needs start/end lat/lon, distance_m, moving_s, duration_s, avg_hr, beats,
    hr_quality, date, activity_id. Per ride: moving time, avg moving speed, heartbeats per km (chest strap only),
    avg HR, share of elapsed time stopped. Coordinates never leave this function."""
    centers = centers or endpoint_clusters(rides)
    if len(centers) < 2:
        return []

    def where(lat, lon):
        for i, c in enumerate(centers):
            if _dist_m(lat, lon, *c) < tol_m:
                return i
        return None

    ends = [(where(r.start_lat, r.start_lon), where(r.end_lat, r.end_lon)) for r in rides.itertuples()]
    out = []
    for a, b in ((0, 1), (1, 0)):
        sel = rides[[e == (a, b) for e in ends]]
        sel = sel[sel["distance_m"].notna() & sel["moving_s"].notna()]
        if len(sel) < MATCH_MIN_RIDES:
            continue
        med = sel["distance_m"].median()
        sel = sel[(sel["distance_m"] - med).abs() <= MATCH_DIST_BAND * med].sort_values("start_ts_local")
        if len(sel) < MATCH_MIN_RIDES:
            continue
        rows = []
        for r in sel.itertuples():
            km = r.distance_m / 1000
            strap = r.hr_quality == "strap"
            beats = getattr(r, "beats", None)
            rows.append({"date": pd.Timestamp(r.date).date(), "activity_id": int(r.activity_id), "moving_s": float(r.moving_s),
                         "kmh": km / (r.moving_s / 3600),
                         "beats_per_km": float(beats) / km if strap and beats is not None and not pd.isna(beats) else None,
                         "avg_hr": None if pd.isna(r.avg_hr) else float(r.avg_hr),
                         "stop_share": max(0.0, 1 - r.moving_s / r.duration_s) if r.duration_s else None, "strap": bool(strap)})
        df = pd.DataFrame(rows)
        for col in ("moving_s", "beats_per_km"):
            df[f"{col}_med5"] = df[col].rolling(MATCH_ROLL, min_periods=3).median()
        for r, m5, b5 in zip(rows, df["moving_s_med5"], df["beats_per_km_med5"]):
            r["moving_med5"] = None if pd.isna(m5) else float(m5)
            r["beats_med5"] = None if pd.isna(b5) else float(b5)
        latest = df.iloc[-1]
        prior = df[(pd.to_datetime(df["date"]) < pd.Timestamp(latest["date"]))
                   & (pd.to_datetime(df["date"]) >= pd.Timestamp(latest["date"]) - pd.Timedelta(days=MATCH_PRIOR_DAYS))]

        def delta(col):
            v, p = latest[col], prior[col].dropna()
            return float(v - p.median()) if len(p) >= 2 and not pd.isna(v) else None
        out.append({"id": f"{a}{b}", "label": f"{labels[a]} → {labels[b]}", "km": float(med / 1000), "n": len(rows),
                    "rides": rows, "trend": {"latest_date": latest["date"], "moving_s_delta": delta("moving_s"),
                                             "beats_per_km_delta": delta("beats_per_km"), "prior_n": int(len(prior))}})
    return out


# --------------------------------------------------------------------------- best efforts (plan C)

BEST_DISTANCES_M = (1000, 5000, 10000, 20000)
BEST_GAP_S = 10          # a recording gap longer than this invalidates every window spanning it
BEST_MAX_KMH = 70        # faster samples are GPS/sensor errors
BEST_MAX_JUMP_MPS = 50   # a GPS jump faster than this between two samples is an error


def best_efforts(stream: pd.DataFrame, distances=BEST_DISTANCES_M) -> dict[int, float | None]:
    """Fastest time (s) to cover each distance within one ride: a sliding window over cumulative distance on the 1-s
    grid. Distance is the integral of speed. Windows spanning a recording gap > 10 s, a sample above 70 km/h or a GPS
    jump above 50 m/s are invalid, not repaired. The start of a window is interpolated, so times are not quantised."""
    none = {d: None for d in distances}
    if stream.empty or "speed" not in stream or stream["speed"].notna().sum() < 2:
        return none
    s = stream.dropna(subset=["speed"]).copy()
    s["t"] = s["t_s"].round().astype(int)
    s = s.drop_duplicates("t", keep="last").reset_index(drop=True)
    if len(s) < 2:
        return none
    t0, n = int(s["t"].iloc[0]), int(s["t"].iloc[-1]) - int(s["t"].iloc[0]) + 1
    idx = (s["t"] - t0).to_numpy()
    brk = np.zeros(n + 1, dtype=bool)
    gap = np.diff(idx) > BEST_GAP_S
    if "lat" in s and "lon" in s:
        lat, lon = np.radians(s["lat"].to_numpy(dtype=float)), np.radians(s["lon"].to_numpy(dtype=float))
        dl, dn = np.diff(lat), np.diff(lon)
        a = np.sin(dl / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dn / 2) ** 2
        jump_m = 2 * 6371000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        with np.errstate(invalid="ignore", divide="ignore"):
            gap = gap | (np.nan_to_num(jump_m / np.maximum(np.diff(idx), 1)) > BEST_MAX_JUMP_MPS)
    for k in np.flatnonzero(gap):
        brk[idx[k] + 1: idx[k + 1] + 1] = True
    for k in idx[(s["speed"].to_numpy(dtype=float) * 3.6) > BEST_MAX_KMH]:
        brk[k: k + 2] = True
    speed = pd.Series(s["speed"].to_numpy(dtype=float), index=idx).reindex(range(n)).interpolate(limit_area="inside")
    cum = np.cumsum(np.nan_to_num(speed.to_numpy(), nan=0.0))
    bad = np.cumsum(brk[:n])
    out = {}
    for d in distances:
        if cum[-1] < d:
            out[d] = None
            continue
        j = np.flatnonzero(cum >= d)
        j = j[j > 0]
        target = cum[j] - d
        st = np.searchsorted(cum, target, side="right") - 1
        ok = (st >= 0) & (bad[j] - bad[np.clip(st, 0, None)] == 0)
        if not ok.any():
            out[d] = None
            continue
        j, st, target = j[ok], st[ok], target[ok]
        frac = (target - cum[st]) / np.maximum(cum[st + 1] - cum[st], 1e-9)
        out[d] = float((j - (st + frac)).min())
    return out


# --------------------------------------------------------------------------- personal segments (plan D)

SEG_MAX_GAP_S = 10        # a recording gap inside an effort invalidates it
SEG_DIST_BAND = 0.15      # ridden distance within +-15 % of the segment's median, otherwise a wrong turn


def _metres(lat, lon, lat0, lon0):
    return (np.asarray(lon, dtype=float) - lon0) * math.cos(math.radians(lat0)) * 111_320, (np.asarray(lat, dtype=float) - lat0) * 110_574


def _passes(x, y, px, py, radius):
    """Closest approach of the track polyline to the point, once per contiguous stretch within `radius`.
    Returns [(segment index i, fraction f along i -> i+1)]; the polyline, not the samples, so a fast rider whose
    samples straddle the circle still registers."""
    ax, ay, bx, by = x[:-1], y[:-1], x[1:], y[1:]
    dx, dy = bx - ax, by - ay
    L2 = np.maximum(dx * dx + dy * dy, 1e-9)
    f = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1)
    d = np.hypot(ax + f * dx - px, ay + f * dy - py)
    near = d <= radius
    out, i, n = [], 0, len(d)
    while i < n:
        if near[i]:
            j = i
            while j + 1 < n and near[j + 1]:
                j += 1
            k = i + int(np.argmin(d[i:j + 1]))
            out.append((k, float(f[k])))
            i = j + 1
        else:
            i += 1
    return out


def segment_efforts(stream: pd.DataFrame, seg: dict) -> list[dict]:
    """Efforts of one stream on a segment {start: [lat, lon], end: [lat, lon], radius_m}: a pass near the start followed
    by a pass near the end. Time and distance are interpolated at the closest-approach points; a ridden track with a
    recording gap > 10 s, or ridden against the segment's direction (end before start), yields nothing. The caller
    drops wrong turns with `filter_efforts` once the median distance is known."""
    s = stream.dropna(subset=["lat", "lon"])
    if len(s) < 5:
        return []
    lat0, lon0 = seg["start"]
    x, y = _metres(s["lat"], s["lon"], lat0, lon0)
    sx, sy = 0.0, 0.0
    ex, ey = _metres(seg["end"][0], seg["end"][1], lat0, lon0)
    ends = _passes(x, y, float(ex), float(ey), seg.get("radius_m", 30))
    starts = _passes(x, y, sx, sy, seg.get("radius_m", 30))
    if not starts or not ends:
        return []
    t = s["t_s"].to_numpy(dtype=float)
    cum = np.r_[0.0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
    at = lambda arr, p: float(arr[p[0]] + p[1] * (arr[p[0] + 1] - arr[p[0]]))
    out = []
    for a in starts:
        e = next((b for b in ends if (b[0], b[1]) > (a[0], a[1])), None)
        if e is None:
            continue
        i0, i1 = a[0] + 1, e[0]          # samples strictly inside the effort
        if np.any(np.diff(t[a[0]:e[0] + 2]) > SEG_MAX_GAP_S):
            continue
        dur, dist = at(t, e) - at(t, a), at(cum, e) - at(cum, a)
        if dur <= 0 or dist <= 0 or dist / dur * 3.6 > BEST_MAX_KMH:
            continue
        inner = s.iloc[i0:i1 + 1] if i1 >= i0 else s.iloc[0:0]
        alt = s["altitude"].to_numpy(dtype=float) if "altitude" in s else None
        out.append({"time_s": dur, "dist_m": dist,
                    "avg_hr": float(inner["hr"].mean()) if len(inner) and inner["hr"].notna().any() else None,
                    "avg_power": float(inner["power"].mean()) if len(inner) and inner["power"].notna().sum() > 2 else None,
                    "gain_m": (at(alt, e) - at(alt, a)) if alt is not None and not np.isnan(alt[[a[0], e[0]]]).any() else None})
    return out


def filter_efforts(efforts_by_ride: dict, band: float = SEG_DIST_BAND) -> tuple[dict, float | None]:
    """One effort per ride: the fastest whose ridden distance is within +-band of the median over all candidates
    (rides that took a wrong turn or a detour fall outside). Returns ({ride: effort}, median distance)."""
    dists = [e["dist_m"] for v in efforts_by_ride.values() for e in v]
    if not dists:
        return {}, None
    med = float(np.median(dists))
    kept = {}
    for k, v in efforts_by_ride.items():
        ok = [e for e in v if abs(e["dist_m"] - med) <= band * med]
        if ok:
            kept[k] = min(ok, key=lambda e: e["time_s"])
    return kept, med


# --------------------------------------------------------------------------- heatmap track (plan F)

HEAT_TRIM_M = 200      # privacy: the first and last 200 m of every ride are dropped (home, work)
HEAT_TOL_M = 8         # Douglas-Peucker tolerance
HEAT_SPLIT_M = 400     # a jump bigger than this between samples starts a new part (GPS glitch, tunnel)


def _douglas_peucker(x: np.ndarray, y: np.ndarray, tol: float) -> np.ndarray:
    n = len(x)
    keep = np.zeros(n, dtype=bool)
    keep[[0, n - 1]] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        L = math.hypot(dx, dy)
        px, py = x[a + 1:b] - x[a], y[a + 1:b] - y[a]
        d = np.hypot(px, py) if L < 1e-9 else np.abs(px * dy - py * dx) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return np.flatnonzero(keep)


def heat_track(stream: pd.DataFrame) -> list[list[int]] | None:
    """Simplified, privacy-trimmed ride line for the heatmap: parts of [lat1e5, lon1e5, dlat, dlon, ...] (1e-5 deg, about
    1 m). None for rides without GPS or shorter than the trim."""
    if "lat" not in stream:
        return None
    s = stream.dropna(subset=["lat", "lon"])
    if len(s) < 10:
        return None
    lat, lon = s["lat"].to_numpy(dtype=float), s["lon"].to_numpy(dtype=float)
    x, y = _metres(lat, lon, float(np.median(lat)), float(np.median(lon)))
    step = np.hypot(np.diff(x), np.diff(y))
    cum = np.r_[0.0, np.cumsum(np.where(step > HEAT_SPLIT_M, 0.0, step))]
    total = cum[-1]
    if total < 2 * HEAT_TRIM_M + 300:
        return None
    keep = (cum >= HEAT_TRIM_M) & (cum <= total - HEAT_TRIM_M)
    cut = np.flatnonzero(np.r_[False, step > HEAT_SPLIT_M])
    parts, idx = [], np.flatnonzero(keep)
    for chunk in np.split(idx, np.searchsorted(idx, cut)) if len(cut) else [idx]:
        if len(chunk) < 2:
            continue
        sel = chunk[_douglas_peucker(x[chunk], y[chunk], HEAT_TOL_M)]
        la, lo = np.round(lat[sel] * 1e5).astype(int), np.round(lon[sel] * 1e5).astype(int)
        parts.append([int(la[0]), int(lo[0])] + [int(v) for pair in zip(np.diff(la), np.diff(lo)) for v in pair])
    return parts or None
