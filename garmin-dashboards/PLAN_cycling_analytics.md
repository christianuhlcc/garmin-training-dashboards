# Cycling analytics: build plan

Strava-subscription-style analytics, rebuilt from data we already have. Written 2026-11-15.

Scope: four features buildable now (A–D), then a Strava archive import (E) that unlocks the personal heatmap (F)
and back-fills A–D with the Sigma rides and earlier years.

Already covered, not in scope: fitness/freshness (CTL/ATL/TSB), HR zones / intensity distribution, cumulative stats
(year over year), training log (consistency calendar), AI summaries (weekly review), mean-max power + eFTP.

## Data we have

| Source | What | Limits |
|---|---|---|
| `activities` | 194 rides: ~100 `cycling` (mostly commutes), 14 `road_biking`, 76 e-bike, 2 indoor, 2 virtual; GPS on all outdoor | type keys differ (`road_biking`!): always filter on parent `cycling`, as the pipeline does |
| `activity_records` via `load_activity_streams()` | t, hr, power, speed, cadence, altitude, lat, lon | smart recording, ~3 s spacing; `to_1s()` interpolates gaps ≤5 s |
| Power | 10 rides with a power stream: 5 outdoor road rides (Sep 2026, new pedals), 1 short test ride, 4 indoor/virtual | power section is on (`min_power_rides: 5`); road rides make power-based best efforts and segment power meaningful |
| HR quality | `hr_quality == "strap"` marks chest-strap rides | wrist HR excluded from HR-based comparisons |
| Commute detection | `endpoint_clusters()` / `_is_commute()` / `classify_rides()` in `src/metrics/cycling.py` | reuse, don't duplicate |

Rules for every feature: e-bike rides are always excluded. Indoor/virtual rides are excluded from anything GPS-based.
Every panel states its sample size and the data caveat (design principle 2, honest numbers).

---

## A. Matched commute: same route, over time (Strava "Matched Activities")

**Question it answers:** am I getting fitter on the one ride I repeat? The route is constant, so changes in time and heart cost
are the cleanest fitness signal available.

**Metric (per ride, `src/metrics/cycling.py`)**
- `match_routes(rides, cfg)`: group rides whose start and end both fall within 400 m of the same endpoint pair (existing
  clusters) and whose distance is within ±10 % of the group's median, which drops detours. Direction matters: home→work and
  work→home are separate groups. Keep groups with ≥ 6 rides.
- Per ride: moving time, average moving speed, **heartbeats per km** = Σ(hr·dt) / km (strap rides only), avg HR,
  and the share of time stopped (elapsed − moving) as a traffic note.
- Trend: rolling median of the last 5 rides, plus the Δ of the latest ride vs the median of the previous 8 weeks.

**Payload** `cycling.json → matched: {groups: [{id, label, km, n, rides: [{date, moving_s, kmh, beats_per_km, avg_hr, stop_share, strap}], trend}]}`

**UI** (Cycling, group "How you ride"): one panel with a small multiple per direction, showing two aligned time series
(moving time, beats/km) with dots per ride and a rolling-median line. Wrist-HR rides are drawn hollow and leave beats/km empty.
A table view is one click away. Caption: "Same route, same direction. Lower beats per km at the same speed = fitter."

**Tests** synthetic rides: a detour excluded by the ±10 % rule; directions kept apart; beats/km = known value for a
constant-HR stream; e-bike never matched.

**Size** S–M (≈ ½ day). Mostly reuses commute detection.

---

## B. Weekly effort range (Strava "Relative Effort" weekly range)

**Question it answers:** is this week's load normal for me right now? It has to feel calm: inside the range says nothing.

**Metric**
- Weekly load = Σ TRIMP of rides (same `load` series as the fitness chart, so the units agree).
- Range for week *w* = mean load of the 3 completed weeks before *w* × [0.75, 1.25]; weeks with no prior load get no range.
- The current week shows load so far, plus a "same pace as last 3 weeks" marker at today's weekday fraction.

**Payload** `cycling.json → effort_range: {weeks: [{week, load, lo, hi}], current: {load, lo, hi, pace_marker}}`

**UI** (Cycling, "Fitness and volume"): bars per week with the range as a pale band behind them. The bar colour is neutral
inside the band, seq-blue below it and the "over" orange above it, plus a direct label on the current week. Overview/attention
gets **one** line, only when a *completed* week is > 1.5× the upper bound or there are 2 completed weeks in a row below the
lower bound: "Last week's riding load was well above your recent range." No line in normal weeks.

**Tests** range maths on a fixed series; the attention rule fires only on the thresholds; empty weeks handled.

**Size** S (≈ 2–3 h).

**Decided:** rides only.

---

## C. Best efforts: fastest 1 / 5 / 10 / 20 km, longest, biggest climb (Strava "Best Efforts")

**Metric (`best_efforts(stream)`)**
- Resample to 1 s with `to_1s()`; cumulative distance from GPS distance, or from speed when distance is missing.
- Two-pointer sliding window over cumulative distance: minimum elapsed time to cover D for D ∈ {1, 5, 10, 20} km.
  Windows spanning a recording gap > 10 s are invalid.
- Sanity: drop samples > 70 km/h and any GPS jump > 50 m/s.
- Per ride also: longest distance, longest moving time, max elevation gain.
- Power best efforts already exist (mean-max). Link them from here rather than duplicating.

**Payload** `cycling.json → best_efforts: {distances: [1,5,10,20], prs: {D: {time_s, kmh, date, activity_id}}, progression: {D: [[date, time_s]]}, top3: {D: [...]}}`

**UI** (Cycling, "How you ride"): a compact table (distance · best time · km/h · date · 2nd and 3rd best) and a
step chart of PR progression per distance. A new PR gets a quiet "new best" tag for 7 days: no confetti, per the anti-references.
Caption caveat: "GPS speed at ~3 s recording; wind and traffic lights included."

**Tests** a synthetic stream with a known fastest 5 km; a gap invalidates a window; a speed spike is dropped.

**Size** M (≈ ½–1 day).

---

## D. Personal segments (Strava "Segment Efforts")

**Question it answers:** on the bits of the commute I care about (a climb, the fast stretch), am I quicker than before?

**Definition (config.yaml)**
```yaml
cycling:
  segments:
    - name: "Climb past <street>"
      start: [0.0, 0.0]
      end:   [0.0, 0.0]
      radius_m: 30
```
Helper: `python -m src.segments_suggest` prints the median commute track every 500 m (coordinates, km mark, gradient),
so you can pick start and end points without a map. Coordinates from Google Maps also work.

**Detection (`segment_efforts(stream, seg)`)**: find the closest pass within `radius_m` of the start, then of the end
afterwards. The ridden distance between them must be within ±15 % of the straight-line-matched median, otherwise reject
(wrong turn). Effort time is interpolated at the closest-approach points. Record time, km/h, avg HR (strap only) and
avg power when available.

**Payload** `cycling.json → segments: [{name, km, gain_m, efforts: [{date, time_s, kmh, avg_hr, power}], pr, latest, median}]`

**UI** (Cycling, "How you ride"): one row per segment with a dot strip of all efforts over time, PR / latest / median, and
a "latest vs PR" comparison. Optional later: the commute sprint tracker could become a segment view.

**Tests** a synthetic track through a segment both ways (only the defined direction counts), a near-miss outside the
radius is ignored, a wrong turn is rejected.

**Size** M (≈ 1 day).

**Decision needed:** which 2–3 stretches? Run the suggest helper first, then pick.

---

## E. Strava archive import (prerequisite for F; back-fills A–D)

**How to get it:** strava.com → Settings → My Account → *Download or Delete Your Account* → **Request your archive**.
It arrives by email (can take hours) as a zip with `activities.csv` and `activities/` containing `.fit.gz`, `.gpx.gz` and `.tcx.gz`.

**Import**
- Drop the zip into `manual/strava_export/` (gitignored, read-only for the pipeline).
- `src/adapters/strava_archive.py`: read `activities.csv` for metadata (gear name → e-bike flag, type, commute flag) and
  parse FIT (fitparse, already used), GPX and TCX into the same stream columns as `load_activity_streams()`.
- **Dedupe** against Garmin: same start time within ±2 min **and** overlapping duration → keep Garmin for HR (strap), take
  power from the Sigma copy if Garmin has none. Never double-count load.
- Cache parsed streams in `build/cache/strava/` (parquet) so daily builds stay fast.
- This replaces the proposed `manual/fit_inbox/` (DATA_GAPS #6) for the Sigma rides. Update DATA_GAPS.md and README.
- Re-import: drop a newer archive in; the import is idempotent by Strava activity id.

**Tests** dedupe on a crafted pair; GPX without HR parses; gear-based e-bike detection.

**Size** M–L (≈ 1 day), mostly format edge cases.

**Check after import:** fitness/freshness, best efforts and segments all change once years of history arrive.
Re-check the eFTP and power-curve copy, since Sigma rides bring more power data.

---

## F. Personal heatmap (Strava "Personal Heatmaps"), after E

**Build**
- All outdoor rides (Garmin + Strava archive, deduped; e-bike included here, since it's about where you've been, with a toggle).
- Pre-render on the server side to keep the page light: project the points to Web Mercator, rasterise line segments into a
  density grid per zoom level, then log-scale the density into the seq ramp. Output PNG tiles or one large PNG plus bounds.
- **Privacy trim:** drop the first and last 200 m of every ride (home/work), even though the site is local. It costs nothing
  and makes screenshots safe to share.

**UI** (new "Map" section on Cycling, or its own page): a zoomable map, all rides by default, filters for year and ride
type (commute / long / e-bike). Ride-type colours follow DESIGN.md series colours. Works in light and dark.

**Decision needed:** basemap. (a) OSM raster tiles fetched live (needs network when viewing; fine for one personal user under OSM's
tile policy), (b) no basemap, just glowing lines on the page colour (fully offline and very clean; the city shape
appears from the rides themselves), (c) download a small offline tile set for the Munich area once. Recommendation: (b)
first, with (a) as an optional toggle.

**Size** M (≈ 1 day), plus a vendored Leaflet (~40 KB) if we go with zoomable tiles.

---

## Order and checkpoints

| Step | Feature | Size | Depends on |
|---|---|---|---|
| 1 | B. Weekly effort range | S | – |
| 2 | A. Matched commute | S–M | – |
| 3 | C. Best efforts | M | – |
| 4 | D. Segments | M | you pick the segments |
| 5 | E. Strava import | M–L | you request the archive (do it now, it takes a while) |
| 6 | F. Heatmap | M | E |

After each step: `make test`, `make site`, screenshots at 1440/390 in light and dark, a README section for the new panel,
and an `/impeccable critique` pass on the Cycling page after step 4.

## Status (2026-11-15)

All six steps built, tested and documented in README: B, A, C, D (three segments picked with `segments_suggest`), E (371 rides
added from the archive, 23 duplicates of Garmin rides, 7 without a file) and F (own Map page, client-side lines instead of
pre-rendered tiles). Still open: an `/impeccable critique` pass on the Cycling page (the plan asks for one after step 4).
Observed: no commute has a chest strap, so beats/km on the matched routes is empty for now.

## Decisions (2026-11-15)

1. Weekly effort range: **rides only** (consistent with the fitness chart).
2. Segments: **later**, picked after `segments_suggest`.
3. Heatmap basemap: **OpenStreetMap raster tiles**, fetched live (attribution "© OpenStreetMap contributors" on the map;
   respect the OSM tile usage policy: real User-Agent/Referer, browser cache, no prefetching).
4. Strava archive: **requested 2026-11-15**. Drop the zip into `manual/strava_export/` when it arrives.
