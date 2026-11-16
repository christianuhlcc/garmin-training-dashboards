"""The only module that knows how the synced data is stored.

Source: ../garmin.db (SQLite written by garmin_sync.py and notes_import.py, see ../AGENTS.md) and
../data/fit/<activity_id>.zip. Everything is opened read-only. Every loader returns a DataFrame with
the canonical columns of the plan (section 4). Columns are NULL where the source has nothing.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

PROJECT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT / "build" / "cache"

ACTIVITY_COLS = [
    "activity_id", "start_ts_local", "sport", "sub_sport", "name", "duration_s", "moving_s", "distance_m",
    "elev_gain_m", "avg_hr", "max_hr", "avg_power", "max_power", "avg_cadence", "avg_speed", "calories",
    "device", "gear", "start_lat", "start_lon", "end_lat", "end_lon", "has_hr_stream", "has_power_stream",
    "garmin_training_load", "garmin_aerobic_te", "garmin_anaerobic_te",
    # extras beyond the canonical set
    "date", "has_gps_stream", "hr_sensor", "power_sensor", "hr_quality", "is_manual",
]
STREAM_COLS = ["t_s", "hr", "power", "speed", "cadence", "altitude", "lat", "lon"]
DAILY_COLS = ["date", "resting_hr", "steps", "intensity_min_moderate", "intensity_min_vigorous", "stress_avg",
              "body_battery_max", "body_battery_min", "respiration_avg_sleep", "spo2_avg_sleep", "vo2max",
              "calories_active", "floors"]
SLEEP_COLS = ["date", "bed_ts", "wake_ts", "total_s", "deep_s", "light_s", "rem_s", "awake_s", "sleep_score",
              "need_min", "need_baseline_min", "need_hrv_adj", "need_training_adj", "need_history_adj", "need_nap_adj"]
HRV_COLS = ["date", "hrv_overnight_avg_ms", "hrv_status", "garmin_baseline_low", "garmin_baseline_high"]
WEIGHT_COLS = ["date", "weight_kg", "body_fat_pct"]
STRENGTH_COLS = ["activity_id", "date", "set_index", "exercise_raw", "exercise", "reps", "weight_kg",
                 "duration_s", "set_type", "source",
                 # extras: implements = 2 for a dumbbell pair (weight_kg is per implement), session id
                 "implements", "to_failure", "workout_id"]


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((PROJECT / "config.yaml").read_text())


def db_path() -> Path:
    return (PROJECT / config()["source"]["sqlite"]).resolve()


def fit_dir() -> Path:
    return (PROJECT / config()["source"]["fit_dir"]).resolve()


def connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(f"synced database not found: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _query(sql: str, params=()) -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query(sql, con, params=params)


def _has_table(name: str) -> bool:
    with connect() as con:
        return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def _to_dt(df: pd.DataFrame, *cols: str) -> pd.DataFrame:
    for c in cols:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


# --------------------------------------------------------------------------- activities

def _sensors(detail_raw: str | None) -> tuple[str | None, str | None]:
    """(hr_sensor, power_sensor) from the activity detail's metadataDTO.sensors list."""
    if not detail_raw:
        return None, None
    sensors = (json.loads(detail_raw).get("metadataDTO") or {}).get("sensors") or []
    hr = next((s.get("manufacturer") for s in sensors if s.get("antplusDeviceType") == "HEART_RATE"
               or "HEART" in str(s.get("deviceType", "")).upper()), None)
    pw = next((s.get("manufacturer") or "unknown" for s in sensors
               if s.get("antplusDeviceType") in ("BIKE_POWER", "FITNESS_EQUIPMENT")
               or "POWER" in str(s.get("deviceType", "")).upper()), None)
    return (hr.lower() if hr else None), (pw.lower() if pw else None)


def load_fit_meta() -> dict[int, dict]:
    """activity_id -> {device, hr_sensor, power_sensor} from the FIT file's file_id and device_info
    messages. Scanning is slow (whole file), so results are cached in build/cache per file size."""
    cache_file = CACHE_DIR / "fit_meta.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    names = {int(k): v for k, v in (config().get("devices") or {}).items()}
    changed, out = False, {}
    d = fit_dir()
    for path in sorted(d.glob("*.zip")) if d.exists() else []:
        key = f"{path.stem}:{path.stat().st_size}"
        if key not in cache:
            cache[key] = _scan_fit(path)
            changed = True
        meta = cache[key]
        if not meta:
            continue
        mfr, prod = meta["manufacturer"], meta["product"]
        device = names.get(prod) if mfr == "garmin" and isinstance(prod, int) else None
        out[int(path.stem)] = {"device": device or f"{mfr}:{prod}", "hr_sensor": meta["hr_sensor"],
                               "power_sensor": meta["power_sensor"]}
    if changed:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    return out


def _scan_fit(path: Path) -> dict | None:
    import fitdecode
    meta = {"manufacturer": None, "product": None, "hr_sensor": None, "power_sensor": None}
    try:
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if not member.lower().endswith(".fit"):
                    continue
                with fitdecode.FitReader(io.BytesIO(zf.read(member)), check_crc=fitdecode.CrcCheck.DISABLED,
                                         error_handling=fitdecode.ErrorHandling.IGNORE) as fr:
                    for f in fr:
                        if not isinstance(f, fitdecode.FitDataMessage):
                            continue
                        if f.name == "file_id" and meta["manufacturer"] is None:
                            meta["manufacturer"] = str(f.get_value("manufacturer", fallback=None))
                            prod = f.get_value("garmin_product", fallback=None)
                            if prod is None:
                                prod = f.get_value("product", fallback=None)
                            meta["product"] = prod if isinstance(prod, int) else str(prod)
                        elif f.name == "device_info":
                            kind = str(f.get_value("antplus_device_type", fallback=None)
                                       or f.get_value("ble_device_type", fallback=None) or "")
                            if f.get_value("source_type", fallback=None) == "local" or not kind:
                                continue
                            mfr = f.get_value("manufacturer", fallback=None)
                            if kind == "heart_rate" and (meta["hr_sensor"] is None or mfr):
                                meta["hr_sensor"] = str(mfr or "unknown")
                            elif kind in ("bike_power", "fitness_equipment") and (meta["power_sensor"] is None or mfr):
                                meta["power_sensor"] = str(mfr or "unknown")
    except Exception:
        return None
    return meta


def load_activities() -> pd.DataFrame:
    df = _query("""
        SELECT a.activity_id, a.start_time_local AS start_ts_local, a.parent_type_key AS sport,
               a.type_key AS sub_sport, a.name, a.duration_s, a.moving_duration_s AS moving_s, a.distance_m,
               a.elevation_gain_m AS elev_gain_m, a.avg_hr, a.max_hr, a.avg_power, a.max_power,
               COALESCE(a.avg_bike_cadence, a.avg_run_cadence) AS avg_cadence, a.avg_speed_mps AS avg_speed,
               a.calories, NULL AS gear, a.start_lat, a.start_lon, a.end_lat, a.end_lon,
               EXISTS (SELECT 1 FROM activity_records r WHERE r.activity_id = a.activity_id
                       AND r.heart_rate IS NOT NULL) AS has_hr_stream,
               EXISTS (SELECT 1 FROM activity_records r WHERE r.activity_id = a.activity_id
                       AND r.power IS NOT NULL) AS has_power_stream,
               EXISTS (SELECT 1 FROM activity_records r WHERE r.activity_id = a.activity_id
                       AND r.lat IS NOT NULL) AS has_gps_stream,
               NULL AS garmin_training_load,
               a.aerobic_te_message AS garmin_aerobic_te, a.anaerobic_te_message AS garmin_anaerobic_te,
               a.calendar_date AS date, a.is_manual, a.detail_raw
        FROM activities a ORDER BY a.start_time_gmt
    """)
    # Sensors: Garmin's activity detail JSON first, FIT device_info as fallback.
    sensors = df["detail_raw"].map(_sensors)
    fit = load_fit_meta()
    fit_meta = df["activity_id"].map(lambda a: fit.get(a) or {})
    df["hr_sensor"] = [j[0] or f.get("hr_sensor") for j, f in zip(sensors, fit_meta)]
    df["power_sensor"] = [j[1] or f.get("power_sensor") for j, f in zip(sensors, fit_meta)]
    df["device"] = fit_meta.map(lambda f: f.get("device"))
    df["hr_quality"] = df["hr_sensor"].map(lambda s: "strap" if s else "wrist")
    df.loc[df["avg_hr"].isna(), "hr_quality"] = None
    for c in ("has_hr_stream", "has_power_stream", "has_gps_stream", "is_manual"):
        df[c] = df[c].astype(bool)
    df = _to_dt(df, "start_ts_local")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[ACTIVITY_COLS]


def load_activity_streams(activity_id: int) -> pd.DataFrame:
    from src.adapters import strava_archive  # Strava-archive rides live in the build cache, not in garmin.db
    if int(activity_id) >= strava_archive.STRAVA_ID_OFFSET:
        return strava_archive.load_stream(activity_id)
    df = _query("""
        SELECT ts_gmt, heart_rate AS hr, power, speed_mps AS speed, cadence, altitude_m AS altitude, lat, lon
        FROM activity_records WHERE activity_id = ? ORDER BY ts_gmt
    """, (int(activity_id),))
    ts = pd.to_datetime(df.pop("ts_gmt"))
    df.insert(0, "t_s", (ts - ts.iloc[0]).dt.total_seconds() if len(ts) else pd.Series(dtype=float))
    return df[STREAM_COLS]


# --------------------------------------------------------------------------- daily health

def load_daily() -> pd.DataFrame:
    df = _query("""
        SELECT d.calendar_date AS date, d.hr_resting AS resting_hr, d.steps,
               d.intensity_moderate_min AS intensity_min_moderate,
               d.intensity_vigorous_min AS intensity_min_vigorous, d.stress_avg,
               d.bb_max AS body_battery_max, d.bb_min AS body_battery_min,
               s.avg_respiration AS respiration_avg_sleep,
               COALESCE(s.avg_spo2, sp.avg_sleep) AS spo2_avg_sleep,
               v.precise_value AS vo2max, d.calories_active, d.floors_up AS floors
        FROM daily_summary d
        LEFT JOIN sleep s USING (calendar_date)
        LEFT JOIN spo2 sp USING (calendar_date)
        LEFT JOIN vo2max v ON v.calendar_date = d.calendar_date AND v.kind = 'generic'
        WHERE d.includes_wellness = 1
        ORDER BY d.calendar_date
    """)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[DAILY_COLS]


def load_vo2max() -> pd.DataFrame:
    """All VO2max estimates (kind = generic | cycling); only dates when a new estimate was recorded."""
    df = _query("SELECT calendar_date AS date, kind, sport, sub_sport, precise_value AS vo2max FROM vo2max "
                "ORDER BY calendar_date")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def load_sleep() -> pd.DataFrame:
    """Sleep belongs to the calendar date of wake-up. Timestamps are local wall-clock."""
    df = _query("""
        SELECT calendar_date AS date, sleep_start_local AS bed_ts, sleep_end_local AS wake_ts,
               sleep_s AS total_s, deep_s, light_s, rem_s, awake_s, score AS sleep_score,
               sleep_need_min AS need_min, json_extract(raw, '$.sleepNeed.baseline') AS need_baseline_min,
               json_extract(raw, '$.sleepNeed.hrvAdjustment') AS need_hrv_adj,
               json_extract(raw, '$.sleepNeed.trainingFeedback') AS need_training_adj,
               json_extract(raw, '$.sleepNeed.sleepHistoryAdjustment') AS need_history_adj,
               json_extract(raw, '$.sleepNeed.napAdjustment') AS need_nap_adj
        FROM sleep WHERE sleep_s IS NOT NULL ORDER BY calendar_date
    """)
    df = _to_dt(df, "bed_ts", "wake_ts")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[SLEEP_COLS]


def load_hrv() -> pd.DataFrame:
    df = _query("""
        SELECT calendar_date AS date, last_night_avg AS hrv_overnight_avg_ms, status AS hrv_status,
               baseline_balanced_low AS garmin_baseline_low, baseline_balanced_upper AS garmin_baseline_high
        FROM hrv WHERE last_night_avg IS NOT NULL ORDER BY calendar_date
    """)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[HRV_COLS]


def load_weight() -> pd.DataFrame:
    df = _query("SELECT calendar_date AS date, weight_kg, body_fat_pct FROM weight WHERE weight_kg IS NOT NULL "
                "ORDER BY ts_gmt")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[WEIGHT_COLS]


def load_intraday(kind: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Intraday series: kind in heart_rate (2 min) | body_battery (15 min min/max) | hrv (5 min, sleep) |
    spo2 (hourly). Columns: ts_local, date, value."""
    table, col = {"heart_rate": ("heart_rate_samples", "bpm"), "body_battery": ("body_battery_samples", "level"),
                  "hrv": ("hrv_readings", "hrv_ms"), "spo2": ("spo2_samples", "spo2")}[kind]
    where, params = [], []
    if start:
        where.append("calendar_date >= ?"), params.append(start)
    if end:
        where.append("calendar_date <= ?"), params.append(end)
    sql = f"SELECT ts_local, calendar_date AS date, {col} AS value FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    df = _query(sql + " ORDER BY ts_gmt", params)
    return _to_dt(df, "ts_local")


# --------------------------------------------------------------------------- strength

def load_strength_sets() -> pd.DataFrame:
    """Manual log (Apple Notes, source='manual') wins; Garmin auto-detected FIT sets (source='garmin') are
    returned only for strength activities that have no manual session linked to them.
    weight_kg is per implement (one dumbbell); implements = 2 for a dumbbell pair, NULL for bodyweight."""
    frames = []
    if _has_table("manual_sets"):
        frames.append(_query("""
            SELECT w.garmin_activity_id AS activity_id, s.workout_date AS date, s.set_no AS set_index,
                   s.exercise_raw, s.exercise, s.reps, s.weight_kg, NULL AS duration_s, 'active' AS set_type,
                   'manual' AS source, s.implements, s.to_failure, s.workout_id
            FROM manual_sets s JOIN manual_workouts w USING (workout_id)
            WHERE s.skipped = 0
            ORDER BY s.workout_id, s.exercise_no, s.set_no
        """))
        manual_acts = "(SELECT garmin_activity_id FROM manual_workouts WHERE garmin_activity_id IS NOT NULL)"
    else:
        manual_acts = "(SELECT NULL)"
    frames.append(_query(f"""
        SELECT s.activity_id, a.calendar_date AS date, s.set_index, s.category AS exercise_raw,
               NULL AS exercise, s.repetitions AS reps, s.weight_kg, s.duration_s, s.set_type,
               'garmin' AS source, CASE WHEN s.weight_kg > 0 THEN 1 END AS implements, 0 AS to_failure,
               NULL AS workout_id
        FROM activity_sets s JOIN activities a USING (activity_id)
        WHERE s.activity_id NOT IN {manual_acts}
        ORDER BY s.activity_id, s.set_index
    """))
    frames = [f.astype(object) for f in frames if not f.empty]  # object dtype: no all-NA concat warning
    df = pd.concat(frames, ignore_index=True).infer_objects() if frames else pd.DataFrame(columns=STRENGTH_COLS)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[STRENGTH_COLS]


def load_weekly_reviews(limit: int = 26) -> list[dict]:
    """AI weekly reviews written by ../weekly_review.py (newest first)."""
    if not _has_table("weekly_reviews"):
        return []
    with connect() as con:
        rows = con.execute("SELECT week_iso, week_start, week_end, generated_at, model, review_json FROM weekly_reviews "
                           "ORDER BY week_start DESC LIMIT ?", (limit,)).fetchall()
    return [{"week": r[0], "start": r[1], "end": r[2], "generated_at": r[3], "model": r[4], "review": json.loads(r[5])}
            for r in rows]
