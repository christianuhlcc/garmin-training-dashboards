#!/usr/bin/env python3
"""Import a Fitbod "Export Workout Data" CSV into the manual strength tables of garmin.db.

    python fitbod_import.py ~/Downloads/WorkoutExport.csv [--dry-run]

Same tables as notes_import.py (manual_workouts / manual_sets), stored with source = 'fitbod', so the dashboards treat
these sessions like the Apple Note ones: manual sets win over Garmin's auto-detected sets for the linked activity.
Re-running replaces the 'fitbod' rows only (idempotent); the Apple Note import never touches them.

Fitbod writes one row per set (newest session first). Mapping:
- exercise names -> the project's canonical names (FITBOD_NAMES below; unknown names keep Fitbod's spelling and show
  up in `make review-exercises`);
- warm-up sets, timed sets without reps, and conditioning moves (burpees) are stored but flagged `skipped`, so they are
  kept for reference and never counted;
- weight is per implement: multiplier 2 means a dumbbell pair (implements = 2); weight 0 means bodyweight (NULL).
Session = one local (Europe/Berlin) calendar day of the export's timestamps.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import notes_import as ni

SOURCE = "fitbod"
TZ = "Europe/Berlin"

FITBOD_NAMES = {
    "Barbell Bench Press": "Bench Press", "Close-Grip Bench Press": "Close-Grip Bench Press",
    "Barbell Incline Bench Press": "Incline Bench Press",
    "Barbell Shoulder Press": "Overhead Press", "Seated Barbell Shoulder Press": "Overhead Press",
    "Bent Over Barbell Row": "Barbell Row", "Gorilla Rows": "DB Row",
    "Barbell Curl": "Barbell Curl", "Reverse Barbell Curl": "Reverse Barbell Curl", "Concentration Curl": "DB Curl",
    "Dumbbell Bicep Curl": "DB Curl", "Hammer Curls": "DB Hammer Curl",
    "Back Squat": "Back Squat", "Deadlift": "Deadlift", "Romanian Deadlift": "Romanian Deadlift",
    "Barbell Hip Thrust": "Hip Thrust", "Good Morning": "Good Morning", "Single Leg Kickback": "Glute Kickback",
    "Barbell Shrug": "Barbell Shrug", "Upright Row": "Upright Row",
    "Pull Up": "Pull-up", "Wide Grip Pull Up": "Pull-up", "Neutral Grip Pull Up": "Pull-up",
    "Chin Up": "Chin-up", "Australian Chin Up": "Inverted Row",
    "Push Up": "Push-up", "Pike Push Up": "Pike Push-up", "Bench Dip": "Bench Dip",
    "Crunches": "Crunch", "Bicycle Crunch": "Bicycle Crunch", "Leg Raise": "Leg Raise",
}
CONDITIONING = {"Burpee"}                 # not a strength set: kept, not counted
TIMED = {"Single Leg Glute Bridge"}       # reps are 0, only a duration: kept, not counted


def read_export(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Weight(kg)": "Weight", "Duration(s)": "Duration"})
    need = {"Date", "Exercise", "Reps", "Weight", "isWarmup", "multiplier"}
    if need - set(df.columns):
        raise SystemExit(f"{path.name}: missing columns {sorted(need - set(df.columns))}; is this a Fitbod export?")
    df["Exercise"] = df["Exercise"].str.strip()
    df["ts"] = pd.to_datetime(df["Date"].str.strip(), utc=True)
    df["day"] = df["ts"].dt.tz_convert(TZ).dt.date
    df["warm"] = df["isWarmup"].astype(str).str.lower().eq("true")
    for c in ("Reps", "Weight", "multiplier", "Duration"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build(df: pd.DataFrame) -> list[dict]:
    """Sessions (oldest first) with exercises in the order they appear in the file, and per-set rows."""
    sessions = []
    for day, g in sorted(df.groupby("day"), key=lambda kv: kv[0]):
        exercises: dict[str, list] = {}
        for r in g.itertuples():
            exercises.setdefault(r.Exercise, []).append(r)
        sessions.append({"date": day, "ts": g["ts"].min(), "exercises": exercises})
    return sessions


def rows_for(session: dict, wid: int) -> tuple[list[tuple], list[str]]:
    rows, lines = [], []
    for ex_no, (name, sets) in enumerate(session["exercises"].items(), 1):
        canon = FITBOD_NAMES.get(name, name)
        parts = []
        for set_no, r in enumerate(sets, 1):
            reps = None if pd.isna(r.Reps) or r.Reps <= 0 else int(r.Reps)
            pair = r.multiplier == 2
            weight = None if pd.isna(r.Weight) or r.Weight <= 0 else float(r.Weight)
            implements = (2 if pair else 1) if weight is not None else None
            load = weight * implements if weight is not None else None
            flag = skipped = None
            if name in CONDITIONING:
                skipped, flag = 1, "conditioning, not counted"
            elif name in TIMED or reps is None:
                skipped, flag = 1, "timed set without reps, not counted"
            elif r.warm:
                skipped, flag = 1, "warm-up, not counted"
            rows.append((wid, session["date"].isoformat(), ex_no, set_no, canon, name, reps, weight, implements, load, 0, 0,
                         1 if skipped else 0, flag, None, f"{name} {reps if reps is not None else '-'}"
                         f"{'x' + format(weight, 'g') + 'kg' if weight else ''}{' (warm-up)' if r.warm else ''}"))
            parts.append(f"{reps if reps is not None else '-'}{'x' + format(weight, 'g') if weight else ''}")
        lines.append(f"{name}: " + " / ".join(parts))
    return rows, lines


def store(db: sqlite3.Connection, sessions: list[dict]) -> tuple[int, int, int]:
    db.executescript(ni.SCHEMA)
    db.executescript(ni.VIEWS)
    old = [r[0] for r in db.execute("SELECT workout_id FROM manual_workouts WHERE source = ?", (SOURCE,))]
    db.executemany("DELETE FROM manual_sets WHERE workout_id = ?", [(i,) for i in old])
    db.execute("DELETE FROM manual_workouts WHERE source = ?", (SOURCE,))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    n_sets = n_skipped = 0
    for s in sessions:
        ds = s["date"].isoformat()
        seq = 1
        while db.execute("SELECT 1 FROM manual_workouts WHERE workout_id = ?", (int(ds.replace("-", "")) * 10 + seq,)).fetchone():
            seq += 1
        wid = int(ds.replace("-", "")) * 10 + seq
        rows, lines = rows_for(s, wid)
        db.executemany("INSERT INTO manual_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        counted = sum(1 for r in rows if not r[12])
        n_sets += counted
        n_skipped += len(rows) - counted
        db.execute("INSERT INTO manual_workouts (workout_id, source, workout_date, date_raw, date_flag, title, comments, "
                   "n_exercises, n_sets, raw_block, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (wid, SOURCE, ds, str(s["ts"]), None, "Fitbod", None, len(s["exercises"]), counted, "\n".join(lines), now))
    ni.link_garmin(db)
    db.commit()
    return len(sessions), n_sets, n_skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="parse and summarise, write nothing")
    args = ap.parse_args()
    sessions = build(read_export(args.csv))
    unknown = sorted({n for s in sessions for n in s["exercises"] if n not in FITBOD_NAMES | dict.fromkeys(CONDITIONING | TIMED, "")})
    n_rows = sum(len(r) for s in sessions for r in s["exercises"].values())
    print(f"fitbod: {len(sessions)} sessions ({sessions[0]['date']} to {sessions[-1]['date']}), {n_rows} set rows"
          + (f"; unmapped names (kept as spelled): {unknown}" if unknown else ""))
    if args.dry_run:
        return
    db = sqlite3.connect(ni.DB_PATH)
    n, n_sets, n_skipped = store(db, sessions)
    linked = db.execute("SELECT COUNT(*) FROM manual_workouts WHERE source = ? AND garmin_activity_id IS NOT NULL", (SOURCE,)).fetchone()[0]
    print(f"fitbod: stored {n} sessions, {n_sets} counted sets ({n_skipped} warm-up/timed/conditioning kept but not counted), "
          f"{linked} linked to a Garmin strength activity")


if __name__ == "__main__":
    sys.exit(main())
