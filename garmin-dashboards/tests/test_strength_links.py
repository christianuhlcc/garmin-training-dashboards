"""One note entry, several Garmin activities (watch stopped and restarted): joined, not 'unlogged'."""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: notes_import.py
import notes_import  # noqa: E402

from src.metrics import strength  # noqa: E402


def _db(activities, workouts):
    db = sqlite3.connect(":memory:")
    db.executescript(notes_import.SCHEMA)
    db.execute("CREATE TABLE activities (activity_id INTEGER PRIMARY KEY, calendar_date TEXT, type_key TEXT, "
               "duration_s REAL)")
    db.executemany("INSERT INTO activities VALUES (?, ?, 'strength_training', ?)", activities)
    db.executemany("INSERT INTO manual_workouts (workout_id, workout_date) VALUES (?, ?)", workouts)
    notes_import.link_garmin(db)
    return db


def _links(db):
    return sorted(db.execute("SELECT activity_id, workout_id, role FROM manual_workout_activities"))


def test_second_garmin_activity_joins_the_only_note_entry():
    db = _db([(1, "2026-11-26", 1740), (2, "2026-11-26", 1140)], [(202611261, "2026-11-26")])
    assert db.execute("SELECT garmin_activity_id FROM manual_workouts").fetchone()[0] == 1  # longest is primary
    assert _links(db) == [(1, 202611261, "primary"), (2, 202611261, "joined")]


def test_two_note_entries_keep_one_activity_each():
    db = _db([(1, "2026-11-26", 1740), (2, "2026-11-26", 1140), (3, "2026-11-26", 600)],
             [(202611261, "2026-11-26"), (202611262, "2026-11-26")])
    assert _links(db) == [(1, 202611261, "primary"), (2, 202611262, "primary")]  # 3 stays unassigned


def test_no_join_across_days():
    db = _db([(1, "2026-11-26", 1740), (2, "2026-11-27", 1140)], [(202611261, "2026-11-26"),
                                                                   (202611271, "2026-11-27")])
    assert _links(db) == [(1, 202611261, "primary"), (2, 202611271, "primary")]


def test_garmin_per_session_combines_activities():
    acts = pd.DataFrame({"activity_id": [1, 2, 3], "duration_s": [1740.0, 1140.0, 3000.0],
                         "avg_hr": [95.0, 85.0, 100.0], "trimp": [20.0, 10.0, 40.0]})
    links = pd.DataFrame({"workout_id": [11, 11, 12], "activity_id": [1, 2, 3], "role": ["primary", "joined", "primary"]})
    g = strength.garmin_per_session(acts, links)
    assert g.loc[11, "duration_s"] == 2880
    assert g.loc[11, "avg_hr"] == pytest.approx((95 * 1740 + 85 * 1140) / 2880)
    assert g.loc[11, "trimp"] == 30 and g.loc[11, "n_activities"] == 2
    assert g.loc[12, "duration_s"] == 3000 and g.loc[12, "n_activities"] == 1
