#!/usr/bin/env python3
"""Import the hand-written strength log from Apple Notes ("Workout 2026", ...) into garmin.db.

The note is the source of truth for exercises / sets / reps / weights; Garmin's auto-detected sets are
not. Each note session is linked to the Garmin strength_training activity of the same day (or ±1 day)
for HR, calories and duration.

    .venv/bin/python notes_import.py                   # all notes whose title contains "workout 20"
    .venv/bin/python notes_import.py --file notes/workout_2026.txt --title "Workout 2026"

Reads Notes via osascript (macOS asks once for Automation permission). A plain-text snapshot of each
note is written to notes/<title>.txt. See AGENTS.md ("Manual strength log") for the format rules.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "garmin.db"
NOTES_DIR = ROOT / "notes"
NOTE_QUERY = "workout 20"  # case-insensitive match on the note title: "Workout 2026", "Workout 2027", ...

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_workouts (
    workout_id INTEGER PRIMARY KEY, source TEXT, workout_date TEXT, date_raw TEXT, date_flag TEXT,
    title TEXT, comments TEXT, garmin_activity_id INTEGER, garmin_match TEXT,
    n_exercises INTEGER, n_sets INTEGER, raw_block TEXT, imported_at TEXT);
CREATE INDEX IF NOT EXISTS idx_manual_workouts_date ON manual_workouts (workout_date);
CREATE TABLE IF NOT EXISTS manual_sets (
    workout_id INTEGER, workout_date TEXT, exercise_no INTEGER, set_no INTEGER,
    exercise TEXT, exercise_raw TEXT, reps INTEGER, weight_kg REAL, implements INTEGER, load_kg REAL,
    to_failure INTEGER, superset INTEGER, skipped INTEGER, parse_flag TEXT, note TEXT, line_raw TEXT,
    PRIMARY KEY (workout_id, exercise_no, set_no));
CREATE INDEX IF NOT EXISTS idx_manual_sets_ex ON manual_sets (exercise, workout_date);
"""

VIEWS = """
DROP VIEW IF EXISTS v_strength_sessions;
CREATE VIEW v_strength_sessions AS
SELECT w.workout_id, w.workout_date, w.title, w.comments, w.n_exercises, w.n_sets,
       (SELECT SUM(reps) FROM manual_sets s WHERE s.workout_id = w.workout_id) AS total_reps,
       (SELECT ROUND(SUM(reps * load_kg)) FROM manual_sets s WHERE s.workout_id = w.workout_id) AS volume_kg,
       (SELECT GROUP_CONCAT(exercise, ', ') FROM (SELECT DISTINCT exercise FROM manual_sets s
            WHERE s.workout_id = w.workout_id AND skipped = 0 ORDER BY exercise_no)) AS exercises,
       w.garmin_activity_id, w.garmin_match, a.start_time_local AS garmin_start_local,
       ROUND(a.duration_s / 60.0, 1) AS garmin_duration_min, a.avg_hr AS garmin_avg_hr,
       a.max_hr AS garmin_max_hr, a.calories AS garmin_calories,
       a.total_sets AS garmin_sets, a.total_reps AS garmin_reps, a.body_battery_delta AS garmin_bb_delta
FROM manual_workouts w LEFT JOIN activities a ON a.activity_id = w.garmin_activity_id;

DROP VIEW IF EXISTS v_exercise_progress;
CREATE VIEW v_exercise_progress AS
SELECT exercise, workout_date, COUNT(*) AS sets, SUM(reps) AS total_reps,
       GROUP_CONCAT(reps, '/') AS reps_per_set, MAX(weight_kg) AS top_weight_kg, MAX(implements) AS implements,
       ROUND(SUM(reps * load_kg)) AS volume_kg,
       ROUND(MAX(weight_kg * (1 + reps / 30.0)), 1) AS est_1rm_kg,  -- Epley, per implement
       MAX(to_failure) AS hit_failure
FROM manual_sets WHERE skipped = 0 AND reps IS NOT NULL
GROUP BY exercise, workout_date;
"""


# --------------------------------------------------------------------------- reading notes

def read_apple_notes(query: str = NOTE_QUERY) -> list[tuple[str, str]]:
    """Return [(title, plaintext)] for every Apple Note whose title contains `query` (case-insensitive)."""
    script = f'''
    set out to ""
    tell application "Notes"
        repeat with n in (every note whose name contains "{query}")
            set out to out & "<<<NOTE>>>" & (name of n) & linefeed & (plaintext of n) & linefeed
        end repeat
    end tell
    return out
    '''
    res = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"osascript failed: {res.stderr.strip()}")
    notes = []
    for chunk in res.stdout.split("<<<NOTE>>>")[1:]:
        title, _, body = chunk.partition("\n")
        notes.append((title.strip(), body))
    return notes


def clean_text(text: str) -> str:
    """Normalize no-break spaces, zero-width chars and line separators Notes likes to insert."""
    for ch, rep in (("\u00a0", " "), ("\u200b", ""), ("\u2028", "\n"), ("\r", "")):
        text = text.replace(ch, rep)
    return text


# --------------------------------------------------------------------------- parsing

DATE_RE = re.compile(r"^(\d{1,2})\s*\.+\s*(\d{1,2})\.?(?=\s|$|[^\d.])\s*(.*)$")
NUM = r"\d+(?:\.\d+)?"
TOKEN_RE = re.compile(rf"""
    (?P<wlist>{NUM}(?:/{NUM})+)\s*kg            # per-set weights: 20/30/30/30 kg
  | (?P<impl>\d+)\s*x\s*(?P<implw>{NUM})\s*kg   # implements x weight: 2x6kg = pair of 6 kg dumbbells
  | (?P<w>{NUM})\s*kg                          # weight: 32.5kg
  | (?P<sets>\d+)\s*x\s*(?P<reps>{NUM})         # sets x reps: 3x12   (or early-log reps x kg: 5x60)
  | (?P<rlist>\d+(?:/\d+)+)                    # reps per set: 10/10/8/5
  | (?P<num>{NUM})                             # bare number: reps of one set, or a weight after NxM
  | (?P<dash>-)                                # exercise skipped
""", re.X)


def canonical_exercise(name: str, implements: int | None, weight: float | None) -> str:
    n = " " + re.sub(r"[^a-zäöüß]+", " ", name.lower()) + " "
    has = lambda *words: all(w in n for w in words)  # noqa: E731
    if has("pullover"):
        return "DB Pullover"
    if has("pull") or has("klimmz"):
        return "Pull-up"
    if has(" rdl "):
        return "Romanian Deadlift"
    if has("deadlift"):
        return "Deadlift"
    if has("squat"):
        return "Back Squat"
    if has("close") and ("grip" in n):
        return "Close-Grip Bench Press"
    if has("incline") or has("schrägbank"):
        return "Incline Bench Press"
    if has("flat press") or (has(" db ") and has("bench")):
        return "DB Bench Press"
    if has("bench"):
        return "Bench Press"
    if has("fly") and has("rear"):
        return "DB Rear Delt Fly"
    if has("fly"):
        return "DB Fly"
    if has("shoulder press"):
        return "DB Shoulder Press"
    if has(" ohp ") or has("overhead press"):
        return "Overhead Press"
    if has("overhead", "tricep"):
        return "Overhead Triceps Extension"
    if has("lat") and (has("raise") or has("rise")):
        return "DB Lateral Raise"
    if has("shrug"):
        return "Barbell Shrug" if has(" bb ") or has("barbell") else "DB Shrug"
    if has("row"):
        return "DB Row" if has(" db ") else "Barbell Row"
    if has("curl"):
        if has(" oder "):
            return "Curl"
        if has("hammer"):
            return "DB Hammer Curl"
        if has("barbell") or has(" bb "):
            return "Barbell Curl"
        if has(" db ") or implements == 2:
            return "DB Curl"
        if weight is None:
            return "Curl"
        return "Barbell Curl" if weight >= 15 else "DB Curl"
    if has("crunch"):
        return "Crunch"
    return name.strip().title()


def parse_exercise(line: str) -> dict:
    """Parse one exercise line into individual sets. Returns {name, sets: [...], notes, flag, skipped}."""
    s = line.replace("×", "x")
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)  # decimal comma
    first_num = re.search(r"(?:^|\s)[\d-]", s)
    head, tail = (s[: first_num.start()], s[first_num.start():]) if first_num else (s, "")
    notes = [m.strip() for m in re.findall(r"\(([^)]*)\)", tail)]
    tail = re.sub(r"\([^)]*\)", " ", tail)
    m = re.search(r"\s-\s*([A-Za-zÄÖÜäöüß].*)$", tail)  # "... 5kg - sehr hart noch"
    if m:
        notes.append(m.group(1).strip())
        tail = tail[: m.start()]
    tail = tail.replace(",", " ")
    name = head.strip()
    superset = int("superset" in name.lower() or any("superset" in x.lower() for x in notes))
    failure = any("failure" in x.lower() for x in notes)
    one_arm = "einarm" in name.lower()
    has_kg = "kg" in tail.lower()

    sets: list[dict] = []
    pending: list[int] = []
    flags: set[str] = set()
    prev_kind = None
    skipped = False

    def emit(weight=None, implements=None, weights=None):
        nonlocal pending
        for i, reps in enumerate(pending):
            w = weights[min(i, len(weights) - 1)] if weights else weight
            sets.append({"reps": reps, "weight": w, "implements": implements if w is not None else None})
        if weights and len(weights) != len(pending):
            flags.add("weight_count_mismatch")
        pending = []

    pos = 0
    tail = tail.strip()
    while pos < len(tail):
        if tail[pos].isspace():
            pos += 1
            continue
        t = TOKEN_RE.match(tail, pos)
        if not t:
            flags.add("unparsed_text")
            nxt = tail.find(" ", pos)
            pos = len(tail) if nxt == -1 else nxt
            continue
        pos = t.end()
        kind = t.lastgroup if t.lastgroup not in ("implw", "reps") else ("impl" if t.group("impl") else "sets")
        if t.group("wlist"):
            emit(weights=[float(x) for x in t.group("wlist").split("/")], implements=1)
        elif t.group("impl"):
            emit(float(t.group("implw")), int(t.group("impl")))
        elif t.group("w"):
            if not pending and sets and sets[-1]["weight"] is None:
                flags.add("weight_without_sets")
            emit(float(t.group("w")), 1)
        elif t.group("sets"):
            n, reps = int(t.group("sets")), float(t.group("reps"))
            if not has_kg and (reps >= 20 or not reps.is_integer()):
                # Early-log notation "Deadlift 5x60 5x70": reps x kg, one set each.
                sets.append({"reps": n, "weight": reps, "implements": 1})
                flags.add("reps_x_kg")
            else:
                pending += [int(reps)] * n
        elif t.group("rlist"):
            pending += [int(x) for x in t.group("rlist").split("/")]
        elif t.group("num"):
            v = float(t.group("num"))
            if prev_kind == "sets" and pending and not has_kg:
                emit(v, 1)  # "OHP 3x5 25": trailing bare weight
                flags.add("weight_without_unit")
            elif skipped:
                pass  # "BB close grip - 0"
            else:
                pending.append(int(v))
        elif t.group("dash"):
            skipped = True
        prev_kind = kind
    emit()
    if skipped and not sets:
        return {"name": name, "sets": [], "notes": notes, "flag": "skipped", "skipped": True,
                "superset": superset}
    if one_arm:
        for st in sets:
            if st["weight"] is not None:
                st["implements"] = 1
    if failure and sets:
        sets[-1]["failure"] = True
    if not sets:
        flags.add("no_sets")
    return {"name": name, "sets": sets, "notes": notes, "flag": ",".join(sorted(flags)) or None,
            "skipped": False, "superset": superset}


def is_exercise_line(line: str) -> bool:
    if not line[:1].isalpha():
        return False
    return bool(re.search(r"\d", line)) or bool(re.search(r"\s-\s*0?\s*$", line + " "))


def parse_note(text: str, year: int) -> list[dict]:
    """Split a note into sessions. Dates are D.M without a year; the log runs newest → oldest."""
    sessions, cur = [], None
    for raw in clean_text(text).split("\n"):
        line = raw.strip()
        if not line:
            if cur is not None:
                cur["raw"].append("")
            continue
        m = DATE_RE.match(line)
        if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
            cur = {"day": int(m.group(1)), "month": int(m.group(2)), "date_raw": line[: m.start(3)].strip(),
                   "title": re.sub(r"^[-–\s]+", "", m.group(3)).strip() or None,
                   "exercises": [], "comments": [], "raw": [line]}
            sessions.append(cur)
            continue
        if cur is None:
            continue  # note title / preamble
        cur["raw"].append(line)
        if is_exercise_line(line):
            ex = parse_exercise(line)
            ex["line"] = line
            cur["exercises"].append(ex)
        else:
            cur["comments"].append(line)

    # Assign years and fix obvious month typos using the newest→oldest ordering.
    prev = None
    for i, s in enumerate(sessions):
        y = year
        flag = None
        d = _safe_date(y, s["month"], s["day"])
        if prev and d and d > prev + timedelta(days=60):  # wrapped past New Year going backwards
            y -= 1
            d = _safe_date(y, s["month"], s["day"])
        nxt = sessions[i + 1] if i + 1 < len(sessions) else None
        nxt_d = _safe_date(y, nxt["month"], nxt["day"]) if nxt else None
        if prev and d and nxt_d and d < nxt_d <= prev:
            # Out of order (e.g. "27.4" between 28.6 and 26.6): try the neighbour's month.
            cand = _safe_date(y, prev.month, s["day"])
            if cand and nxt_d <= cand <= prev:
                flag = f"month corrected from {s['month']} to {prev.month} (out of order)"
                d = cand
        if d is None:
            flag = "invalid date"
        s["date"], s["date_flag"] = d, flag
        if d:
            prev = d
    return sessions


def _safe_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


# --------------------------------------------------------------------------- storing

def store(db: sqlite3.Connection, source: str, sessions: list[dict]) -> tuple[int, int]:
    db.executescript(SCHEMA)
    db.executescript(VIEWS)
    old = [r[0] for r in db.execute("SELECT workout_id FROM manual_workouts WHERE source = ?", (source,))]
    db.executemany("DELETE FROM manual_sets WHERE workout_id = ?", [(i,) for i in old])
    db.execute("DELETE FROM manual_workouts WHERE source = ?", (source,))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    seq: dict[str, int] = {}
    n_sets_total = 0
    for s in sessions:
        if not s["date"]:
            continue
        ds = s["date"].isoformat()
        seq[ds] = seq.get(ds, 0) + 1
        wid = int(ds.replace("-", "")) * 10 + seq[ds]
        rows = []
        for ex_no, ex in enumerate(s["exercises"], 1):
            first_w = next((st["weight"] for st in ex["sets"] if st["weight"] is not None), None)
            first_i = next((st["implements"] for st in ex["sets"] if st["implements"]), None)
            canon = canonical_exercise(ex["name"], first_i, first_w)
            note = "; ".join(ex["notes"]) or None
            if ex["skipped"]:
                rows.append((wid, ds, ex_no, 0, canon, ex["name"], None, None, None, None, 0, ex["superset"], 1,
                             "skipped", note, ex["line"]))
                continue
            for set_no, st in enumerate(ex["sets"], 1):
                load = st["weight"] * (st["implements"] or 1) if st["weight"] is not None else None
                rows.append((wid, ds, ex_no, set_no, canon, ex["name"], st["reps"], st["weight"], st["implements"],
                             load, int(bool(st.get("failure"))), ex["superset"], 0, ex["flag"], note, ex["line"]))
        db.executemany("INSERT INTO manual_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        n_sets = sum(1 for r in rows if not r[12])
        n_sets_total += n_sets
        db.execute(
            "INSERT INTO manual_workouts (workout_id, source, workout_date, date_raw, date_flag, title, comments, "
            "n_exercises, n_sets, raw_block, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, source, ds, s["date_raw"], s["date_flag"], s["title"], "\n".join(s["comments"]) or None,
             len(s["exercises"]), n_sets, "\n".join(s["raw"]).strip(), now))
    link_garmin(db)
    db.commit()
    return len([s for s in sessions if s["date"]]), n_sets_total


def link_garmin(db: sqlite3.Connection):
    """Link each note session to a Garmin strength activity: same day first, then ±1 day (unclaimed)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'activities'").fetchone():
        return
    db.execute("UPDATE manual_workouts SET garmin_activity_id = NULL, garmin_match = NULL")
    # Longest first, so an aborted 2-minute start on the same day never wins over the real session.
    acts = db.execute("SELECT activity_id, calendar_date FROM activities WHERE type_key = 'strength_training' "
                      "ORDER BY duration_s DESC").fetchall()
    by_day: dict[str, list[int]] = {}
    for aid, d in acts:
        by_day.setdefault(d, []).append(aid)
    claimed: set[int] = set()
    workouts = db.execute("SELECT workout_id, workout_date FROM manual_workouts ORDER BY workout_id").fetchall()
    for offset, label in ((0, "same_day"), (1, "+1 day"), (-1, "-1 day")):
        for wid, wd in workouts:
            if db.execute("SELECT garmin_activity_id FROM manual_workouts WHERE workout_id = ?", (wid,)).fetchone()[0]:
                continue
            day = (date.fromisoformat(wd) + timedelta(days=offset)).isoformat()
            # A same-date Garmin session that another note entry already claims is never re-used.
            free = [a for a in by_day.get(day, []) if a not in claimed]
            if offset and db.execute("SELECT 1 FROM manual_workouts WHERE workout_date = ?", (day,)).fetchone():
                continue  # that day has its own note entry
            if free:
                claimed.add(free[0])
                db.execute("UPDATE manual_workouts SET garmin_activity_id = ?, garmin_match = ? WHERE workout_id = ?",
                           (free[0], label, wid))


def sync(db: sqlite3.Connection, log=print, query: str = NOTE_QUERY) -> None:
    notes = read_apple_notes(query)
    if not notes:
        log(f"notes: no Apple Note title contains '{query}'")
        return
    NOTES_DIR.mkdir(exist_ok=True)
    for title, text in notes:
        (NOTES_DIR / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', title.lower()).strip('_')}.txt").write_text(text)
        year_m = re.search(r"20\d\d", title)
        sessions = parse_note(text, int(year_m.group()) if year_m else date.today().year)
        n_workouts, n_sets = store(db, title, sessions)
        flagged = db.execute("SELECT COUNT(DISTINCT workout_id || '-' || exercise_no) FROM manual_sets s "
                             "JOIN manual_workouts w USING (workout_id) WHERE w.source = ? AND parse_flag IS NOT NULL "
                             "AND parse_flag != 'skipped'", (title,)).fetchone()[0]
        linked = db.execute("SELECT COUNT(*) FROM manual_workouts WHERE source = ? AND garmin_activity_id IS NOT NULL",
                            (title,)).fetchone()[0]
        log(f"notes: '{title}': {n_workouts} sessions, {n_sets} sets, {linked} linked to Garmin, "
            f"{flagged} exercise lines with parse flags")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="parse a plain-text export instead of reading Apple Notes")
    ap.add_argument("--title", help="source title to store with --file (default: file stem)")
    ap.add_argument("--query", default=NOTE_QUERY, help=f"Apple Notes title filter (default '{NOTE_QUERY}')")
    args = ap.parse_args()
    db = sqlite3.connect(DB_PATH)
    if args.file:
        title = args.title or args.file.stem
        year_m = re.search(r"20\d\d", title)
        sessions = parse_note(args.file.read_text(), int(year_m.group()) if year_m else date.today().year)
        n_workouts, n_sets = store(db, title, sessions)
        print(f"notes: '{title}' from {args.file}: {n_workouts} sessions, {n_sets} sets")
    else:
        sync(db, query=args.query)
    db.close()


if __name__ == "__main__":
    sys.exit(main())
