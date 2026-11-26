"""Unit tests on synthetic data for every formula in plan section 7."""

import math

import numpy as np
import pandas as pd
import pytest

from src.metrics import common, cycling, health, strength


# ---------------------------------------------------------------- 7.1 TRIMP
def test_trimp_constant_hr_matches_formula():
    t = np.arange(0, 3600, 1.0)  # 1 h at 1 s
    hr = np.full_like(t, 150.0)
    hrr = (150 - 60) / (190 - 60)
    expected = 60 * hrr * 0.64 * math.exp(1.92 * hrr)
    assert cycling.trimp_stream(t, hr, 60, 190) == pytest.approx(expected, rel=1e-3)
    assert cycling.trimp_avg(150, 3600, 60, 190) == pytest.approx(expected, rel=1e-9)


def test_trimp_clamps_and_caps_gaps():
    assert cycling.trimp_stream([0, 1, 2], [40, 40, 40], 60, 190) == 0  # below rest -> 0
    # a 10-minute recording gap counts at most 30 s
    t = np.array([0, 1, 601, 602], dtype=float)
    hr = np.full(4, 190.0)
    full = 0.64 * math.exp(1.92)
    assert cycling.trimp_stream(t, hr, 60, 190) == pytest.approx((1 + 1 + 30 + 1) / 60 * full, rel=1e-6)


def test_zone_seconds():
    t = np.arange(0, 300, 1.0)
    hr = np.r_[np.full(100, 120.0), np.full(100, 160.0), np.full(100, 175.0)]
    z1, z2, z3 = cycling.zone_seconds(t, hr, 151.2, 166.3)
    assert (z1, z2, z3) == (100, 100, 100)


# ---------------------------------------------------------------- 7.2 CTL / ATL / TSB
def test_ctl_converges_to_constant_load():
    idx = pd.date_range("2026-01-01", periods=400, freq="D")
    ff = common.fitness_fatigue(pd.Series(50.0, index=idx))
    assert ff["ctl"].iloc[-1] == pytest.approx(50, abs=1e-6)
    assert ff["atl"].iloc[-1] == pytest.approx(50, abs=1e-6)


def test_tsb_is_yesterdays_ctl_minus_atl_and_zero_days_fill():
    load = pd.Series([100.0, 0.0, 80.0], index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]))
    ff = common.fitness_fatigue(load)
    assert len(ff) == 5 and ff["load"].tolist() == [100, 0, 0, 0, 80]
    for i in range(1, 5):
        assert ff["tsb"].iloc[i] == pytest.approx(ff["ctl"].iloc[i - 1] - ff["atl"].iloc[i - 1])


# ---------------------------------------------------------------- 7.3 power
def test_np_and_tss_at_ftp():
    p = np.full(3600, 200.0)
    assert cycling.normalized_power(p) == pytest.approx(200)
    assert cycling.tss(3600, 200, 200) == pytest.approx(100)


def test_np_exceeds_average_for_variable_power():
    p = np.tile(np.r_[np.full(60, 300.0), np.full(60, 100.0)], 30)
    assert cycling.normalized_power(p) > p.mean()


def test_mean_max():
    p = np.r_[np.full(100, 100.0), np.full(20, 400.0), np.full(100, 100.0)]
    mm = cycling.mean_max(p, [5, 20, 60])
    assert mm[5] == pytest.approx(400) and mm[20] == pytest.approx(400)
    assert mm[60] == pytest.approx((20 * 400 + 40 * 100) / 60)


def test_eftp_trailing_window():
    dates = pd.Series(pd.to_datetime(["2026-01-01", "2026-02-01", "2026-06-01"]))
    best20 = pd.Series([200.0, 150.0, 100.0])
    e = cycling.eftp_series(dates, best20)
    assert e.iloc[0] == pytest.approx(190) and e.iloc[1] == pytest.approx(190)
    assert e.iloc[2] == pytest.approx(95)  # Jan/Feb fell out of the 90-day window


# ---------------------------------------------------------------- 7.4 decoupling
def test_decoupling_speed_based():
    n = 4000
    grid = pd.DataFrame({"hr": np.r_[np.full(n // 2, 130.0), np.full(n // 2, 143.0)],
                         "speed": np.full(n, 7.0), "power": np.nan})
    d = cycling.decoupling(grid, use_power=False)
    assert d["decoupling_pct"] == pytest.approx((1 - 130 / 143) * 100, rel=1e-6)


# ---------------------------------------------------------------- 7.5 sprints
def test_sprints_power_counts_and_merges():
    p = np.full(600, 100.0)
    p[100:115] = 500   # 15 s effort
    p[125:135] = 500   # 10 s, 10 s later -> merged with the first
    p[400:412] = 500   # separate effort
    p[500:503] = 500   # too short
    assert cycling.sprints_power(p, threshold_w=300, min_s=8, merge_gap_s=20) == 2


def test_sprints_speed_acceleration_from_low_speed():
    v = np.r_[np.full(30, 10 / 3.6), np.linspace(10, 32, 8) / 3.6, np.full(60, 32 / 3.6),
              np.linspace(32, 25, 10) / 3.6]
    assert cycling.sprints_speed(v) == 1
    assert cycling.sprints_speed(np.full(100, 30 / 3.6)) == 0  # fast cruising is not a sprint
    pull_away = np.r_[np.zeros(10), np.linspace(0, 24, 10) / 3.6, np.full(60, 24 / 3.6)]
    assert cycling.sprints_speed(pull_away) == 0  # traffic-light start to 24 km/h is not a sprint


# ---------------------------------------------------------------- 7.6 e1RM
def test_e1rm_epley_and_rep_range():
    assert strength.e1rm(100, 10) == pytest.approx(100 * (1 + 10 / 30))
    assert math.isnan(strength.e1rm(100, 13))
    assert math.isnan(strength.e1rm(0, 5))


def test_progression_status():
    assert strength.progression_status([100, 100, 100]) == "new"
    assert strength.progression_status([100, 100, 100, 102, 104, 106]) == "progressing"
    assert strength.progression_status([100, 100, 100, 100, 99.5, 100]) == "flat"
    assert strength.progression_status([100, 100, 100, 90, 90, 90]) == "regressing"


def test_trend_e1rm_covers_high_reps():
    assert strength.trend_e1rm(7, 15) == pytest.approx(7 * 1.5)
    assert strength.trend_e1rm(100, 10) == pytest.approx(strength.e1rm(100, 10))
    assert math.isnan(strength.trend_e1rm(7, 21))
    assert math.isnan(strength.trend_e1rm(0, 15))


def test_high_rep_sessions_count_for_the_trend():
    # DB Curl as logged (2025-06 .. 2026-11-26): the two newest sessions are 3x15 @ 7 kg. With the 1-12 rep e1RM
    # they vanished and the block was compared with Aug 2025 -> "regressing". With the trend e1RM it progresses.
    log = [(7, 10), (7, 16), (7, 12), (9, 15), (5, 15), (6, 15), (5, 12), (5, 12), (6, 12), (6, 12), (7, 15), (7, 15)]
    assert strength.progression_status([strength.e1rm(w, r) for w, r in log]) == "regressing"
    assert strength.progression_status([strength.trend_e1rm(w, r) for w, r in log]) == "progressing"
    # A real drop at normal reps still regresses (Back Squat after the break: 70x8 -> 30-40x10).
    squat = [(70, 8), (70, 8), (70, 8), (30, 10), (35, 10), (40, 10)]
    assert strength.progression_status([strength.trend_e1rm(w, r) for w, r in squat]) == "regressing"


# ---------------------------------------------------------------- 7.7 baselines and flags
def test_rolling_baseline_excludes_current_window_and_needs_30_days():
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    v = pd.Series(50.0, index=idx)
    v.iloc[-7:] = 60.0
    v.iloc[::2] += 2  # some spread so sd > 0
    b = common.rolling_baseline(v, 60, 7, 30)
    last = b.iloc[-1]
    assert last["baseline"] == pytest.approx(v.iloc[-67:-7].mean())
    assert last["short"] == pytest.approx(v.iloc[-7:].mean())
    assert math.isnan(b["baseline"].iloc[30])  # only 24 days of baseline available there


def test_flag_directions():
    assert common.flag(-1.5, "low") == "watch"
    assert common.flag(1.5, "low") == "in_range"
    assert common.flag(2.5, "high") == "out_of_range"
    assert common.flag(-2.5, "high") == "in_range"
    assert common.flag(0.5) == "in_range"
    assert common.flag(float("nan")) == "no_baseline"


# ---------------------------------------------------------------- 7.8 / 7.9
def test_sleep_midpoint_wraps_around_midnight():
    assert health.sleep_midpoint_minutes(pd.Timestamp("2026-01-01 23:00"), 3600) == pytest.approx(-30)
    assert health.sleep_midpoint_minutes(pd.Timestamp("2026-01-01 23:00"), 8 * 3600) == pytest.approx(180)


def test_weight_ema_carries_forward():
    s = pd.Series([70.0, 72.0], index=pd.to_datetime(["2026-01-01", "2026-01-03"]))
    e = health.weight_ema(s)
    assert len(e) == 3
    assert e.iloc[1] == pytest.approx(70.0)
    assert e.iloc[2] == pytest.approx(70 + 0.1 * 2)


def test_strain_days_requires_two_consecutive():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    rhr = pd.Series([2, 2, 0, 2, 0.0], index=idx)
    hrv = pd.Series([-2, -2, 0, -2, 0.0], index=idx)
    resp = pd.Series([2, 2, 0, 2, 0.0], index=idx)
    assert health.strain_days(rhr, hrv, resp).tolist() == [True, True, False, False, False]


# ---------------------------------------------------------------- 7.10 muscle volume / recency
def _sets(rows):
    df = pd.DataFrame(rows, columns=["date", "exercise", "reps", "weight_kg", "implements"])
    return df.assign(source="manual", set_type="active", workout_id=1, activity_id=None, set_index=range(len(df)),
                     exercise_raw=df["exercise"], duration_s=None, to_failure=0)


def test_three_bench_sets_weighted():
    emap = strength.load_exercise_map()
    s = strength.prepare_sets(_sets([("2026-01-05", "Bench Press", 10, 50.0, 1)] * 3), emap, pd.Series(dtype=float), 68)
    mr, unmapped = strength.muscle_rows(s, emap)
    ws = strength.weighted_sets(mr, "2026-01-01", "2026-01-31")
    assert ws["chest"] == 3.0 and ws["front_delts"] == 1.5 and ws["triceps"] == 1.5
    assert unmapped == []


def test_recency_halves_after_half_life():
    mr = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "session": [1], "exercise": ["Bench Press"],
                       "muscle": ["chest"], "w": [1.0], "load": [500.0]})
    r0 = strength.recency(mr, "2026-01-01", 3)["chest"]
    r3 = strength.recency(mr, "2026-01-04", 3)["chest"]
    assert r3 == pytest.approx(r0 / 2)


def test_unmapped_exercise_reported_and_warmups_excluded():
    emap = strength.load_exercise_map()
    rows = [("2026-01-05", "Zercher Carry", 10, 20.0, 1),
            ("2026-01-05", "Back Squat", 10, 20.0, 1), ("2026-01-05", "Back Squat", 10, 60.0, 1)]
    s = strength.prepare_sets(_sets(rows), emap, pd.Series(dtype=float), 68)
    assert s.loc[(s.exercise == "Back Squat") & (s.weight_kg == 20), "working"].item() is False or \
        not s.loc[(s.exercise == "Back Squat") & (s.weight_kg == 20), "working"].item()
    _, unmapped = strength.muscle_rows(s, emap)
    assert unmapped == ["Zercher Carry"]


def test_bodyweight_lift_uses_bodyweight():
    emap = strength.load_exercise_map()
    bw = pd.Series([70.0], index=pd.to_datetime(["2025-12-01"]))
    s = strength.prepare_sets(_sets([("2026-01-05", "Pull-up", 6, None, None)]), emap, bw, 68)
    assert s["eff_weight"].item() == 70.0
    assert s["e1rm"].item() == pytest.approx(70 * 1.2)


# ---------------------------------------------------------------- muscle map consistency
def test_body_svg_regions_match_exercise_map():
    import re
    from pathlib import Path
    svg = (Path(__file__).resolve().parents[1] / "site" / "static" / "body.svg").read_text()
    svg_ids = set(re.findall(r'data-muscle="([a-z_]+)"', svg))
    emap = strength.load_exercise_map()
    assert svg_ids == set(emap["regions"])
    used = {m for ex in emap["exercises"].values() for m in ex["muscles"]}
    assert used <= set(emap["regions"])


# ---------------------------------------------------------------- plan B: weekly effort range
def _weekly(loads):
    idx = pd.date_range("2026-09-07", periods=len(loads), freq="7D")  # Mondays
    return pd.Series(loads, index=idx, dtype=float)


def test_effort_range_values_and_empty_prior_weeks():
    r = cycling.effort_range(_weekly([100, 200, 300, 400, 0, 0, 0, 50, 10]), "2026-11-02")
    w = {x["week"].isoformat(): x for x in r["weeks"]}
    assert (w["2026-09-28"]["lo"], w["2026-09-28"]["hi"]) == pytest.approx((150, 250))
    assert w["2026-09-14"]["lo"] is None  # fewer than 3 prior weeks
    assert w["2026-10-26"]["lo"] is None  # three prior weeks without any load: no range, not 0
    assert r["current"]["week"].isoformat() == "2026-11-02"


def test_effort_current_week_pace_marker():
    r = cycling.effort_range(_weekly([200, 200, 200, 90]), "2026-09-30")  # Wednesday: 3 of 7 days
    assert r["current"]["pace_marker"] == pytest.approx(200 * 3 / 7)
    assert r["current"]["load"] == 90 and r["weeks"][-1]["load"] == 200


def test_effort_flag_only_on_thresholds():
    base = [100, 100, 100]
    assert cycling.effort_range(_weekly(base + [10]), "2026-09-30")["flag"] is None  # only 3 weeks: no range yet
    # last completed week 160 > 1.5 x 125 = 187.5? no -> calm
    assert cycling.effort_range(_weekly(base + [100, 160, 0]), "2026-10-12")["flag"] is None
    assert cycling.effort_range(_weekly(base + [100, 200, 0]), "2026-10-12")["flag"] == "over"
    # two completed weeks in a row below 75 %
    assert cycling.effort_range(_weekly(base + [50, 50, 0]), "2026-10-12")["flag"] == "under"
    assert cycling.effort_range(_weekly(base + [50, 100, 0]), "2026-10-12")["flag"] is None


# ---------------------------------------------------------------- plan A: matched commute
HOME, WORK = (48.1000, 11.5000), (48.1900, 11.5000)  # ~10 km apart


def _ride(i, a, b, km=10.0, moving=1800, strap=True, beats=None, hr=140.0):
    start, end = (HOME, WORK) if a == "h" else (WORK, HOME)
    return {"activity_id": i, "date": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
            "start_ts_local": pd.Timestamp("2026-09-01 08:00") + pd.Timedelta(days=i),
            "start_lat": start[0] + 0.0004, "start_lon": start[1], "end_lat": end[0], "end_lon": end[1] + 0.0004,
            "distance_m": km * 1000, "moving_s": moving, "duration_s": moving + 300, "avg_hr": hr,
            "beats": hr * moving / 60 if beats is None else beats, "hr_quality": "strap" if strap else "wrist"}


def test_match_routes_keeps_directions_apart_and_drops_detours():
    rows = [_ride(i, "h", "w") for i in range(8)] + [_ride(20 + i, "w", "h") for i in range(7)]
    rows.append(_ride(40, "h", "w", km=12.5))  # a detour: > 10 % longer than the median
    groups = cycling.match_routes(pd.DataFrame(rows), ("Home", "Work"))
    by = {g["label"]: g for g in groups}
    assert set(by) == {"Home → Work", "Work → Home"}
    assert by["Home → Work"]["n"] == 8 and by["Work → Home"]["n"] == 7  # detour not in either group


def test_match_routes_needs_six_rides_and_skips_other_endpoints():
    rows = [_ride(i, "h", "w") for i in range(5)] + [_ride(20 + i, "w", "h") for i in range(8)]
    rows.append({**_ride(50, "h", "w"), "end_lat": 48.3, "end_lon": 11.9})  # ends elsewhere
    groups = cycling.match_routes(pd.DataFrame(rows), ("Home", "Work"))
    assert [g["label"] for g in groups] == ["Work → Home"]


def test_beats_per_km_known_value_strap_only_and_trend():
    rows = [_ride(i, "h", "w", moving=1800 - 30 * i, hr=140) for i in range(8)]
    rows.append(_ride(-1, "h", "w", moving=1800, strap=False))  # earliest ride
    g = cycling.match_routes(pd.DataFrame(rows))[0]
    first = g["rides"][1]  # ride 0 (the wrist ride, id -1, sorts first)
    assert first["beats_per_km"] == pytest.approx(140 * 1800 / 60 / 10)  # 420 beats over 10 km = 42 per km
    assert first["kmh"] == pytest.approx(20.0) and first["stop_share"] == pytest.approx(300 / 2100)
    assert [r["beats_per_km"] is None for r in g["rides"]].count(True) == 1  # the wrist ride leaves it empty
    assert g["rides"][-1]["moving_med5"] is not None
    assert g["trend"]["moving_s_delta"] < 0  # the latest ride is faster than the earlier ones


def test_stream_beats_for_constant_hr():
    t = np.arange(0, 600, 1.0)
    m = cycling.ride_stream_metrics(pd.DataFrame({"t_s": t, "hr": 120.0, "power": np.nan, "speed": 5.0, "cadence": np.nan}),
                                    60, 190, 150, 166)
    assert m["beats"] == pytest.approx(120 * 10, rel=1e-3)


# ---------------------------------------------------------------- plan C: best efforts
def _speed_stream(speeds, t=None):
    sp = np.asarray(speeds, dtype=float)
    t = np.arange(len(sp), dtype=float) if t is None else np.asarray(t, dtype=float)
    return pd.DataFrame({"t_s": t, "speed": sp, "lat": np.nan, "lon": np.nan})


def test_best_efforts_known_fastest_windows():
    # 600 s at 5 m/s, then 600 s at 10 m/s, then 600 s at 5 m/s
    be = cycling.best_efforts(_speed_stream([5.0] * 600 + [10.0] * 600 + [5.0] * 600))
    assert be[1000] == pytest.approx(100, abs=1.5)
    assert be[5000] == pytest.approx(500, abs=1.5)
    assert be[10000] is None or be[10000] > 600  # only 6 km at 10 m/s: the window must include slower stretches
    assert be[20000] is None  # the ride is 12 km long


def test_best_efforts_gap_invalidates_window():
    # the only fast stretch (10 m/s) contains a 60 s recording gap; everything else is 5 m/s
    t = np.r_[np.arange(0, 300), np.arange(360, 960)].astype(float)
    sp = np.r_[np.full(300, 5.0), np.full(600, 10.0)]
    be = cycling.best_efforts(_speed_stream(sp, t[:900]))
    assert be[1000] == pytest.approx(100, abs=1.5)  # windows fully inside the fast part after the gap still count
    t2 = np.r_[np.arange(0, 100), np.arange(160, 260)].astype(float)  # 60 s gap in a 5 m/s ride: 1 km needs 200 s
    be2 = cycling.best_efforts(_speed_stream(np.full(200, 5.0), t2))
    assert be2[1000] is None  # no 1 km window avoids the gap (500 m before, 500 m after)


def test_best_efforts_speed_spike_is_dropped():
    sp = np.full(600, 5.0)
    sp[300] = 40.0  # 144 km/h
    be = cycling.best_efforts(_speed_stream(sp))
    assert be[1000] == pytest.approx(200, abs=1.5)


def test_best_efforts_gps_jump_is_dropped():
    sp = np.full(600, 5.0)
    s = _speed_stream(sp)
    s["lat"] = 48.0 + np.arange(600) * 5.0 / 111_000
    s["lon"] = 11.0
    s.loc[300:, "lat"] += 0.05  # a 5.5 km teleport at t=300
    assert cycling.best_efforts(s)[1000] == pytest.approx(200, abs=1.5)  # windows across the jump are invalid


def test_effort_flag_never_judges_a_week_without_range():
    # last completed week (229) has no range: no verdict, even though the weeks before it were "low"
    assert cycling.effort_range(_weekly([700, 700, 0, 0, 0, 229, 0]), "2026-10-19")["flag"] is None


# ---------------------------------------------------------------- plan D: segments
SEG = {"start": [48.1000, 11.5000], "end": [48.1090, 11.5000], "radius_m": 30}  # 1 km north


def _track(lats, lons, dt=3.0, speed=6.0):
    n = len(lats)
    return pd.DataFrame({"t_s": np.arange(n) * dt, "lat": lats, "lon": lons, "hr": 140.0, "power": np.nan, "altitude": 500.0, "speed": speed})


def _line(lat_from, lat_to, n, lon=11.5):
    return np.linspace(lat_from, lat_to, n), np.full(n, lon)


def test_segment_effort_in_the_defined_direction_only():
    la, lo = _line(48.0950, 48.1140, 120)  # rides through start then end, northbound
    eff = cycling.segment_efforts(_track(la, lo), SEG)
    assert len(eff) == 1 and eff[0]["dist_m"] == pytest.approx(1000, rel=0.02)
    assert eff[0]["time_s"] == pytest.approx(3.0 * 119 * (0.0090 / 0.0190), rel=0.03)  # interpolated at the pass points
    assert eff[0]["avg_hr"] == 140
    la, lo = _line(48.1140, 48.0950, 120)  # southbound: end is reached before the start
    assert cycling.segment_efforts(_track(la, lo), SEG) == []


def test_segment_near_miss_outside_radius_is_ignored():
    la, lo = _line(48.0950, 48.1140, 120, lon=11.5008)  # ~59 m east of the line, radius is 30 m
    assert cycling.segment_efforts(_track(la, lo), SEG) == []


def test_segment_gap_invalidates_effort_and_sparse_samples_still_register():
    la, lo = _line(48.0950, 48.1140, 60)  # 6 s spacing: samples ~ 35 m apart straddle the 30 m circle
    t = _track(la, lo, dt=6.0)
    assert len(cycling.segment_efforts(t, SEG)) == 1
    t.loc[30:, "t_s"] += 40  # a 40 s hole in the middle of the effort
    assert cycling.segment_efforts(t, SEG) == []


def test_wrong_turn_is_rejected_by_the_distance_rule():
    a = {"dist_m": 1000, "time_s": 170}
    eff = {1: [a], 2: [{"dist_m": 1010, "time_s": 160}], 3: [{"dist_m": 1020, "time_s": 175}], 4: [{"dist_m": 1600, "time_s": 260}]}
    kept, med = cycling.filter_efforts(eff)
    assert med == pytest.approx(1015) and set(kept) == {1, 2, 3}


# ---------------------------------------------------------------- plan F: heatmap track
def test_heat_track_trims_200m_ends_and_encodes_deltas():
    la = np.linspace(48.0, 48.0 + 5000 / 110_574, 400)  # 5 km north
    parts = cycling.heat_track(_track(la, np.full(400, 11.0)))
    assert len(parts) == 1
    flat = parts[0]
    lat, lon = [flat[0]], [flat[1]]
    for i in range(2, len(flat), 2):
        lat.append(lat[-1] + flat[i]); lon.append(lon[-1] + flat[i + 1])
    lat = np.array(lat) / 1e5
    assert (lat.min() - 48.0) * 110_574 == pytest.approx(200, abs=15) and (48.0 + 5000 / 110_574 - lat.max()) * 110_574 == pytest.approx(200, abs=15)
    assert len(lat) <= 3  # a straight line simplifies to its end points
    assert cycling.heat_track(_track(*_line(48.0, 48.002, 50))) is None  # shorter than the trim
