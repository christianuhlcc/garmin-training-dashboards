"""Strava archive adapter (plan E): rides from the "Request your archive" export, as extra activities and streams.

Drop the export into manual/strava_export/ as the extracted folder (activities.csv + activities/) or as the .zip.
Only activities.csv and the activity files are read; nothing else in the archive is touched.

- Metadata from activities.csv (German headers, as exported for a German account); streams from .fit/.gpx/.tcx,
  each optionally gzipped, parsed into the same columns as load_activity_streams() and cached per ride in
  build/cache/strava/<id>.pkl (re-import is idempotent: a cached ride is not parsed again).
- Strava rides get activity_id = STRAVA_ID_OFFSET + Strava id, so they can never collide with Garmin ids.
- Dedupe against Garmin: the two recordings overlap in time by at least half of the shorter one -> the Garmin ride is
  kept (strap HR, device). Starts can differ by many minutes (Sigma head unit started before/after the watch), so a
  start-time tolerance alone misses real duplicates; one person cannot ride two bikes at once.
  (Plan E also says "take power from the Sigma copy if Garmin has none": with the archive of 2026-11-15 no duplicate
  pair has that case, so it is not implemented; the cached streams keep absolute timestamps `ts` if it is ever needed.)
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import warnings

from src.adapters import source as src

STRAVA_ID_OFFSET = 10 ** 12
EXPORT_DIR = src.PROJECT / "manual" / "strava_export"
CACHE = src.CACHE_DIR / "strava"
DEDUPE_MIN_OVERLAP = 0.5  # share of the shorter recording that must overlap a Garmin ride
SEMICIRCLE = 180.0 / 2 ** 31

TYPES = {  # Strava activity type (German export) -> (sub_sport as the Garmin adapter names it)
    "Radfahrt": "cycling", "Virtuelle Radfahrt": "virtual_ride", "E-Bike-Radfahrt": "e_bike_fitness",
    "Ride": "cycling", "Virtual Ride": "virtual_ride", "E-Bike Ride": "e_bike_fitness",
}
COLS = {"id": "Aktivitäts-ID", "date": "Aktivitätsdatum", "name": "Name der Aktivität", "type": "Aktivitätsart",
        "gear": "Aktivitätsausrüstung", "file": "Dateiname", "elapsed": "Verstrichene Zeit.1", "moving": "Bewegungszeit",
        "dist": "Distanz.1", "elev": "Höhenzunahme", "avg_hr": "Durchschnittliche Herzfrequenz",
        "max_hr": "Max. Herzfrequenz.1", "avg_w": "Durchschnittliche Watt", "max_w": "Max. Watt",
        "cad": "Durchschnittliche Trittfrequenz", "speed": "Durchschnittliche Geschwindigkeit", "kcal": "Kalorien"}


# --------------------------------------------------------------------------- archive access

class _Archive:
    """The export as a folder or a zip; read(rel) returns bytes."""

    def __init__(self, root: Path | None = None):
        root = root or EXPORT_DIR
        self.zip = self.base = None
        if (root / "activities.csv").exists():
            self.base = root
        else:
            hits = list(root.glob("*/activities.csv")) if root.exists() else []
            zips = sorted(root.glob("*.zip")) if root.exists() else []
            if hits:
                self.base = hits[0].parent
            elif zips:
                self.zip = zipfile.ZipFile(zips[-1])
                names = [n for n in self.zip.namelist() if n.endswith("activities.csv")]
                if not names:
                    raise FileNotFoundError(f"{zips[-1]} has no activities.csv")
                self.prefix = names[0][: -len("activities.csv")]
            else:
                raise FileNotFoundError(f"no Strava export in {root}")

    def read(self, rel: str) -> bytes:
        if self.zip is not None:
            return self.zip.read(self.prefix + rel)
        return (self.base / rel).read_bytes()

    def key(self, rel: str) -> str:
        if self.zip is not None:
            i = self.zip.getinfo(self.prefix + rel)
            return f"{i.file_size}"
        return str((self.base / rel).stat().st_size)


def available() -> bool:
    try:
        _Archive()
        return True
    except FileNotFoundError:
        return False


def _unzip(data: bytes) -> bytes:
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


# --------------------------------------------------------------------------- stream parsers

def _haversine(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _finish(df: pd.DataFrame, dist_m: np.ndarray | None = None) -> pd.DataFrame:
    """Sort, drop duplicate timestamps, derive speed from distance (cumulative) or GPS when the file has none."""
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df.empty:
        return df
    if "speed" not in df or df["speed"].isna().all():
        dt = np.diff(df["ts"].to_numpy())
        step = None
        if "dist" in df and df["dist"].notna().sum() > 2:
            step = np.diff(df["dist"].to_numpy(dtype=float))
        elif df["lat"].notna().sum() > 2:
            lat, lon = df["lat"].ffill().bfill().to_numpy(), df["lon"].ffill().bfill().to_numpy()
            step = _haversine(lat, lon)
        if step is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                v = np.where(dt > 0, step / dt, np.nan)
            v = np.r_[v[:1], v] if len(v) else v
            df["speed"] = pd.Series(v).where(lambda s: s.between(0, 30)).rolling(3, min_periods=1, center=True).mean()
    df["t_s"] = df["ts"] - df["ts"].iloc[0]
    for c in ("hr", "power", "speed", "cadence", "altitude", "lat", "lon"):
        if c not in df:
            df[c] = np.nan
    return df[["t_s", "hr", "power", "speed", "cadence", "altitude", "lat", "lon", "ts"]]


def _parse_fit(data: bytes) -> tuple[pd.DataFrame, dict]:
    import fitdecode
    warnings.filterwarnings("ignore", message=".*field_description.*")
    rows, meta = [], {"hr_sensor": None, "power_sensor": None, "device": None}
    with fitdecode.FitReader(io.BytesIO(data)) as r:
        for fr in r:
            if fr.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if fr.name == "record":
                g = fr.get_value
                ts = g("timestamp", fallback=None)
                if ts is None:
                    continue
                lat, lon = g("position_lat", fallback=None), g("position_long", fallback=None)
                rows.append({"ts": ts.timestamp(), "hr": g("heart_rate", fallback=None), "power": g("power", fallback=None),
                             "speed": g("enhanced_speed", fallback=None) or g("speed", fallback=None),
                             "cadence": g("cadence", fallback=None), "altitude": g("enhanced_altitude", fallback=None) or g("altitude", fallback=None),
                             "lat": None if lat is None else lat * SEMICIRCLE, "lon": None if lon is None else lon * SEMICIRCLE,
                             "dist": g("distance", fallback=None)})
            elif fr.name == "device_info":
                dtype = str(fr.get_value("device_type", fallback="")).lower()
                if "heart_rate" in dtype:
                    meta["hr_sensor"] = str(fr.get_value("manufacturer", fallback="heart rate sensor"))
                elif "power" in dtype:
                    meta["power_sensor"] = str(fr.get_value("manufacturer", fallback="power meter"))
                elif fr.get_value("device_index", fallback=None) == "creator":
                    meta["device"] = str(fr.get_value("manufacturer", fallback="") or "") or None
    df = pd.DataFrame(rows)
    df = df.astype({c: float for c in df.columns if c != "ts"}, errors="ignore") if len(df) else df
    return _finish(df) if len(df) else df, meta


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return pd.Timestamp(s).tz_convert("UTC").timestamp() if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC").timestamp()
    except Exception:
        return None


def _num(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_gpx(data: bytes) -> tuple[pd.DataFrame, dict]:
    rows = []
    for _, el in ET.iterparse(io.BytesIO(data)):
        if _local(el.tag) != "trkpt":
            continue
        row = {"lat": _num(el.get("lat")), "lon": _num(el.get("lon"))}
        for ch in el.iter():
            t = _local(ch.tag).lower()
            if t == "ele":
                row["altitude"] = _num(ch.text)
            elif t == "time":
                row["ts"] = _iso(ch.text)
            elif t == "hr":
                row["hr"] = _num(ch.text)
            elif t == "cad":
                row["cadence"] = _num(ch.text)
            elif t in ("power", "watts"):
                row["power"] = _num(ch.text)
        rows.append(row)
        el.clear()
    df = pd.DataFrame(rows)
    return (_finish(df) if len(df) else df), {"hr_sensor": None, "power_sensor": None, "device": None}


def _parse_tcx(data: bytes) -> tuple[pd.DataFrame, dict]:
    rows = []
    for _, el in ET.iterparse(io.BytesIO(data)):
        if _local(el.tag) != "Trackpoint":
            continue
        row = {}
        for ch in el.iter():
            t = _local(ch.tag)
            if t == "Time":
                row["ts"] = _iso(ch.text)
            elif t == "LatitudeDegrees":
                row["lat"] = _num(ch.text)
            elif t == "LongitudeDegrees":
                row["lon"] = _num(ch.text)
            elif t == "AltitudeMeters":
                row["altitude"] = _num(ch.text)
            elif t == "DistanceMeters":
                row["dist"] = _num(ch.text)
            elif t == "Cadence":
                row["cadence"] = _num(ch.text)
            elif t == "Watts":
                row["power"] = _num(ch.text)
            elif t == "Speed":
                row["speed"] = _num(ch.text)
            elif t == "Value" and "hr" not in row:
                row["hr"] = _num(ch.text)  # HeartRateBpm/Value
        rows.append(row)
        el.clear()
    df = pd.DataFrame(rows)
    return (_finish(df) if len(df) else df), {"hr_sensor": None, "power_sensor": None, "device": None}


def parse_file(name: str, data: bytes) -> tuple[pd.DataFrame, dict]:
    """(stream with columns t_s, hr, power, speed, cadence, altitude, lat, lon, ts[UTC epoch s], meta) from one activity file."""
    base = re.sub(r"\.gz$", "", name.lower())
    data = _unzip(data)
    if base.endswith((".gpx", ".tcx")):
        data = data.lstrip()  # some exports start with blank lines before the XML declaration
    if base.endswith(".fit"):
        return _parse_fit(data)
    if base.endswith(".gpx"):
        return _parse_gpx(data)
    if base.endswith(".tcx"):
        return _parse_tcx(data)
    raise ValueError(f"unsupported activity file {name}")


# --------------------------------------------------------------------------- import

def _num_col(df, key):
    c = COLS[key]
    return pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(np.nan, index=df.index)


def read_metadata(arc: _Archive) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(arc.read("activities.csv")))
    missing = [COLS[k] for k in ("id", "date", "type", "file") if COLS[k] not in raw]
    if missing:
        raise ValueError(f"activities.csv has no column {missing}: only the German export format is supported")
    df = pd.DataFrame({"strava_id": raw[COLS["id"]].astype("int64"), "csv_start": pd.to_datetime(raw[COLS["date"]], format="%d.%m.%Y, %H:%M:%S", errors="coerce"),
                       "name": raw[COLS["name"]], "type": raw[COLS["type"]], "gear": raw.get(COLS["gear"]), "file": raw[COLS["file"]]})
    for k in ("elapsed", "moving", "dist", "elev", "avg_hr", "max_hr", "avg_w", "max_w", "cad", "speed", "kcal"):
        df[k] = _num_col(raw, k)
    return df[df["type"].isin(TYPES)].reset_index(drop=True)


def _ensure_streams(arc: _Archive, meta: pd.DataFrame) -> dict[int, dict]:
    """Parse every ride file not cached yet. Returns {strava_id: info} (start, n samples, sensors) from the cache index."""
    CACHE.mkdir(parents=True, exist_ok=True)
    idx_path = CACHE / "index.json"
    index = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    done = fail = 0
    for r in meta.itertuples():
        sid = str(r.strava_id)
        if not isinstance(r.file, str):
            continue
        size = arc.key(r.file)
        if sid in index and index[sid].get("size") == size and not index[sid].get("error"):
            continue
        try:
            df, m = parse_file(r.file, arc.read(r.file))
        except Exception as e:  # a broken file is skipped, not fatal
            index[sid] = {"size": size, "n": 0, "error": f"{type(e).__name__}: {e}"[:120]}
            fail += 1
            continue
        if len(df):
            df.to_pickle(CACHE / f"{sid}.pkl")
        index[sid] = {"size": size, "n": int(len(df)), "start": float(df["ts"].iloc[0]) if len(df) else None,
                      "end": float(df["ts"].iloc[-1]) if len(df) else None, **m}
        done += 1
    idx_path.write_text(json.dumps(index))
    if done or fail:
        print(f"[strava] parsed {done} files, {fail} failed", flush=True)
    return index


def load_stream(activity_id: int) -> pd.DataFrame:
    p = CACHE / f"{int(activity_id) - STRAVA_ID_OFFSET}.pkl"
    df = pd.read_pickle(p) if p.exists() else pd.DataFrame(columns=src.STREAM_COLS)
    df = df[src.STREAM_COLS].copy()
    df.loc[df["hr"] <= 30, "hr"] = np.nan  # trainers and sensors write 0 for "no reading"
    return df


def stream_counts() -> dict[int, int]:
    p = CACHE / "index.json"
    if not p.exists():
        return {}
    return {STRAVA_ID_OFFSET + int(k): v["n"] for k, v in json.loads(p.read_text()).items() if v.get("n")}


def _to_local(epoch: float, tz: str) -> pd.Timestamp:
    return pd.Timestamp(epoch, unit="s", tz="UTC").tz_convert(tz).tz_localize(None)


def with_archive(acts: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Garmin activities + Strava archive rides that are not duplicates. Returns (activities, report)."""
    report = {"archive": False, "rides": 0, "added": 0, "duplicates": 0, "no_file": 0, "failed": 0}
    if not available():
        return acts, report
    arc = _Archive()
    meta = read_metadata(arc)
    index = _ensure_streams(arc, meta)
    tz = cfg.get("timezone", "Europe/Berlin")
    report.update(archive=True, rides=int(len(meta)))
    g = acts[acts["sport"] == "cycling"]
    g_start = pd.to_datetime(g["start_ts_local"]).to_numpy("datetime64[s]").astype("int64")
    g_end = g_start + g["duration_s"].fillna(0).to_numpy().astype("int64")
    rows = []
    # Strava files rarely say which sensor recorded HR; the user can state it for the whole archive.
    hr_default = (cfg.get("cycling") or {}).get("strava_hr_source", "unknown")
    for r in meta.itertuples():
        info = index.get(str(r.strava_id)) or {}
        if not info.get("n"):
            report["no_file" if not isinstance(r.file, str) else "failed"] += 1
            continue
        start_local = _to_local(info["start"], tz)
        s = int(start_local.to_datetime64().astype("datetime64[s]").astype("int64"))
        dur = r.elapsed if r.elapsed == r.elapsed else info["end"] - info["start"]  # the CSV misses some elapsed times
        e = s + int(dur)
        overlap = np.minimum(g_end, e) - np.maximum(g_start, s)
        shorter = np.maximum(np.minimum(g_end - g_start, e - s), 1)
        if len(g) and (overlap >= DEDUPE_MIN_OVERLAP * shorter).any():
            report["duplicates"] += 1
            continue
        df = load_stream(STRAVA_ID_OFFSET + r.strava_id)
        gps = df.dropna(subset=["lat", "lon"])
        sub = TYPES[r.type]
        if isinstance(r.gear, str) and r.gear.lower() in [n.lower() for n in cfg["cycling"].get("ebike_names") or []]:
            sub = "e_bike_fitness"  # gear named as an e-bike in config.yaml (Strava type says plain "Radfahrt")
        hr_sensor = info.get("hr_sensor")
        rows.append({
            "activity_id": STRAVA_ID_OFFSET + int(r.strava_id), "start_ts_local": start_local, "sport": "cycling", "sub_sport": sub,
            "name": r.name, "duration_s": dur, "moving_s": r.moving, "distance_m": r.dist,
            "elev_gain_m": r.elev, "avg_hr": r.avg_hr, "max_hr": r.max_hr, "avg_power": r.avg_w, "max_power": r.max_w,
            "avg_cadence": r.cad, "avg_speed": r.speed, "calories": r.kcal, "device": info.get("device"),
            "gear": r.gear if isinstance(r.gear, str) else None,
            "start_lat": gps["lat"].iloc[0] if len(gps) else np.nan, "start_lon": gps["lon"].iloc[0] if len(gps) else np.nan,
            "end_lat": gps["lat"].iloc[-1] if len(gps) else np.nan, "end_lon": gps["lon"].iloc[-1] if len(gps) else np.nan,
            "has_hr_stream": bool(df["hr"].notna().any()), "has_power_stream": bool(df["power"].notna().sum() > 60),
            "garmin_training_load": np.nan, "garmin_aerobic_te": None, "garmin_anaerobic_te": None,
            "date": start_local.date(), "has_gps_stream": bool(len(gps) > 10), "hr_sensor": hr_sensor,
            "power_sensor": info.get("power_sensor"), "hr_quality": None, "is_manual": False,
        })
        rows[-1]["hr_quality"] = None if pd.isna(rows[-1]["avg_hr"]) else ("strap" if hr_sensor else hr_default)
    report["added"] = len(rows)
    if not rows:
        return acts, report
    extra = pd.DataFrame(rows)
    extra["start_ts_local"] = pd.to_datetime(extra["start_ts_local"])
    out = pd.concat([acts, extra[[c for c in acts.columns if c in extra]]], ignore_index=True)
    return out.sort_values("start_ts_local").reset_index(drop=True), report
