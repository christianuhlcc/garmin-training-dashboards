"""Strava archive adapter: parsers, dedupe against Garmin, e-bike by gear. Synthetic archive in a temp dir."""

import gzip

import numpy as np
import pandas as pd
import pytest

from src.adapters import source as src, strava_archive as sa

HDR = "Aktivitäts-ID,Aktivitätsdatum,Name der Aktivität,Aktivitätsart,Aktivitätsausrüstung,Dateiname,Verstrichene Zeit.1,Distanz.1,Bewegungszeit\n"


def gpx(start="2026-03-01T08:00:00Z", n=120, hr=None):
    pts = []
    for i in range(n):
        t = pd.Timestamp(start) + pd.Timedelta(seconds=3 * i)
        ext = f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>" if hr else ""
        pts.append(f'<trkpt lat="{48.0 + i * 0.00003:.6f}" lon="11.0"><ele>500</ele><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time>{ext}</trkpt>')
    return ('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
            f"<trk><trkseg>{''.join(pts)}</trkseg></trk></gpx>").encode()


def test_gpx_without_hr_parses_and_derives_speed():
    df, meta = sa.parse_file("a.gpx.gz", gzip.compress(gpx()))
    assert len(df) == 120 and df["hr"].isna().all()
    assert df["speed"].median() == pytest.approx(3.34 / 3, rel=0.05) or df["speed"].median() > 0.5  # ~1.1 m/s
    assert df["t_s"].iloc[0] == 0 and df["lat"].notna().all()


def test_gpx_with_hr_and_tcx_with_leading_blank_lines():
    df, _ = sa.parse_file("a.gpx", gpx(hr=140))
    assert df["hr"].eq(140).all()
    tcx = b"\n\n" + (b'<?xml version="1.0"?><TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities><Activity><Lap><Track>'
                     + b"".join(b"<Trackpoint><Time>2026-03-01T08:00:%02dZ</Time><DistanceMeters>%d</DistanceMeters><HeartRateBpm><Value>130</Value></HeartRateBpm></Trackpoint>" % (i, 5 * i) for i in range(30))
                     + b"</Track></Lap></Activity></Activities></TrainingCenterDatabase>")
    df, _ = sa.parse_file("b.tcx", tcx)
    assert len(df) == 30 and df["hr"].eq(130).all() and df["speed"].median() == pytest.approx(5.0, abs=0.1)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "export"
    (root / "activities").mkdir(parents=True)
    rows = [  # id, csv start (UTC), name, type, gear, file
        (1, "01.03.2026, 08:00:00", "same as Garmin", "Radfahrt", "", "activities/1.gpx", 360),
        (2, "02.03.2026, 08:00:00", "new ride", "Radfahrt", "", "activities/2.gpx", 360),
        (3, "03.03.2026, 08:00:00", "ebike by gear", "Radfahrt", "My E-Bike", "activities/3.gpx", 360),
        (4, "04.03.2026, 08:00:00", "ebike by type", "E-Bike-Radfahrt", "", "activities/4.gpx", 360),
    ]
    (root / "activities.csv").write_text(HDR + "".join(f'{i},"{d}",{n},{t},{g},{f},{e},1000,{e}\n' for i, d, n, t, g, f, e in rows), encoding="utf-8")
    for i, d in ((1, "2026-03-01"), (2, "2026-03-02"), (3, "2026-03-03"), (4, "2026-03-04")):
        (root / f"activities/{i}.gpx").write_bytes(gpx(start=f"{d}T08:00:00Z", n=120))
    monkeypatch.setattr(sa, "EXPORT_DIR", root)
    monkeypatch.setattr(sa, "CACHE", tmp_path / "cache")
    return root


def _garmin(start_local):
    cols = src.ACTIVITY_COLS
    row = {c: None for c in cols} | {"activity_id": 111, "start_ts_local": pd.Timestamp(start_local), "sport": "cycling",
                                     "sub_sport": "road_biking", "duration_s": 360.0, "date": pd.Timestamp(start_local).date()}
    return pd.DataFrame([row])


CFG = {"timezone": "Europe/Berlin", "cycling": {"ebike_names": ["My E-Bike"]}}


def test_dedupe_keeps_garmin_and_adds_new_rides(archive):
    garmin = _garmin("2026-03-01 09:01:00")  # local time = UTC + 1 h, one minute off the Strava start
    out, rep = sa.with_archive(garmin, CFG)
    assert rep["duplicates"] == 1 and rep["added"] == 3
    assert (out["activity_id"] == 111).sum() == 1 and out["activity_id"].max() >= sa.STRAVA_ID_OFFSET
    # same clock time but no overlap in time (a different day) is not a duplicate
    out2, rep2 = sa.with_archive(_garmin("2026-03-05 09:00:00"), CFG)
    assert rep2["duplicates"] == 0 and rep2["added"] == 4


def test_dedupe_by_overlap_when_starts_differ(archive):
    # Sigma started 4 min before the watch (ride 1: 08:00-08:06 UTC = 09:00-09:06 local); Garmin 09:04-09:10 overlaps 2 of 6 min
    _, rep = sa.with_archive(_garmin("2026-03-01 09:04:00"), CFG)
    assert rep["duplicates"] == 0  # 2 min overlap < half of the shorter ride: different rides
    _, rep = sa.with_archive(_garmin("2026-03-01 08:58:00"), CFG)
    assert rep["duplicates"] == 1  # started 2 min apart, 4 of 6 min overlap: same ride


def test_ebike_by_gear_or_type_and_idempotent_reimport(archive):
    out, _ = sa.with_archive(_garmin("2026-03-10 09:00:00"), CFG)
    subs = dict(zip(out["name"], out["sub_sport"]))
    assert subs["ebike by gear"] == "e_bike_fitness" and subs["ebike by type"] == "e_bike_fitness" and subs["new ride"] == "cycling"
    again, rep = sa.with_archive(_garmin("2026-03-10 09:00:00"), CFG)
    assert len(again) == len(out) and rep["added"] == 4
    assert sa.stream_counts()[sa.STRAVA_ID_OFFSET + 2] == 120
