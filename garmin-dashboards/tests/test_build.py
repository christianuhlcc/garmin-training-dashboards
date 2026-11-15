"""Reconciliation tests on the built payloads (skipped until `make metrics` has run)."""

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

BUILD = Path(__file__).resolve().parents[1] / "build"
pytestmark = pytest.mark.skipif(not (BUILD / "metrics.duckdb").exists(), reason="run make metrics first")


def _payload(name):
    return json.loads((BUILD / "data" / f"{name}.json").read_text())


def test_weekly_hours_equal_activity_durations():
    vol = _payload("cycling")["volume"]
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    df = con.execute("SELECT week, SUM(duration_s) / 3600 AS h FROM activities WHERE sport = 'cycling' GROUP BY 1").df()
    expected = {pd.Timestamp(w).date().isoformat(): h for w, h in zip(df["week"], df["h"])}
    for i, w in enumerate(vol["weeks"]):
        shown = sum((vol["series"]["hours"][t][i] or 0) for t in vol["types"])
        assert shown == pytest.approx(expected.get(w, 0.0), abs=0.05), w


def test_muscle_map_matches_weekly_sets_source():
    s = _payload("strength")
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    today = pd.Timestamp(s["meta"]["today"])
    mr = con.execute("SELECT date, muscle, w FROM muscle_rows").df()
    mr["date"] = pd.to_datetime(mr["date"])
    last7 = mr[(mr["date"] > today - pd.Timedelta(days=7)) & (mr["date"] <= today)].groupby("muscle")["w"].sum()
    for m, v in s["muscle_map"]["volume"]["7d"].items():
        assert v == pytest.approx(last7.get(m, 0.0), abs=0.01), m
    # heatmap: the current ISO week column equals the muscle rows of that week
    ws = s["weekly_sets"]
    cw = ws["current_week"]
    i = ws["weeks"].index(cw)
    week = mr[mr["date"] >= pd.Timestamp(cw)].groupby("muscle")["w"].sum()
    for m in ws["regions"]:
        assert (ws["values"][m][i] or 0) == pytest.approx(week.get(m, 0.0), abs=0.01), m


def test_ebikes_never_matched_or_ranked():
    c = _payload("cycling")
    ids = {r["activity_id"] for g in c["matched"]["groups"] for r in g["rides"]}
    ids |= {e["activity_id"] for tops in c["best_efforts"]["top3"].values() for e in tops}
    ids |= {e["activity_id"] for e in (c["best_efforts"]["records"] or {}).values() if e}
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    types = dict(con.execute("SELECT activity_id, ride_type FROM activities WHERE sport = 'cycling'").fetchall())
    assert ids and all(types[i] not in ("ebike", "indoor") for i in ids)


def test_map_payload_has_only_outdoor_rides_with_routes():
    m = _payload("map")
    con = duckdb.connect(str(BUILD / "metrics.duckdb"), read_only=True)
    n_out = con.execute("SELECT COUNT(*) FROM activities WHERE sport = 'cycling' AND ride_type <> 'indoor'").fetchone()[0]
    assert 0 < len(m["rides"]) <= n_out
    assert {r["t"] for r in m["rides"]} <= {"commute", "long", "other", "ebike"}
    assert all(r["p"] and len(r["p"][0]) >= 4 for r in m["rides"])  # start point + at least one delta
