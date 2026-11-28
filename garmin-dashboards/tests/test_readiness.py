import math

import numpy as np
import pandas as pd

from src.metrics import readiness as rd

GOOD = {"hrv": 50, "hrv_z": 0.2, "hrv_status": "BALANCED", "rhr": 52, "rhr_z": -0.1, "sleep_h": 8.0, "need_h": 8.0,
        "debt_h": 0.5, "load_ratio": 1.0, "strain": False}


def status(out, key):
    return next(f["status"] for f in out["factors"] if f["key"] == key)


def test_all_in_range_is_go():
    out = rd.assess(GOOD)
    assert out["verdict"] == "go" and out["reason"] == "All signals in range."


def test_one_caution_is_go_with_a_watch_note():
    out = rd.assess(GOOD | {"sleep_h": 7.0})                                         # 87.5 % of need: caution
    assert out["verdict"] == "go" and out["reason"].startswith("Worth watching: Last night")


def test_related_signals_count_once():
    assert rd.assess(GOOD | {"sleep_h": 7.0, "debt_h": 4.0})["verdict"] == "go"      # sleep domain only
    assert rd.assess(GOOD | {"hrv_z": -1.3, "rhr_z": 1.4})["verdict"] == "go"        # body domain only


def test_cautions_in_two_domains_is_easy():
    out = rd.assess(GOOD | {"hrv_z": -1.3, "sleep_h": 7.0})
    assert out["verdict"] == "easy" and out["reason"] == "Off: HRV, Last night."
    assert rd.assess(GOOD | {"rhr_z": 1.4, "load_ratio": 1.4})["verdict"] == "easy"


def test_load_alone_never_makes_a_day_easy():
    assert rd.assess(GOOD | {"load_ratio": 1.7})["verdict"] == "go"
    assert status(rd.assess(GOOD | {"load_ratio": 1.7}), "load") == "poor"           # still shown as poor
    assert rd.assess(GOOD | {"load_ratio": 1.7, "sleep_h": 7.0})["verdict"] == "easy"
    assert rd.assess(GOOD | {"load_ratio": 1.7, "debt_h": 7})["verdict"] == "easy"   # capped: not two poor domains


def test_one_poor_domain_is_easy_strong_signals_are_rest():
    assert rd.assess(GOOD | {"hrv_z": -2.3})["verdict"] == "easy"
    assert rd.assess(GOOD | {"rhr_z": 2.4})["verdict"] == "easy"
    assert rd.assess(GOOD | {"sleep_h": 5.5})["verdict"] == "easy"
    assert rd.assess(GOOD | {"strain": True})["verdict"] == "rest"
    assert rd.assess(GOOD | {"hrv_z": -2.3, "rhr_z": 2.4})["verdict"] == "rest"
    assert rd.assess(GOOD | {"hrv_z": -2.3, "sleep_h": 5.5})["verdict"] == "rest"   # body and sleep both poor


def test_garmin_low_hrv_status_lifts_ok_to_caution():
    assert status(rd.assess(GOOD | {"hrv_status": "LOW"}), "hrv") == "caution"


def test_missing_night_is_unknown_and_missing_factors_are_ignored():
    none = {k: None for k in GOOD} | {"strain": False}
    assert rd.assess(none)["verdict"] == "unknown"
    out = rd.assess(GOOD | {"load_ratio": None, "debt_h": None})
    assert out["verdict"] == "go" and status(out, "load") is None


def test_sleep_debt_pays_back_and_floors_at_zero():
    idx = pd.date_range("2026-01-01", periods=7)
    need = pd.Series(8.0, index=idx)
    sleep = pd.Series([6, 6, 6, 6, 6, 6, 6], index=idx, dtype=float)
    assert rd.sleep_debt(sleep, need).iloc[-1] == 14.0
    sleep2 = pd.Series([6, 10, 6, 10, 8, 8, 8], index=idx, dtype=float)  # short nights repaid by long ones
    assert rd.sleep_debt(sleep2, need).iloc[-1] == 0.0
    sleep3 = pd.Series([10] * 7, index=idx, dtype=float)
    assert rd.sleep_debt(sleep3, need).iloc[-1] == 0.0                     # surplus never becomes negative debt
    naps = pd.Series([0.5, None, 1.0, 0, 0, 0.5, 0], index=idx, dtype=float)
    assert rd.sleep_debt(sleep, need, nap_h=naps).iloc[-1] == 12.0        # naps pay back, missing = no nap


def test_load_ratio_needs_history_and_detects_spike():
    idx = pd.date_range("2026-01-01", periods=35)
    load = pd.Series(20.0, index=idx)
    assert math.isclose(rd.load_ratio(load).iloc[-1], 1.0)
    load.iloc[-7:] = 40.0
    r = rd.load_ratio(load).iloc[-1]
    assert r > 1.5 and rd.rate(r, rd.LOAD_RATIO, low_is_bad=False) == "poor"
    assert np.isnan(rd.load_ratio(pd.Series(1.0, index=idx)).iloc[-1])   # too little chronic load: no ratio


def test_need_reasons_text():
    txt = rd.need_reasons({"need_hrv_adj": "INCREASING", "need_training_adj": "NO_CHANGE", "need_history_adj": "DECREASING"},
                          8 + 10 / 60, 7 + 50 / 60)
    assert txt == "Need 8 h 10 min (usual 7 h 50 min): + HRV is low, − recent good sleep"
    assert rd.need_reasons({}, 8.0, 8.0) == "Need 8 h 00 min"


# ---------------------------------------------------------------- next session + plan
from src.metrics import strength as st

EMAP = {"regions": {"chest": {"label": "Chest", "group": "push"}, "triceps": {"label": "Triceps", "group": "push"},
                    "lats": {"label": "Lats", "group": "pull"}, "front_delts": {"label": "Front delts", "group": "shoulders"},
                    "quads": {"label": "Quads", "group": "legs"}},
        "exercises": {"Bench Press": {"muscles": {"chest": 1.0, "triceps": 0.5}}, "DB Pullover": {"muscles": {"lats": 1.0, "chest": 0.5}},
                      "Pull-up": {"muscles": {"lats": 1.0}}, "Overhead Press": {"muscles": {"front_delts": 1.0, "triceps": 0.5}},
                      "Back Squat": {"muscles": {"quads": 1.0}}}}
SPLIT = ["push", "pull", "shoulders"]


def _mr(rows):  # (date, exercise) -> muscle rows like strength.muscle_rows
    out = [{"date": pd.Timestamp(d), "session": i, "exercise": e, "muscle": m, "w": w, "load": 0.0}
           for i, (d, e) in enumerate(rows) for m, w in EMAP["exercises"][e]["muscles"].items()]
    return pd.DataFrame(out)


def _logged(rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "type": t} for d, t in rows])


def test_next_session_follows_split_and_ignores_legs():
    logged = _logged([("2026-11-11", "push"), ("2026-11-12", "pull"), ("2026-11-13", "shoulders"), ("2026-11-15", "legs")])
    mr = _mr([("2026-11-11", "Bench Press"), ("2026-11-12", "Pull-up"), ("2026-11-13", "DB Pullover"), ("2026-11-13", "Overhead Press"),
              ("2026-11-15", "Back Squat")])
    prog = [{"exercise": "Bench Press", "status": "flat"}, {"exercise": "Pull-up", "status": "flat"}]
    n = st.next_session(SPLIT, logged, mr, EMAP, prog, "2026-11-16")
    assert n["type"] == "push" and n["days_since_type"] == 5
    chest = next(m for m in n["muscles"] if m["key"] == "chest")
    assert chest["days"] == 5 and chest["status"] == "recovered"   # the pullover (chest 0.5) does not count
    tri = next(m for m in n["muscles"] if m["key"] == "triceps")
    assert tri["days"] is None                                     # only secondary work so far
    assert n["hints"] == [{"exercise": "Bench Press", "status": "flat"}]  # Pull-up belongs to pull, not push


def test_plan_actions():
    base = {"type": "push", "last_of_type": None, "days_since_type": 5, "today_types": [], "hints": [{"exercise": "Bench Press", "status": "flat"}],
            "muscles": [{"key": "chest", "label": "Chest", "last": None, "days": 5, "status": "recovered"}]}
    go = rd.plan(base, "go")
    assert go["status"] == "go" and go["action"] == "Train as planned." and "Bench Press" in go["hint"]
    assert rd.plan(base, "easy")["status"] == "light" and rd.plan(base, "easy")["hint"] is None
    assert rd.plan(base, "rest")["status"] == "skip"
    yday = base | {"muscles": [{"key": "chest", "label": "Chest", "last": None, "days": 1, "status": "caution"}]}
    assert rd.plan(yday, "go")["status"] == "light" and "chest" in rd.plan(yday, "go")["action"]
    assert rd.plan(base | {"today_types": ["legs"]}, "go")["status"] == "done"


def test_trend_note_only_when_week_flagged_but_night_fine():
    assert rd.trend_note({"hrv": "ok", "rhr": "ok"}, {"hrv": "in_range", "rhr": "in_range"}) is None
    assert "7-day HRV is still low" in rd.trend_note({"hrv": "caution", "rhr": "ok"}, {"hrv": "watch", "rhr": "in_range"})
    assert rd.trend_note({"hrv": "poor"}, {"hrv": "out_of_range"}) is None  # last night is bad too: no "but" needed


def test_today_done_nothing_logged():
    assert rd.today_done([], [], "go") is None


def test_today_done_rides_and_lift():
    rides = [{"ride_type": "commute", "duration_s": 2520, "distance_m": 13100},
             {"ride_type": "commute", "duration_s": 3000, "distance_m": 13300}]
    out = rd.today_done(rides, [], "go")
    assert out["summary"] == "2 commute rides (1 h 32 min, 26 km) logged today." and "Training's in" in out["text"]
    one = rd.today_done(rides[:1], ["push"], "easy")
    assert one["summary"] == "Commute ride (42 min, 13 km) and push workout logged today."
    assert "already trained" in one["text"]
    assert "despite strain" in rd.today_done([], ["strength"], "rest")["text"]
    mixed = rd.today_done([{"ride_type": "commute", "duration_s": 600, "distance_m": 3000},
                           {"ride_type": "long", "duration_s": 7200, "distance_m": 60000}], [], "go")
    assert mixed["summary"].startswith("2 rides ")
