# Garmin dashboards

Three local dashboards, Cycling, Strength and Health, plus an overview, built from the Garmin data synced into
`../garmin.db` and the Apple Notes strength log. It's a static site: no server, no cloud and no network at runtime.
ECharts is vendored in `site/static/`, and the system font is used.

## Daily workflow

Nothing to do: `../daily.sh` runs at 08:30 and at every login (launchd). It syncs Garmin, re-imports the note, then
runs `make all` here. Open **`build/site/index.html`** in a browser (works from `file://`), or:

```sh
make all     # metrics + site (about 1 s with a warm cache, a few seconds after config changes)
make serve   # http://localhost:8765/index.html
```

| Target | What |
|---|---|
| `make inventory` | Coverage table of the synced data (`build/inventory.md`). |
| `make metrics` | `build/metrics.duckdb` + one payload per page (`build/data/*.json`, `build/site/data/*.js`). |
| `make site` | Renders `site/templates` + `site/static` into `build/site/`. |
| `make test` | Formula unit tests + reconciliation tests on the built payloads. |
| `make verify` | Last 5 rides and nights: dashboard value next to the raw value. |
| `make review-exercises` | Unmapped exercise names, raw spellings, lines the notes parser had to interpret. |
| `make screenshots` | Headless-Chrome screenshots, 1440 px and 390 px, light and dark (`build/screenshots/`). |

## Layout

```
config.yaml               athlete values, zones, thresholds (edit here; gitignored, start from config.example.yaml)
src/adapters/source.py    the only code that reads ../garmin.db (read-only) and ../data/fit
src/metrics/*.py          formulas (plan section 7), unit-tested in tests/test_metrics.py
src/exercise_map.yaml     exercise -> muscle regions (1.0 primary, 0.5 secondary)
src/build_metrics.py      adapters -> metrics -> DuckDB + payloads; per-ride stream metrics cached in build/cache
src/build_site.py         static pages
site/static/              app.js (shared runtime), <page>.js, style.css, body.svg, echarts.min.js
DATA_INVENTORY.md / DATA_GAPS.md   what the data contains and what's missing
```

## Weekly AI review

`../weekly_review.py` (run by `daily.sh`) writes one review per completed ISO week, only if none exists yet.
`src/weekly_brief.py` turns the week into a JSON brief in which every number is precomputed. `claude -p` evaluates it
with the project skill `../.claude/skills/weekly-training-review/SKILL.md` (rubric, athlete context, writing rules) and
returns structured output, enforced by a JSON schema. Results go to `garmin.db` → `weekly_reviews` and
`../data/weekly_reviews/<week>.{md,json}`, and appear on the Reviews page (the Overview links to the latest).
**This is the one step that leaves the machine:** the brief (aggregated week data + strength log) is sent to Anthropic.
Each review costs about $1 with the default model (capped at $3). Useful commands:

```sh
cd .. && garmin-dashboards/.venv/bin/python weekly_review.py --dry-run           # show the brief
cd .. && garmin-dashboards/.venv/bin/python weekly_review.py --week 2026-11-05 --force
```
To change how weeks are judged, edit the skill, not the code.

## Changing things

- **Heart-rate zones:** set `cycling.zones_hr.z1_upper_bpm` / `z2_upper_bpm` in `config.yaml`. They default to
  80 % / 88 % of `athlete.hr_max`, which overstate the hard band. Changing them invalidates the stream cache automatically.
- **FTP:** after a test, set `athlete.ftp_w`. eFTP stays visible as a labelled lower bound. Once `ftp_w` is set and
  ≥ 10 rides have power and HR, fitness load switches from TRIMP to TSS-equivalent (TRIMP rides scaled by a fitted factor).
- **Exercise mapping:** a new exercise in the note shows up in `make review-exercises` and as a warning in
  `make metrics`. Add it under `exercises:` in `src/exercise_map.yaml` using the canonical name the notes importer
  produces (`manual_sets.exercise`). Region keys must be ones from `regions:` (a test checks this against `body.svg`).
  If the importer names it badly, fix `canonical_exercise()` in `../notes_import.py`.
- **Strength log entries:** write them in the Apple Note "Workout 2026" as before (date line `14.11`, then one line
  per exercise like `Bench Press 4x10 52,5kg`, `DB Row 4x12 2x20kg`, `Pullup 7/7/5/4`). The next sync imports them.
  The plan's `manual/strength_log.csv` isn't needed because the note is the log. Notation rules are in `../AGENTS.md`.

## Decisions and deviations from the plan

- **Strength = path B:** sets come from the Apple Note, and Garmin's auto-detected sets are ignored (median 12 % rep
  error). Garmin-only sessions (another app, export pending) count as sessions without sets.
- **Intensity distribution** uses chest-strap rides only (174 of 194 rides have wrist HR).
- **eFTP** is labelled a lower bound: all power rides so far are endurance rides.
- **Commute sprints** (speed-based, experimental): the plan's rule (+10 km/h in 10 s from < 15 km/h) also fires on
  every pull-away from a traffic light (~16 per commute), so an extra condition was added: the effort must reach
  ≥ 30 km/h (`cycling.sprint.speed_peak_min_kmh`). The watch records about every 2–3 s on commutes; 1-s recording
  would make this reliable.
- **Fitbod history** (`../fitbod_import.py`, one-off): the Fitbod CSV export (35 sessions, 2025-05-18 to 2025-11-27) is stored
  in the same `manual_*` tables with `source = 'fitbod'`; re-running replaces only those rows and the Apple Note import
  never touches them. Fitbod names map to the canonical exercise names (`FITBOD_NAMES`), new ones were added to
  `src/exercise_map.yaml`. Warm-up sets, timed sets and burpees are kept but flagged `skipped` (not counted). The Strength
  page history now starts at the first logged set instead of the first Garmin day. A pre-import copy of `garmin.db` is in
  `../data/backups/`.
- **Strength progression** has an extra status, *restart*: the first session after 6+ weeks off, where a trend across the
  break would be meaningless.
- **Needs attention** (`src/metrics/attention.py`, tested in `tests/test_attention.py`): HRV/sleep/stress metrics
  outside the usual range (body signals), and training reminders (a split day missed for 8+ days, lifts flat or
  slipping, no rides or under half the recent volume, a long ride overdue after 21 days). The Overview shows one
  sentence that connects the two plus one line per area; the items themselves are listed at the top of each detail
  page. Thresholds sit at the top of the module.
- **Training load vs recovery** (Health) compares next-night HRV and sleep score after rest days with four load
  quarters of training days (`health.load_bins`), on fixed, unzoomed scales, instead of a scatter dominated by rest days.
- **Captions** are one plain-language line; the method sits in a collapsed "How it's computed" under it (`method:` in
  `D.panel`).
- **Page layout:** each detail page has 4 KPI tiles, grouped sections with a jump list, and a collapsed "More charts"
  section for specialist panels (grouping lives in the `D.arrange(...)` call at the end of each page script).
- **Last 12 weeks** (Overview, `build_weekly`): aligned weekly strips (training hours cycling + lifting, hard sets, HRV, resting HR, sleep) on one time axis with a shared crosshair; the Table button has the same weeks with rides, sessions and sleep score. It replaced the separate time-budget chart.
- **Weekly time budget** (cycling vs lifting hours, last 12 weeks) lives on the Overview, since it spans both areas; it was removed from the Cycling and Strength pages.
- **Cycling page layout** (2026-11-15 re-think, layout only): four sections by question (Form, How you train, Getting
  faster, Same routes) plus a collapsed archive (year over year, sprints). Related charts share one
  card ("host") with flat blocks inside; best efforts, power and segments use side-by-side rows; the section list sticks
  under the top bar and marks the section in view. The pieces are opt-in (`flat` panels, `D.arrange(..., {sticky: true})`,
  `.host/.blocks/.split` in `style.css`), so Overview, Strength and Health look exactly as before; they would need the
  same pass to match. The critique that led to it is in `.impeccable/critique/`.
- **Weekday pattern** uses dot plots on a zoomed axis: bars from zero would hide the small spread, and truncated
  bars would mislead.
- **KPI tiles** use fixed windows (last 7 / 28 days) with deltas vs the previous window; the date-range control
  affects the charts and tables.
- **Cycling analytics (build plan `PLAN_cycling_analytics.md`, steps 1–3):**
  - *Weekly effort range* (`cycling.effort_range`): weekly ride load against 75–125 % of the mean of the 3 completed weeks
    before it. Bars are neutral inside the band, blue below, orange above. The Overview gets one line only when a
    completed week is above 1.5× the upper bound or two completed weeks in a row are below the lower bound (a week without
    a range never triggers it; the "below" line is dropped when "no rides in 7 days" already says the same).
  - *Same route, over time* (`cycling.match_routes`): rides between the two most frequent endpoints, per direction, distance
    within ±10 % of the median, ≥ 6 rides. Moving time and a rolling median; heartbeats per km for chest-strap rides only.
    Today no commute was recorded with the strap, so that chart stays empty until one is. Names for the endpoints come from
    `cycling.commute_labels`.
  - *Best efforts* (`cycling.best_efforts`): fastest 1/5/10/20 km inside one outdoor ride, from the speed stream (windows
    over a gap > 10 s, a sample > 70 km/h or a GPS jump are dropped), plus the top 3 per distance and the PR progression.
    Stream metrics now carry `STREAM_VERSION`: bump it when a per-ride field is added so the cache is rebuilt.
  - `make`-free helper: `.venv/bin/python -m src.segments_suggest` prints the median commute track every 500 m (terminal only)
    for picking segment start/end points.
  - *Strava archive* (`src/adapters/strava_archive.py`, plan E): drop the export (extracted folder or zip) into
    `manual/strava_export/`; only `activities.csv` and `activities/` are read. FIT/GPX/TCX streams are parsed once into
    `build/cache/strava/` (idempotent by Strava id; delete a pickle to re-parse). Rides get `activity_id = 10^12 + Strava id`.
    Duplicates of Garmin rides (start ±2 min, overlapping) keep the Garmin copy. E-bike = Strava type or gear named in
    `cycling.ebike_names`. Strava files rarely say which sensor recorded HR, so those rides have HR source `unknown`
    (excluded from strap-only views); set `cycling.strava_hr_source: strap` if you know they were chest-strap rides.
    Resting HR before the first Garmin day is assumed constant (earliest value), so old TRIMP is approximate. Commute
    endpoints are taken from the Garmin era only. Fitness, volume and best efforts now start at the first ride (2013);
    year-over-year shows the last four years.
  - *Segments* (`cycling.segment_efforts`, `cycling.segments` in `config.yaml`): a pass within `radius_m` of the start, then
    of the end, in that direction only; no recording gap > 10 s; ridden distance within ±15 % of the segment's median.
    Efforts are computed per ride in the stream cache (changing the segments invalidates it automatically).
  - *Map* (own page, `site/static/map.js`, `build_map`): every outdoor ride as a translucent line over OpenStreetMap
    tiles, filters for year and ride type, basemap toggle. Deviation from the plan: lines are drawn in the browser from
    simplified polylines (8 m tolerance, about 0.9 MB) instead of pre-rendered density tiles; that keeps year/type
    filters trivial. The first and last 200 m of every ride are dropped before anything is written. OSM tiles need a
    network and a referrer, so open the page via `make serve` (`file://` sends no referrer and the tiles may be refused;
    the page says so and still draws the rides). Leaflet 1.9.4 is vendored in `site/static/leaflet/`.
- **Not built:** `manual/fit_inbox/` for Sigma rides (proposed in DATA_GAPS.md #6): replaced by the Strava archive import.
