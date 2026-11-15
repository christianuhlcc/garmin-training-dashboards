"""The Overview's "needs attention" list: what gets flagged, and what deliberately does not."""

from datetime import date

from src.metrics import attention

TODAY = date(2026, 11, 14)


def hdr(key, status, direction=None, avg7=40, baseline=41, sd=4):
    return {"key": key, "label": {"hrv": "HRV (overnight)", "rhr": "Resting HR", "stress": "Stress (avg)"}[key],
            "unit": "ms", "digits": 0, "status": status, "direction": direction, "avg7": avg7, "baseline": baseline, "sd": sd}


def test_health_flags_only_watch_and_out_of_range():
    items, ok = attention.health_items([hdr("hrv", "watch", "low", 35, 40, 5), hdr("rhr", "in_range"), hdr("stress", "no_baseline")])
    assert [i["title"] for i in items] == ["HRV below your usual range"]
    assert items[0]["detail"] == "7-day average 35 ms · usual 40 ± 5 ms"
    assert items[0]["href"] == "health.html#hrv"
    assert ok == ["resting HR"]  # no baseline yet: neither flagged nor counted as fine


def test_cycling_no_rides_and_overdue_long_ride():
    items, ok = attention.cycling_items({"hours7": 0.0, "hours_week_prev4": 1.8, "days_since_long": 56, "long_min": 150})
    assert [i["title"] for i in items] == ["No training rides in the last 7 days", "Long ride overdue"]
    assert ok == []


def test_cycling_small_dip_is_not_a_flag():
    items, ok = attention.cycling_items({"hours7": 1.5, "hours_week_prev4": 1.8, "days_since_long": 10})
    assert items == [] and ok == ["riding volume", "long-ride rhythm"]


def test_strength_split_gap_and_stalled_lifts():
    s = {"last_by_type": {"push": date(2026, 11, 2), "pull": date(2026, 11, 12), "shoulders": None},
         "flat_lifts": ["Bench Press", "Overhead Press"], "regressing_lifts": []}
    items, ok = attention.strength_items(s, TODAY)
    assert items[0]["title"] == "No push session for 12 days"
    assert items[0]["detail"] == "last one on 2 Nov"
    assert items[1]["title"] == "2 lifts flat for 3 sessions"
    assert ok == []


def test_all_clear():
    out = attention.build([hdr("hrv", "in_range")], {"hours7": 2, "hours_week_prev4": 2, "days_since_long": 7},
                          {"last_by_type": {"push": date(2026, 11, 13)}, "flat_lifts": [], "regressing_lifts": []}, TODAY)
    assert out["items"] == []
    assert out["ok"] == ["HRV", "training split", "lift progression", "riding volume", "long-ride rhythm"]


def test_out_of_range_sorts_first():
    out = attention.build([hdr("hrv", "watch", "low"), hdr("rhr", "out_of_range", "high")], {}, {}, TODAY)
    assert [i["tone"] for i in out["items"]] == ["out_of_range", "watch"]


def test_training_reminders_are_untinted_notes():
    items, _ = attention.cycling_items({"hours7": 0.0, "hours_week_prev4": 1.8, "days_since_long": 56})
    assert {i["tone"] for i in items} == {"note"} and {i["kind"] for i in items} == {"rhythm"}


def test_synthesis_connects_body_and_training():
    header = [hdr("hrv", "watch", "low"), hdr("rhr", "watch", "high")]
    out = attention.build(header, {"hours7": 0.0, "hours_week_prev4": 2.0}, {}, TODAY, {"n_metrics": 2, "n_outside": 2})
    assert out["sentence"].startswith("HRV is below and resting HR above your usual range during a lighter training week")
    assert [a["area"] for a in out["areas"]] == ["health", "strength", "cycling"]
    assert out["areas"][0]["line"] == "HRV below and resting HR above your usual range"
    assert out["areas"][2]["status"] == "note"


def test_synthesis_calm_when_body_in_range():
    out = attention.build([hdr("hrv", "in_range")], {"hours7": 2, "hours_week_prev4": 2, "days_since_long": 7},
                          {"last_by_type": {"push": date(2026, 11, 13)}, "flat_lifts": [], "regressing_lifts": []}, TODAY)
    assert out["sentence"] == "Your body is in its usual range and training is on rhythm."


def test_effort_flag_is_one_calm_line_only_when_set():
    items, ok = attention.cycling_items({"effort_flag": "over"})
    assert [i["title"] for i in items] == ["Last week's riding load was well above your recent range"]
    items, ok = attention.cycling_items({"effort_flag": None})
    assert items == [] and ok == ["weekly effort"]


def test_under_range_line_is_dropped_when_volume_line_already_fired():
    items, _ = attention.cycling_items({"hours7": 0.0, "hours_week_prev4": 1.8, "effort_flag": "under"})
    assert [i["title"] for i in items] == ["No training rides in the last 7 days"]
    items, _ = attention.cycling_items({"hours7": 1.8, "hours_week_prev4": 1.8, "effort_flag": "under"})
    assert [i["title"] for i in items] == ["Riding load below your recent range for two weeks"]
