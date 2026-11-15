#!/usr/bin/env python3
"""Fetch Garmin Connect Personal API data into a local SQLite database.

Usage (prefer ./sync.sh, which sets up the venv):
    .venv/bin/python garmin_sync.py              # incremental: re-fetch the last few days + new activities
    .venv/bin/python garmin_sync.py --full       # everything since the first day with data
    .venv/bin/python garmin_sync.py --start 2026-01-01 --end 2026-02-01
    .venv/bin/python garmin_sync.py --days 14    # re-fetch the last 14 days

Auth: GARMIN_API_KEY from the environment or ./.env. The 24h bearer token is
cached in ./.garmin_token (mode 600). Only stdlib is required; `fitdecode` is
needed to parse FIT files into activity_records / activity_laps / activity_sets.
Also re-imports the Apple Notes strength log (notes_import.py) unless --skip-notes.
See AGENTS.md for the database layout.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import fitdecode
except ImportError:  # FIT parsing is optional
    fitdecode = None

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "garmin.db"
FIT_DIR = ROOT / "data" / "fit"
TOKEN_FILE = ROOT / ".garmin_token"
BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
JSON_ACCEPT = "application/json"
ZIP_ACCEPT = "application/zip, application/x-zip-compressed, application/octet-stream"
WORKERS = 4
DEFAULT_OVERLAP_DAYS = 3
BODY_BATTERY_CHUNKS = 96  # min/max pair per 15 minutes


# --------------------------------------------------------------------------- HTTP

class ApiError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status


def load_api_key() -> str:
    key = os.environ.get("GARMIN_API_KEY")
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("GARMIN_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        sys.exit("GARMIN_API_KEY not set (environment or .env)")
    return key


class Api:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._token: str | None = None
        self._lock = threading.Lock()
        self.calls = 0
        self.throttled = 0  # 429/5xx retries

    def _fetch_token(self) -> str:
        req = urllib.request.Request(
            BASE + TOKEN_PATH, method="POST", headers={"Authorization": f"ApiKey {self.api_key}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = resp.read().decode().strip()
        TOKEN_FILE.write_text(token)
        TOKEN_FILE.chmod(0o600)
        return token

    def token(self, force: bool = False) -> str:
        with self._lock:
            if force:
                self._token = None
            if self._token is None:
                # Tokens live 24h; reuse a cached one that is younger than 23h.
                if not force and TOKEN_FILE.exists() and time.time() - TOKEN_FILE.stat().st_mtime < 23 * 3600:
                    self._token = TOKEN_FILE.read_text().strip()
                else:
                    self._token = self._fetch_token()
            return self._token

    def request(self, path: str, params: dict | None = None, accept: str = JSON_ACCEPT) -> bytes:
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        refreshed = False
        for attempt in range(7):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token()}", "Accept": accept})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    self.calls += 1
                    return resp.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code == 401 and not refreshed:
                    self.token(force=True)
                    refreshed = True
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < 6:
                    self.throttled += 1
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                    time.sleep(wait + random.random())
                    continue
                raise ApiError(e.code, url, body) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < 6:
                    time.sleep(2 ** attempt + random.random())
                    continue
                raise ApiError(0, url, str(e)) from None
        raise ApiError(0, url, "retries exhausted")

    def get(self, path: str, **params):
        body = self.request(path, params or None)
        return json.loads(body) if body else None


# --------------------------------------------------------------------------- helpers

def ms_to_iso(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def norm_ts(s) -> str | None:
    """'2026-11-12T23:00:00.0' -> '2026-11-12 23:00:00'."""
    if not s:
        return None
    return s.replace("T", " ")[:19]


def parse_ts(s: str) -> datetime:
    return datetime.strptime(norm_ts(s), "%Y-%m-%d %H:%M:%S")


def day_offset(payload: dict, gmt_key="startTimestampGMT", local_key="startTimestampLocal") -> timedelta:
    """Local-minus-UTC offset Garmin used for that day (follows travel, unlike a fixed tz)."""
    try:
        return parse_ts(payload[local_key]) - parse_ts(payload[gmt_key])
    except (KeyError, TypeError, ValueError):
        return timedelta(0)


def ms_to_local(ms, offset: timedelta) -> str | None:
    if ms is None:
        return None
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + offset).strftime("%Y-%m-%d %H:%M:%S")


def dig(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def date_chunks(start: date, end: date, max_days: int):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=max_days - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def days_between(start: date, end: date):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, mode TEXT,
    window_start TEXT, window_end TEXT, api_calls INTEGER, errors INTEGER, error_sample TEXT);
CREATE TABLE IF NOT EXISTS raw_documents (
    endpoint TEXT, doc_key TEXT, fetched_at TEXT, raw TEXT, PRIMARY KEY (endpoint, doc_key));

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1), display_name TEXT, locale TEXT, measurement_system TEXT,
    time_zone TEXT, raw TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS heart_rate_zones (
    sport TEXT, training_method TEXT, resting_hr INTEGER, max_hr INTEGER, lactate_threshold_hr INTEGER,
    zone1_floor INTEGER, zone2_floor INTEGER, zone3_floor INTEGER, zone4_floor INTEGER, zone5_floor INTEGER,
    raw TEXT, PRIMARY KEY (sport, training_method));
CREATE TABLE IF NOT EXISTS activity_types (type_key TEXT PRIMARY KEY, parent_key TEXT);

CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY, uuid TEXT, name TEXT, type_key TEXT, parent_type_key TEXT,
    event_type TEXT, calendar_date TEXT, start_time_local TEXT, start_time_gmt TEXT, end_time_gmt TEXT,
    duration_s REAL, elapsed_duration_s REAL, moving_duration_s REAL, distance_m REAL,
    calories REAL, bmr_calories REAL, avg_hr REAL, max_hr REAL, avg_speed_mps REAL, max_speed_mps REAL,
    elevation_gain_m REAL, elevation_loss_m REAL, min_elevation_m REAL, max_elevation_m REAL,
    avg_power REAL, max_power REAL, norm_power REAL, max_20min_power REAL,
    avg_bike_cadence REAL, avg_run_cadence REAL, steps INTEGER,
    total_sets INTEGER, active_sets INTEGER, total_reps INTEGER,
    aerobic_te_message TEXT, anaerobic_te_message TEXT, training_effect_label TEXT,
    moderate_intensity_min INTEGER, vigorous_intensity_min INTEGER, body_battery_delta INTEGER,
    avg_respiration REAL, avg_stress REAL, location_name TEXT,
    start_lat REAL, start_lon REAL, end_lat REAL, end_lon REAL,
    hr_zone1_s REAL, hr_zone2_s REAL, hr_zone3_s REAL, hr_zone4_s REAL, hr_zone5_s REAL,
    lap_count INTEGER, is_manual INTEGER, is_pr INTEGER, water_estimated_ml REAL,
    raw TEXT, detail_raw TEXT, workouts_raw TEXT,
    fit_path TEXT, fit_parsed_at TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities (calendar_date);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities (type_key);

CREATE TABLE IF NOT EXISTS activity_laps (
    activity_id INTEGER, lap_index INTEGER, start_time_gmt TEXT, elapsed_s REAL, timer_s REAL,
    distance_m REAL, avg_hr INTEGER, max_hr INTEGER, avg_speed_mps REAL, max_speed_mps REAL,
    avg_power INTEGER, max_power INTEGER, norm_power INTEGER, avg_cadence INTEGER, max_cadence INTEGER,
    ascent_m REAL, descent_m REAL, calories INTEGER, intensity TEXT, lap_trigger TEXT,
    PRIMARY KEY (activity_id, lap_index));
CREATE TABLE IF NOT EXISTS activity_records (
    activity_id INTEGER, ts_gmt TEXT, lat REAL, lon REAL, altitude_m REAL, distance_m REAL,
    speed_mps REAL, heart_rate INTEGER, cadence INTEGER, power INTEGER, temperature_c REAL);
CREATE INDEX IF NOT EXISTS idx_records_activity ON activity_records (activity_id, ts_gmt);
CREATE TABLE IF NOT EXISTS activity_sets (
    activity_id INTEGER, set_index INTEGER, start_time_gmt TEXT, duration_s REAL, set_type TEXT,
    category TEXT, category_subtype TEXT, repetitions INTEGER, weight_kg REAL,
    PRIMARY KEY (activity_id, set_index));

CREATE TABLE IF NOT EXISTS daily_summary (
    calendar_date TEXT PRIMARY KEY, steps INTEGER, step_goal INTEGER, distance_m REAL,
    floors_up INTEGER, floors_down INTEGER, floors_goal INTEGER,
    calories_total INTEGER, calories_active INTEGER, calories_resting INTEGER,
    hr_min INTEGER, hr_max INTEGER, hr_resting INTEGER,
    intensity_moderate_min INTEGER, intensity_vigorous_min INTEGER, intensity_goal_min INTEGER,
    stress_avg INTEGER, stress_max INTEGER, stress_qualifier TEXT,
    stress_rest_s INTEGER, stress_low_s INTEGER, stress_medium_s INTEGER, stress_high_s INTEGER,
    stress_activity_s INTEGER, stress_uncategorized_s INTEGER,
    bb_min INTEGER, bb_max INTEGER, bb_charged INTEGER, bb_drained INTEGER, bb_latest INTEGER,
    respiration_avg REAL, respiration_min REAL, respiration_max REAL,
    spo2_avg REAL, spo2_min REAL, hydration_goal_ml REAL, hydration_ml REAL,
    includes_wellness INTEGER, includes_activity INTEGER, day_start_gmt TEXT, day_start_local TEXT,
    raw TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS sleep (
    calendar_date TEXT PRIMARY KEY, sleep_start_gmt TEXT, sleep_end_gmt TEXT,
    sleep_start_local TEXT, sleep_end_local TEXT,
    sleep_s INTEGER, nap_s INTEGER, deep_s INTEGER, light_s INTEGER, rem_s INTEGER, awake_s INTEGER,
    unmeasurable_s INTEGER, awake_count INTEGER, score INTEGER, score_qualifier TEXT,
    score_feedback TEXT, avg_hr REAL, avg_spo2 REAL, lowest_spo2 REAL,
    avg_respiration REAL, lowest_respiration REAL, highest_respiration REAL, avg_sleep_stress REAL,
    sleep_need_min INTEGER, breathing_disruption TEXT, raw TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS body_battery_samples (ts_gmt TEXT PRIMARY KEY, ts_local TEXT, calendar_date TEXT, level INTEGER);
CREATE INDEX IF NOT EXISTS idx_bb_date ON body_battery_samples (calendar_date);
CREATE TABLE IF NOT EXISTS body_battery_events (
    calendar_date TEXT, event_type TEXT, start_gmt TEXT, start_local TEXT, duration_s REAL,
    impact INTEGER, feedback_type TEXT, short_feedback TEXT,
    PRIMARY KEY (calendar_date, start_gmt, event_type));

CREATE TABLE IF NOT EXISTS heart_rate_samples (ts_gmt TEXT PRIMARY KEY, ts_local TEXT, calendar_date TEXT, bpm INTEGER);
CREATE INDEX IF NOT EXISTS idx_hr_date ON heart_rate_samples (calendar_date);
CREATE TABLE IF NOT EXISTS heart_rate_daily (
    calendar_date TEXT PRIMARY KEY, min_hr INTEGER, max_hr INTEGER, resting_hr INTEGER,
    resting_hr_7d_avg INTEGER, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS hrv (
    calendar_date TEXT PRIMARY KEY, weekly_avg INTEGER, last_night_avg INTEGER, last_night_5min_high INTEGER,
    baseline_low_upper INTEGER, baseline_balanced_low INTEGER, baseline_balanced_upper INTEGER,
    status TEXT, feedback TEXT, raw TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS hrv_readings (ts_gmt TEXT PRIMARY KEY, ts_local TEXT, calendar_date TEXT, hrv_ms INTEGER);
CREATE INDEX IF NOT EXISTS idx_hrv_readings_date ON hrv_readings (calendar_date);

CREATE TABLE IF NOT EXISTS spo2 (
    calendar_date TEXT PRIMARY KEY, avg REAL, lowest REAL, latest REAL, avg_sleep REAL,
    last_7d_avg REAL, raw TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS spo2_samples (
    ts_gmt TEXT, kind TEXT, ts_local TEXT, calendar_date TEXT, spo2 INTEGER, PRIMARY KEY (ts_gmt, kind));

CREATE TABLE IF NOT EXISTS vo2max (
    calendar_date TEXT, kind TEXT, sport TEXT, sub_sport TEXT, value REAL, precise_value REAL,
    PRIMARY KEY (calendar_date, kind));

CREATE TABLE IF NOT EXISTS fitness_age (
    calendar_date TEXT PRIMARY KEY, chronological_age INTEGER, fitness_age REAL, achievable_fitness_age REAL,
    rhr INTEGER, vigorous_days_avg REAL, vigorous_minutes_avg REAL, bmi REAL, raw TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS weight (
    sample_pk INTEGER PRIMARY KEY, calendar_date TEXT, ts_gmt TEXT, weight_kg REAL, bmi REAL,
    body_fat_pct REAL, body_water_pct REAL, bone_mass_kg REAL, muscle_mass_kg REAL,
    visceral_fat REAL, metabolic_age REAL, source_type TEXT, raw TEXT);

CREATE TABLE IF NOT EXISTS wellness_activities (
    uuid TEXT PRIMARY KEY, calendar_date TEXT, name TEXT, type TEXT, start_gmt TEXT, end_gmt TEXT,
    start_local TEXT, end_local TEXT, hr_avg REAL, stress_avg REAL, spo2_avg REAL,
    rmssd_hrv REAL, sdrr_hrv REAL, raw TEXT);

CREATE TABLE IF NOT EXISTS food_logs (calendar_date TEXT PRIMARY KEY, raw TEXT, fetched_at TEXT);
"""

VIEWS = """
DROP VIEW IF EXISTS v_daily;
CREATE VIEW v_daily AS
SELECT d.calendar_date, d.steps, d.distance_m, d.floors_up, d.calories_total, d.calories_active,
       d.hr_resting, d.hr_min, d.hr_max, d.stress_avg, d.bb_min, d.bb_max, d.bb_charged, d.bb_drained,
       d.intensity_moderate_min, d.intensity_vigorous_min,
       d.intensity_moderate_min + 2 * d.intensity_vigorous_min AS intensity_minutes,
       d.respiration_avg, d.spo2_avg,
       ROUND(s.sleep_s / 3600.0, 2) AS sleep_h, s.score AS sleep_score, s.deep_s, s.light_s, s.rem_s,
       s.awake_s, s.avg_sleep_stress, s.sleep_need_min,
       h.last_night_avg AS hrv_last_night, h.weekly_avg AS hrv_weekly, h.status AS hrv_status,
       f.fitness_age, sp.avg AS spo2_day_avg,
       (SELECT COUNT(*) FROM activities a WHERE a.calendar_date = d.calendar_date) AS n_activities,
       (SELECT ROUND(SUM(a.duration_s) / 60.0, 1) FROM activities a WHERE a.calendar_date = d.calendar_date) AS activity_min
FROM daily_summary d
LEFT JOIN sleep s USING (calendar_date)
LEFT JOIN hrv h USING (calendar_date)
LEFT JOIN fitness_age f USING (calendar_date)
LEFT JOIN spo2 sp USING (calendar_date);

DROP VIEW IF EXISTS v_activities;
CREATE VIEW v_activities AS
SELECT activity_id, calendar_date, start_time_local, name, type_key, parent_type_key,
       ROUND(duration_s / 60.0, 1) AS duration_min, ROUND(distance_m / 1000.0, 2) AS distance_km,
       ROUND(avg_speed_mps * 3.6, 1) AS avg_kmh, avg_hr, max_hr, avg_power, norm_power,
       elevation_gain_m, calories, training_effect_label, aerobic_te_message,
       moderate_intensity_min, vigorous_intensity_min, body_battery_delta,
       total_sets, total_reps, location_name
FROM activities;

DROP VIEW IF EXISTS v_weekly;
CREATE VIEW v_weekly AS
SELECT strftime('%Y-W%W', calendar_date) AS week, MIN(calendar_date) AS week_start,
       SUM(steps) AS steps, ROUND(AVG(hr_resting), 1) AS avg_resting_hr,
       ROUND(AVG(sleep_h), 2) AS avg_sleep_h, ROUND(AVG(sleep_score), 1) AS avg_sleep_score,
       ROUND(AVG(hrv_last_night), 1) AS avg_hrv, ROUND(AVG(stress_avg), 1) AS avg_stress,
       SUM(intensity_minutes) AS intensity_minutes, SUM(n_activities) AS activities,
       SUM(activity_min) AS activity_min
FROM v_daily GROUP BY week;
"""


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    db.executescript(VIEWS)
    return db


def upsert(db: sqlite3.Connection, table: str, row: dict, pk: tuple[str, ...]):
    """Insert or update. A NULL never overwrites a stored value: Garmin drops intraday-derived fields
    (e.g. daily min HR) once a day ages out of its ~4.5-month detail window, and re-fetching must not
    erase what an earlier sync saved."""
    cols = list(row)
    updates = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in cols if c not in pk) or f"{pk[0]}={pk[0]}"
    db.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}",
        [row[c] for c in cols],
    )


def insert_many(db, table: str, cols: list[str], rows: list[tuple]):
    if rows:
        db.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows
        )


def replace_day_rows(db, table: str, day: str, cols: list[str], rows: list[tuple]):
    """Replace one day's samples, but only when the API returned some. Older days come back as empty or
    all-null arrays (intraday retention), which must not wipe previously synced samples."""
    if rows:
        db.execute(f"DELETE FROM {table} WHERE calendar_date = ?", (day,))
        insert_many(db, table, cols, rows)


def get_state(db, key, default=None):
    row = db.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(db, key, value):
    upsert(db, "sync_state", {"key": key, "value": value}, ("key",))


def save_raw(db, endpoint: str, key: str, payload):
    upsert(db, "raw_documents", {"endpoint": endpoint, "doc_key": key, "fetched_at": now_iso(), "raw": dumps(payload)},
           ("endpoint", "doc_key"))


# --------------------------------------------------------------------------- static data

def sync_static(api: Api, db):
    p = api.get("/userprofile-service/userprofile/settings")
    upsert(db, "profile", {
        "id": 1, "display_name": p.get("displayName"), "locale": p.get("preferredLocale"),
        "measurement_system": p.get("measurementSystem"), "time_zone": p.get("timeZone"),
        "raw": dumps(p), "fetched_at": now_iso()}, ("id",))

    for z in api.get("/biometric-service/heartRateZones/v2") or []:
        upsert(db, "heart_rate_zones", {
            "sport": z.get("sport"), "training_method": z.get("trainingMethod"),
            "resting_hr": z.get("restingHeartRateUsed"), "max_hr": z.get("maxHeartRateUsed"),
            "lactate_threshold_hr": z.get("lactateThresholdHeartRateUsed"),
            **{f"zone{i}_floor": z.get(f"zone{i}Floor") for i in range(1, 6)}, "raw": dumps(z)},
            ("sport", "training_method"))

    types = api.get("/activity-service/activity/activityTypes/simple") or []
    insert_many(db, "activity_types", ["type_key", "parent_key"], [(t["key"], t.get("parent")) for t in types])
    db.commit()
    log(f"profile, HR zones, {len(types)} activity types")


# --------------------------------------------------------------------------- activities

ACTIVITY_FIELDS = {
    "duration_s": "duration", "elapsed_duration_s": "elapsedDuration", "moving_duration_s": "movingDuration",
    "distance_m": "distance", "calories": "calories", "bmr_calories": "bmrCalories", "avg_hr": "averageHR",
    "max_hr": "maxHR", "avg_speed_mps": "averageSpeed", "max_speed_mps": "maxSpeed",
    "elevation_gain_m": "elevationGain", "elevation_loss_m": "elevationLoss",
    "min_elevation_m": "minElevation", "max_elevation_m": "maxElevation",
    "avg_power": "avgPower", "max_power": "maxPower", "norm_power": "normPower",
    "max_20min_power": "max20MinPower", "avg_bike_cadence": "averageBikingCadenceInRevPerMinute",
    "avg_run_cadence": "averageRunningCadenceInStepsPerMinute", "steps": "steps",
    "total_sets": "totalSets", "active_sets": "activeSets", "total_reps": "totalReps",
    "aerobic_te_message": "aerobicTrainingEffectMessage", "anaerobic_te_message": "anaerobicTrainingEffectMessage",
    "training_effect_label": "trainingEffectLabel", "moderate_intensity_min": "moderateIntensityMinutes",
    "vigorous_intensity_min": "vigorousIntensityMinutes", "body_battery_delta": "differenceBodyBattery",
    "avg_respiration": "avgRespirationRate", "avg_stress": "avgStress", "location_name": "locationName",
    "start_lat": "startLatitude", "start_lon": "startLongitude", "end_lat": "endLatitude",
    "end_lon": "endLongitude", "lap_count": "lapCount", "water_estimated_ml": "waterEstimated",
    **{f"hr_zone{i}_s": f"hrTimeInZone_{i}" for i in range(1, 6)},
}


def sync_activity_list(api: Api, db, since: date | None) -> list[int]:
    """Page through the activity list (newest first) until older than `since`. Returns touched IDs."""
    parents = dict(db.execute("SELECT type_key, parent_key FROM activity_types"))
    touched, start = [], 0
    while True:
        page = api.get("/activitylist-service/activities/search/activities", start=start, limit=100) or []
        for a in page:
            type_key = dig(a, "activityType", "typeKey")
            row = {
                "activity_id": a["activityId"], "uuid": a.get("activityUUID"), "name": a.get("activityName"),
                "type_key": type_key, "parent_type_key": parents.get(type_key) or type_key,
                "event_type": dig(a, "eventType", "typeKey"),
                "calendar_date": (a.get("startTimeLocal") or "")[:10] or None,
                "start_time_local": a.get("startTimeLocal"), "start_time_gmt": a.get("startTimeGMT"),
                "end_time_gmt": a.get("endTimeGMT"),
                "is_manual": int(bool(a.get("manualActivity"))), "is_pr": int(bool(a.get("pr"))),
                "raw": dumps(a), "fetched_at": now_iso(),
                **{col: a.get(src) for col, src in ACTIVITY_FIELDS.items()},
            }
            upsert(db, "activities", row, ("activity_id",))
            touched.append(a["activityId"])
        db.commit()
        start += len(page)
        oldest = page[-1].get("startTimeLocal", "")[:10] if page else ""
        if len(page) < 100 or (since and oldest < since.isoformat()):
            break
    log(f"activity list: {len(touched)} activities upserted")
    return touched


def sync_activity_details(api: Api, db, ids: list[int], errors: list):
    if not ids:
        return
    def fetch(aid):
        return aid, api.get(f"/activity-service/activity/{aid}"), api.get(f"/activity-service/activity/{aid}/workouts")
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(fetch, aid) for aid in ids]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                aid, detail, workouts = fut.result()
                db.execute("UPDATE activities SET detail_raw = ?, workouts_raw = ? WHERE activity_id = ?",
                           (dumps(detail), dumps(workouts), aid))
            except ApiError as e:
                errors.append(str(e))
            if i % 50 == 0:
                db.commit()
                log(f"activity details {i}/{len(ids)}")
    db.commit()
    log(f"activity details: {len(ids)} fetched")


def sync_fit_files(api: Api, db, reparse: bool, errors: list):
    FIT_DIR.mkdir(parents=True, exist_ok=True)
    rows = db.execute("SELECT activity_id, is_manual FROM activities").fetchall()
    missing = [aid for aid, manual in rows if not manual and not (FIT_DIR / f"{aid}.zip").exists()]

    def download(aid):
        return aid, api.request(f"/download-service/files/activity/{aid}", accept=ZIP_ACCEPT)
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(download, aid) for aid in missing]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                aid, content = fut.result()
                path = FIT_DIR / f"{aid}.zip"
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(content)
                tmp.replace(path)
                db.execute("UPDATE activities SET fit_path = ?, fit_parsed_at = NULL WHERE activity_id = ?",
                           (str(path.relative_to(ROOT)), aid))
            except ApiError as e:
                errors.append(str(e))
            if i % 50 == 0:
                db.commit()
                log(f"FIT downloads {i}/{len(missing)}")
    db.commit()
    log(f"FIT downloads: {len(missing)} new")

    # Files may exist on disk from an earlier run without fit_path set (e.g. DB rebuilt).
    for aid, _ in rows:
        path = FIT_DIR / f"{aid}.zip"
        if path.exists():
            db.execute("UPDATE activities SET fit_path = ? WHERE activity_id = ? AND fit_path IS NULL",
                       (str(path.relative_to(ROOT)), aid))
    db.commit()

    if fitdecode is None:
        log("fitdecode not installed: skipping FIT parsing (run ./sync.sh to set up the venv)")
        return
    where = "fit_path IS NOT NULL" + ("" if reparse else " AND fit_parsed_at IS NULL")
    todo = [r[0] for r in db.execute(f"SELECT activity_id FROM activities WHERE {where}")]
    for i, aid in enumerate(todo, 1):
        try:
            parse_fit(db, aid, (FIT_DIR / f"{aid}.zip").read_bytes())
            db.execute("UPDATE activities SET fit_parsed_at = ? WHERE activity_id = ?", (now_iso(), aid))
        except Exception as e:  # corrupt/odd FIT files shouldn't stop the sync
            errors.append(f"FIT parse {aid}: {e}")
        if i % 25 == 0:
            db.commit()
            log(f"FIT parsed {i}/{len(todo)}")
    db.commit()
    log(f"FIT parsing: {len(todo)} files")


SEMICIRCLE = 180 / 2 ** 31


def _fit_ts(v) -> str | None:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return None


def _fit_category(v) -> str | None:
    """Exercise categories come as tuples; 65534 means 'unknown'."""
    if v is None:
        return None
    vals = v if isinstance(v, (tuple, list)) else (v,)
    vals = [str(x) for x in vals if x is not None and x != 65534]
    return ",".join(vals) or None


def parse_fit(db, activity_id: int, zip_bytes: bytes):
    records, laps, sets = [], [], []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.namelist():
            if not member.lower().endswith(".fit"):
                continue
            reader = fitdecode.FitReader(
                io.BytesIO(zf.read(member)), check_crc=fitdecode.CrcCheck.WARN,
                error_handling=fitdecode.ErrorHandling.WARN)
            with reader as fr:
                for f in fr:
                    if not isinstance(f, fitdecode.FitDataMessage):
                        continue
                    g = lambda name: f.get_value(name, fallback=None)  # noqa: E731
                    if f.name == "record":
                        lat, lon = g("position_lat"), g("position_long")
                        speed = g("enhanced_speed")
                        alt = g("enhanced_altitude")
                        records.append((
                            activity_id, _fit_ts(g("timestamp")),
                            lat * SEMICIRCLE if isinstance(lat, int) else None,
                            lon * SEMICIRCLE if isinstance(lon, int) else None,
                            alt if alt is not None else g("altitude"), g("distance"),
                            speed if speed is not None else g("speed"),
                            g("heart_rate"), g("cadence"), g("power"), g("temperature")))
                    elif f.name == "lap":
                        laps.append((
                            activity_id, len(laps), _fit_ts(g("start_time")), g("total_elapsed_time"),
                            g("total_timer_time"), g("total_distance"), g("avg_heart_rate"), g("max_heart_rate"),
                            g("enhanced_avg_speed") or g("avg_speed"), g("enhanced_max_speed") or g("max_speed"),
                            g("avg_power"), g("max_power"), g("normalized_power"), g("avg_cadence"),
                            g("max_cadence"), g("total_ascent"), g("total_descent"), g("total_calories"),
                            str(g("intensity")) if g("intensity") is not None else None,
                            str(g("lap_trigger")) if g("lap_trigger") is not None else None))
                    elif f.name == "set":
                        sets.append((
                            activity_id, len(sets), _fit_ts(g("start_time") or g("timestamp")), g("duration"),
                            str(g("set_type")) if g("set_type") is not None else None,
                            _fit_category(g("category")), _fit_category(g("category_subtype")),
                            g("repetitions"), g("weight")))
    for table in ("activity_records", "activity_laps", "activity_sets"):
        db.execute(f"DELETE FROM {table} WHERE activity_id = ?", (activity_id,))
    insert_many(db, "activity_records", ["activity_id", "ts_gmt", "lat", "lon", "altitude_m", "distance_m",
                                         "speed_mps", "heart_rate", "cadence", "power", "temperature_c"], records)
    insert_many(db, "activity_laps", ["activity_id", "lap_index", "start_time_gmt", "elapsed_s", "timer_s",
                                      "distance_m", "avg_hr", "max_hr", "avg_speed_mps", "max_speed_mps",
                                      "avg_power", "max_power", "norm_power", "avg_cadence", "max_cadence",
                                      "ascent_m", "descent_m", "calories", "intensity", "lap_trigger"], laps)
    insert_many(db, "activity_sets", ["activity_id", "set_index", "start_time_gmt", "duration_s", "set_type",
                                      "category", "category_subtype", "repetitions", "weight_kg"], sets)


# --------------------------------------------------------------------------- range endpoints

def store_daily_summary(db, d: dict):
    upsert(db, "daily_summary", {
        "calendar_date": d["calendarDate"],
        "steps": dig(d, "movement", "steps", "value"), "step_goal": dig(d, "movement", "steps", "goal"),
        "distance_m": dig(d, "movement", "steps", "distanceInMeters"),
        "floors_up": dig(d, "movement", "floorsAscended", "value"),
        "floors_down": dig(d, "movement", "floorsDescended", "value"),
        "floors_goal": dig(d, "movement", "floorsAscended", "goal"),
        "calories_total": dig(d, "calories", "burnedTotal"), "calories_active": dig(d, "calories", "burnedActive"),
        "calories_resting": dig(d, "calories", "burnedResting"),
        "hr_min": dig(d, "heartRate", "minValue"), "hr_max": dig(d, "heartRate", "maxValue"),
        "hr_resting": dig(d, "heartRate", "restingValue"),
        "intensity_moderate_min": dig(d, "intensityMinutes", "moderate"),
        "intensity_vigorous_min": dig(d, "intensityMinutes", "vigorous"),
        "intensity_goal_min": dig(d, "intensityMinutes", "goal"),
        "stress_avg": dig(d, "stress", "avgLevel"), "stress_max": dig(d, "stress", "maxLevel"),
        "stress_qualifier": dig(d, "stress", "qualifier"),
        **{f"stress_{k}_s": (lambda v: v // 1000 if v is not None else None)(dig(d, "stress", f"{src}DurationInMillis"))
           for k, src in [("rest", "rest"), ("low", "lowStress"), ("medium", "mediumStress"),
                          ("high", "highStress"), ("activity", "activity"), ("uncategorized", "uncategorized")]},
        "bb_min": dig(d, "bodyBattery", "minValue"), "bb_max": dig(d, "bodyBattery", "maxValue"),
        "bb_charged": dig(d, "bodyBattery", "chargedValue"), "bb_drained": dig(d, "bodyBattery", "drainedValue"),
        "bb_latest": dig(d, "bodyBattery", "latestValue"),
        "respiration_avg": dig(d, "respiration", "avgValue"), "respiration_min": dig(d, "respiration", "minValue"),
        "respiration_max": dig(d, "respiration", "maxValue"),
        "spo2_avg": dig(d, "pulseOx", "avgValue"), "spo2_min": dig(d, "pulseOx", "minValue"),
        "hydration_goal_ml": dig(d, "hydration", "goalInMl"), "hydration_ml": dig(d, "hydration", "consumedInMl"),
        "includes_wellness": int(bool(d.get("includesWellnessData"))),
        "includes_activity": int(bool(d.get("includesActivityData"))),
        "day_start_gmt": norm_ts(dig(d, "wellnessChronology", "startTimestampGmt")),
        "day_start_local": norm_ts(dig(d, "wellnessChronology", "startTimestampLocal")),
        "raw": dumps(d), "fetched_at": now_iso(),
    }, ("calendar_date",))


def store_sleep(db, s: dict):
    upsert(db, "sleep", {
        "calendar_date": s["calendarDate"],
        "sleep_start_gmt": ms_to_iso(s.get("sleepStartTimestampGMT")),
        "sleep_end_gmt": ms_to_iso(s.get("sleepEndTimestampGMT")),
        # *Local timestamps are epoch-ms already shifted to local time.
        "sleep_start_local": ms_to_iso(s.get("sleepStartTimestampLocal")),
        "sleep_end_local": ms_to_iso(s.get("sleepEndTimestampLocal")),
        "sleep_s": s.get("sleepTimeSeconds"), "nap_s": s.get("napTimeSeconds"),
        "deep_s": s.get("deepSleepSeconds"), "light_s": s.get("lightSleepSeconds"),
        "rem_s": s.get("remSleepSeconds"), "awake_s": s.get("awakeSleepSeconds"),
        "unmeasurable_s": s.get("unmeasurableSleepSeconds"), "awake_count": s.get("awakeCount"),
        "score": dig(s, "sleepScores", "overall", "value"),
        "score_qualifier": dig(s, "sleepScores", "overall", "qualifierKey"),
        "score_feedback": s.get("sleepScoreFeedback"), "avg_hr": s.get("avgHeartRate"),
        "avg_spo2": s.get("averageSpO2Value"), "lowest_spo2": s.get("lowestSpO2Value"),
        "avg_respiration": s.get("averageRespirationValue"),
        "lowest_respiration": s.get("lowestRespirationValue"),
        "highest_respiration": s.get("highestRespirationValue"),
        "avg_sleep_stress": s.get("avgSleepStress"), "sleep_need_min": dig(s, "sleepNeed", "actual"),
        "breathing_disruption": s.get("breathingDisruptionSeverity"),
        "raw": dumps(s), "fetched_at": now_iso(),
    }, ("calendar_date",))


def store_body_battery(db, d: dict):
    day = d["date"]
    offset = day_offset(d)
    samples = [(ms_to_iso(ts), ms_to_local(ts, offset), day, lvl)
               for ts, lvl in (d.get("bodyBatteryValuesArray") or []) if lvl is not None]
    replace_day_rows(db, "body_battery_samples", day, ["ts_gmt", "ts_local", "calendar_date", "level"], samples)
    events = []
    for e in d.get("bodyBatteryActivityEvent") or []:
        start = parse_ts(e["eventStartTimeGmt"])
        local = start + timedelta(milliseconds=e.get("timezoneOffset") or 0)
        events.append((day, e.get("eventType"), start.strftime("%Y-%m-%d %H:%M:%S"),
                       local.strftime("%Y-%m-%d %H:%M:%S"), (e.get("durationInMilliseconds") or 0) / 1000,
                       e.get("bodyBatteryImpact"), e.get("feedbackType"), e.get("shortFeedback")))
    replace_day_rows(db, "body_battery_events", day, ["calendar_date", "event_type", "start_gmt", "start_local",
                                                       "duration_s", "impact", "feedback_type", "short_feedback"],
                     events)


def store_weight(db, payload: dict):
    for day in payload.get("dailyWeightSummaries") or []:
        for m in day.get("allWeightMetrics") or []:
            grams = lambda v: v / 1000 if v is not None else None  # noqa: E731
            upsert(db, "weight", {
                "sample_pk": m["samplePk"], "calendar_date": m.get("calendarDate"),
                "ts_gmt": ms_to_iso(m.get("timestampGMT")), "weight_kg": grams(m.get("weight")),
                "bmi": m.get("bmi"), "body_fat_pct": m.get("bodyFat"), "body_water_pct": m.get("bodyWater"),
                "bone_mass_kg": grams(m.get("boneMass")), "muscle_mass_kg": grams(m.get("muscleMass")),
                "visceral_fat": m.get("visceralFat"), "metabolic_age": m.get("metabolicAge"),
                "source_type": m.get("sourceType"), "raw": dumps(m)}, ("sample_pk",))


def store_wellness_activity(db, w: dict):
    summary = {x.get("summaryType"): x for x in w.get("summaryTypeDataList") or []}
    upsert(db, "wellness_activities", {
        "uuid": dig(w, "activityUuid", "uuid"), "calendar_date": w.get("calendarDate"),
        "name": w.get("activityName"), "type": w.get("wellnessActivityType"),
        "start_gmt": norm_ts(w.get("startTimestampGMT")), "end_gmt": norm_ts(w.get("endTimestampGMT")),
        "start_local": norm_ts(w.get("startTimestampLocal")), "end_local": norm_ts(w.get("endTimestampLocal")),
        "hr_avg": dig(summary, "HEART_RATE", "avgValue"), "stress_avg": dig(summary, "STRESS", "avgValue"),
        "spo2_avg": dig(summary, "SPO2", "avgValue"), "rmssd_hrv": dig(summary, "RMSSD_HRV", "avgValue"),
        "sdrr_hrv": dig(summary, "SDRR_HRV", "avgValue"), "raw": dumps(w)}, ("uuid",))


def store_vo2max(db, entry: dict):
    for kind in ("generic", "cycling"):
        v = entry.get(kind)
        if v and v.get("calendarDate"):
            upsert(db, "vo2max", {
                "calendar_date": v["calendarDate"], "kind": kind, "sport": v.get("sport"),
                "sub_sport": v.get("subSport"), "value": v.get("vo2MaxValue"),
                "precise_value": v.get("vo2MaxPreciseValue")}, ("calendar_date", "kind"))


def sync_ranges(api: Api, db, start: date, end: date, errors: list):
    """Range endpoints. max_days are the server-side limits found by probing (longer → HTTP 400)."""
    jobs = [
        ("daily summaries", 365, lambda s, e: api.get("/usersummary-service/v2/dailysummaries",
                                                       startDate=s, endDate=e),
         lambda p: [store_daily_summary(db, d) for d in (p or {}).get("data") or []]),
        ("sleep", 31, lambda s, e: api.get("/sleep-service/sleep/dailySleepSummariesByDate",
                                           startDate=s, endDate=e),
         lambda p: [store_sleep(db, d) for d in p or [] if d.get("calendarDate")]),
        ("body battery", 31, lambda s, e: api.get("/wellness-service/wellness/bodyBattery/reports/daily",
                                                  startDate=s, endDate=e, chunks=BODY_BATTERY_CHUNKS),
         lambda p: [store_body_battery(db, d) for d in p or []]),
        ("VO2 max", 365, lambda s, e: api.get(f"/metrics-service/metrics/maxmet/daily/{s}/{e}"),
         lambda p: [store_vo2max(db, d) for d in p or []]),
        ("weight", 365, lambda s, e: api.get(f"/weight-service/weight/range/{s}/{e}", includeAll="true"),
         lambda p: store_weight(db, p or {})),
        ("weight goals", 365, lambda s, e: api.get(f"/goal-service/goal/user/effective/weightgoal/{s}/{e}"),
         None),
        ("wellness activities", 365,
         lambda s, e: api.get(f"/wellnessactivity-service/activity/summary/range/{s}/{e}"),
         lambda p: [store_wellness_activity(db, w) for w in p or []]),
    ]
    for name, max_days, fetch, store in jobs:
        n = 0
        for s, e in date_chunks(start, end, max_days):
            try:
                payload = fetch(s.isoformat(), e.isoformat())
            except ApiError as ex:
                errors.append(f"{name} {s}..{e}: {ex}")
                continue
            if store:
                store(payload)
            else:
                save_raw(db, name.replace(" ", "_"), f"{s}..{e}", payload)
            n += 1
        db.commit()
        log(f"{name}: {n} chunk(s)")


# --------------------------------------------------------------------------- per-day endpoints

def store_heart_rate(db, day: str, p: dict):
    if not p:
        return
    offset = day_offset(p)
    upsert(db, "heart_rate_daily", {
        "calendar_date": day, "min_hr": p.get("minHeartRate"), "max_hr": p.get("maxHeartRate"),
        "resting_hr": p.get("restingHeartRate"), "resting_hr_7d_avg": p.get("lastSevenDaysAvgRestingHeartRate"),
        "fetched_at": now_iso()}, ("calendar_date",))
    replace_day_rows(db, "heart_rate_samples", day, ["ts_gmt", "ts_local", "calendar_date", "bpm"],
                     [(ms_to_iso(ts), ms_to_local(ts, offset), day, bpm)
                      for ts, bpm in (p.get("heartRateValues") or []) if bpm is not None])


def store_hrv(db, day: str, p: dict):
    if not p or not p.get("hrvSummary"):
        return
    s = p["hrvSummary"]
    upsert(db, "hrv", {
        "calendar_date": day, "weekly_avg": s.get("weeklyAvg"), "last_night_avg": s.get("lastNightAvg"),
        "last_night_5min_high": s.get("lastNight5MinHigh"),
        "baseline_low_upper": dig(s, "baseline", "lowUpper"),
        "baseline_balanced_low": dig(s, "baseline", "balancedLow"),
        "baseline_balanced_upper": dig(s, "baseline", "balancedUpper"),
        "status": s.get("status"), "feedback": s.get("feedbackPhrase"),
        "raw": dumps({k: v for k, v in p.items() if k != "hrvReadings"}), "fetched_at": now_iso()},
        ("calendar_date",))
    replace_day_rows(db, "hrv_readings", day, ["ts_gmt", "ts_local", "calendar_date", "hrv_ms"],
                     [(norm_ts(r.get("readingTimeGMT")), norm_ts(r.get("readingTimeLocal")), day, r.get("hrvValue"))
                      for r in p.get("hrvReadings") or [] if r.get("hrvValue") is not None])


def store_spo2(db, day: str, p: dict):
    if not p or p.get("averageSpO2") is None and not p.get("spO2HourlyAverages"):
        return
    offset = day_offset(p)
    upsert(db, "spo2", {
        "calendar_date": day, "avg": p.get("averageSpO2"), "lowest": p.get("lowestSpO2"),
        "latest": p.get("latestSpO2"), "avg_sleep": p.get("avgSleepSpO2"),
        "last_7d_avg": p.get("lastSevenDaysAvgSpO2"),
        "raw": dumps({k: v for k, v in p.items() if k not in ("spO2HourlyAverages", "spO2SingleValues")}),
        "fetched_at": now_iso()}, ("calendar_date",))
    rows = []
    for kind, key in (("hourly", "spO2HourlyAverages"), ("single", "spO2SingleValues")):
        for v in p.get(key) or []:
            if len(v) >= 2 and v[1] is not None:
                rows.append((ms_to_iso(v[0]), kind, ms_to_local(v[0], offset), day, v[1]))
    replace_day_rows(db, "spo2_samples", day, ["ts_gmt", "kind", "ts_local", "calendar_date", "spo2"], rows)


def store_fitness_age(db, day: str, p: dict):
    if not p or p.get("fitnessAge") is None:
        return
    c = p.get("components") or {}
    upsert(db, "fitness_age", {
        "calendar_date": day, "chronological_age": p.get("chronologicalAge"), "fitness_age": p.get("fitnessAge"),
        "achievable_fitness_age": p.get("achievableFitnessAge"), "rhr": dig(c, "rhr", "value"),
        "vigorous_days_avg": dig(c, "vigorousDaysAvg", "value"),
        "vigorous_minutes_avg": dig(c, "vigorousMinutesAvg", "value"), "bmi": dig(c, "bmi", "value"),
        "raw": dumps(p), "fetched_at": now_iso()}, ("calendar_date",))


def store_food(db, day: str, p: dict):
    if p and (p.get("mealDetails") or p.get("loggedFoodsWithServingSizes")):
        upsert(db, "food_logs", {"calendar_date": day, "raw": dumps(p), "fetched_at": now_iso()}, ("calendar_date",))


PER_DAY = {
    "heart_rate": (lambda api, d: api.get("/wellness-service/wellness/dailyHeartRate", date=d), store_heart_rate),
    "hrv": (lambda api, d: api.get(f"/hrv-service/hrv/{d}"), store_hrv),
    "spo2": (lambda api, d: api.get(f"/wellness-service/wellness/daily/spo2acclimation/{d}"), store_spo2),
    "fitness_age": (lambda api, d: api.get(f"/fitnessage-service/fitnessage/v2/{d}"), store_fitness_age),
    "food_logs": (lambda api, d: api.get(f"/nutrition-service/food/logs/{d}"), store_food),
}


def sync_per_day(api: Api, db, start: date, end: date, errors: list):
    tasks = [(kind, d.isoformat()) for d in days_between(start, end) for kind in PER_DAY]

    def run(kind, day):
        return kind, day, PER_DAY[kind][0](api, day)
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(run, k, d) for k, d in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                kind, day, payload = fut.result()
                PER_DAY[kind][1](db, day, payload)
            except ApiError as e:
                if e.status != 404:  # days without data
                    errors.append(str(e))
            if i % 200 == 0:
                db.commit()
                log(f"per-day endpoints {i}/{len(tasks)}")
    db.commit()
    log(f"per-day endpoints: {len(tasks)} requests ({', '.join(PER_DAY)})")


# --------------------------------------------------------------------------- orchestration

def discover_start(api: Api, end: date) -> date | None:
    """Walk back a year at a time until daily summaries come back empty."""
    earliest, cur_end = None, end
    while True:
        cur_start = cur_end - timedelta(days=364)
        data = (api.get("/usersummary-service/v2/dailysummaries",
                        startDate=cur_start.isoformat(), endDate=cur_end.isoformat()) or {}).get("data") or []
        days = [d["calendarDate"] for d in data if d.get("includesWellnessData") or d.get("includesActivityData")]
        if not days:
            return earliest
        earliest = date.fromisoformat(min(days))
        if earliest > cur_start:
            return earliest
        cur_end = cur_start - timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="fetch everything since the first day with data")
    ap.add_argument("--start", type=date.fromisoformat, help="window start (YYYY-MM-DD)")
    ap.add_argument("--end", type=date.fromisoformat, help="window end (default: today)")
    ap.add_argument("--days", type=int, help="re-fetch the last N days")
    ap.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP_DAYS,
                    help=f"incremental: days before the last synced day to re-fetch (default {DEFAULT_OVERLAP_DAYS})")
    ap.add_argument("--skip-fit", action="store_true", help="don't download/parse original FIT files")
    ap.add_argument("--reparse-fit", action="store_true", help="re-parse all downloaded FIT files")
    ap.add_argument("--skip-details", action="store_true", help="don't fetch per-activity detail/workouts")
    ap.add_argument("--skip-notes", action="store_true", help="don't import the Apple Notes strength log")
    args = ap.parse_args()

    api = Api(load_api_key())
    db = open_db()
    errors: list[str] = []
    started = now_iso()
    end = args.end or date.today()
    last_end = get_state(db, "last_window_end")

    if args.start:
        start, mode = args.start, "range"
    elif args.days:
        start, mode = end - timedelta(days=args.days - 1), f"last {args.days} days"
    elif args.full or not last_end:
        start, mode = None, "full"
    else:
        start, mode = date.fromisoformat(last_end) - timedelta(days=args.overlap), "incremental"
    log(f"mode: {mode}")

    sync_static(api, db)
    touched = sync_activity_list(api, db, since=start)
    if start is None:
        oldest_activity = db.execute("SELECT MIN(calendar_date) FROM activities").fetchone()[0]
        candidates = [d for d in (discover_start(api, end),
                                  date.fromisoformat(oldest_activity) if oldest_activity else None) if d]
        start = min(candidates) if candidates else end - timedelta(days=30)
    log(f"window: {start} .. {end}")

    sync_ranges(api, db, start, end, errors)
    sync_per_day(api, db, start, end, errors)

    if not args.skip_details:
        ids = [r[0] for r in db.execute(
            "SELECT activity_id FROM activities WHERE detail_raw IS NULL OR calendar_date >= ?", (start.isoformat(),))]
        sync_activity_details(api, db, ids, errors)
    if not args.skip_fit:
        sync_fit_files(api, db, args.reparse_fit, errors)
    if not args.skip_notes:
        try:
            import notes_import
            notes_import.sync(db, log=log)
        except Exception as e:  # non-macOS, Notes permission denied, ...
            errors.append(f"Apple Notes import: {e}")

    # Only advance the high-water mark after a clean run: after days offline a partial catch-up
    # (e.g. network dropped) must be retried from the old mark next time, not skipped.
    if not errors and (not last_end or end.isoformat() > last_end):
        set_state(db, "last_window_end", end.isoformat())
    first = get_state(db, "first_data_date")
    if not first or start.isoformat() < first:
        set_state(db, "first_data_date", start.isoformat())
    set_state(db, "last_sync_at", now_iso())
    db.execute("INSERT INTO sync_runs (started_at, finished_at, mode, window_start, window_end, api_calls, errors, "
               "error_sample) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (started, now_iso(), mode, start.isoformat(), end.isoformat(), api.calls, len(errors),
                "\n".join(errors[:20]) or None))
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()

    log(f"done: {api.calls} API calls ({api.throttled} throttled retries), {len(touched)} activities in list window, {len(errors)} errors")
    for e in errors[:20]:
        print("  !", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
