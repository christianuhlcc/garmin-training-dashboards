# Personal training data: sync, dashboards, weekly AI review

One person's fitness data in one place: Garmin watch data, a strength log kept by hand, and older rides from Strava.
It all goes into a local SQLite database, gets turned into derived metrics, and is shown as a static dashboard site.
Once a week Claude writes a short review of the week.

**Read this first: it won't run for you as-is.** Everything here was built around one person's setup: one Garmin
watch, a Garmin Connect *Personal API* key, a German-language Apple Note with a homemade notation for sets and reps,
a fixed training split, a commute route, and zones and thresholds taken from that person's own numbers. The data
itself (database, FIT files, notes, config, the real AI skill file) is gitignored and not part of this repo.

So use it as a **blueprint**. The parts worth keeping are the architecture, the data conventions, the metric
formulas and the dashboard design. The part you have to rebuild is the **ingestion layer** for your own sources.
The code was written with Claude Code, and the agent docs (`AGENTS.md`, the per-folder READMEs) are detailed on
purpose, so an agent can do most of that rebuild.

---

## What it does

```
 Garmin Personal API ──► garmin_sync.py ──┐
 Apple Note "Workout"  ──► notes_import.py ──┼──► garmin.db (SQLite)  ──► src/adapters/source.py (read-only)
 Fitbod CSV export   ──► fitbod_import.py ─┘                                │
 Strava archive (zip) ─────────────────────► src/adapters/strava_archive.py ┤
                                                                           ▼
                                             src/metrics/*.py  (TRIMP, CTL/ATL, zones, NP, eFTP, e1RM,
                                                                sets per muscle, baselines, readiness, alerts)
                                                                           │
                                             build_metrics.py ──► build/metrics.duckdb + JSON payloads
                                                                           │
                                             build_site.py    ──► build/site/  (static HTML + ECharts, no server)
                                                                           │
                                             weekly_brief.py ──► weekly_review.py ──► claude -p + skill
                                                                                       └► weekly_reviews table
```

`daily.sh` runs the whole chain once a day (via launchd on macOS, plus once at every login because the laptop is
often off): sync, then re-import the notes, rebuild the metrics and site, and write the weekly review if one is due.

| Layer | Files | Personal or reusable? |
|---|---|---|
| **Ingestion** | `garmin_sync.py`, `notes_import.py`, `fitbod_import.py`, `src/adapters/strava_archive.py` | **Personal.** Rewrite for your sources. |
| **Storage** | `garmin.db`: daily wellness, intraday series, activities, per-second FIT records, manual sets (schema in `AGENTS.md`) | Reusable as a model. Keep the conventions. |
| **Adapter seam** | `garmin-dashboards/src/adapters/source.py` | The one file you must adapt. It maps storage onto canonical DataFrames. |
| **Metrics** | `garmin-dashboards/src/metrics/*.py` + tests | Mostly reusable. They only see canonical columns. |
| **Site** | `build_site.py`, `site/templates`, `site/static` | Reusable. |
| **Weekly review** | `weekly_brief.py`, `weekly_review.py`, `.claude/skills/weekly-training-review/SKILL.example.md` | Reusable. Write your own athlete profile in the skill. |
| **Config** | `garmin-dashboards/config.example.yaml` → `config.yaml` | Fill in your own values. |

### The dashboards

- **Overview:** a readiness verdict for today (go / easy / rest, with the reasons), a "needs attention" list
  (HRV drift, a stalled lift, an overdue long ride, …), the last 12 weeks side by side, and a link to the latest
  weekly review.
- **Cycling:** fitness/fatigue (CTL/ATL from TRIMP, or TSS once FTP is set), intensity distribution, eFTP and power
  curve, best 1/5/10/20 km efforts, the same commute route over time, segments, and a map of every ride.
- **Strength:** progression per lift (Epley e1RM), weekly hard sets per muscle group on a body diagram, split
  adherence, and sessions joined with the watch's HR and calories.
- **Health:** sleep, HRV, resting HR, stress and body battery against a 60-day personal baseline, and next-night
  recovery split by training load.

It's a static site: it opens from `file://` with no server, no cloud and no network (the map tiles are the only
exception). The design principles are in `garmin-dashboards/PRODUCT.md` and `DESIGN.md`: calm by default, the
caveats shown next to the numbers, no gamification.

---

## Rebuilding it for your own data

### 1. Decide where your data comes from

| You have | Ingestion to build |
|---|---|
| Garmin, with Personal API access | Use `garmin_sync.py` almost unchanged. Put `GARMIN_API_KEY=...` in `.env`. |
| Garmin, without API access | Garmin Connect's account data export, or an unofficial client. Write the same tables. |
| Apple Health, Oura, Whoop, Polar, Coros, Wahoo, … | Their export or API → the daily, sleep, HRV and activity tables. |
| Activity files only (FIT/GPX/TCX) | Reuse the FIT parsing in `garmin_sync.py` (`parse_fit`) and the GPX/TCX parsing in `strava_archive.py`. |
| A strength app (Strong, Hevy, Fitbod, …) | Copy `fitbod_import.py`. It is the simplest example of "CSV → `manual_sets`". |
| A hand-written log | Copy `notes_import.py` and change the regexes to match your notation. |

You don't have to reproduce every table. Look at what `source.py` reads (`ACTIVITY_COLS`, `STREAM_COLS`,
`DAILY_COLS`, `SLEEP_COLS`, `HRV_COLS`, `WEIGHT_COLS`, `STRENGTH_COLS`) and fill what you can. Missing columns
come out as NULL, and the dashboards hide or caveat what they can't compute.

### 2. Keep the storage conventions

These are what make the rest of the code source-agnostic (details in `AGENTS.md`):

- `calendar_date` (`YYYY-MM-DD`, local day) is the join key for all daily tables.
- **Sleep, overnight HRV and readiness belong to the morning the night ends.**
- Timestamps are stored both as `*_gmt` and as `*_local`. SI units, with the unit in the column name
  (`_s`, `_m`, `_kg`, `_mps`).
- Every row keeps the original payload in a `raw` JSON column, so you never have to re-fetch to get a field.
- All writes are upserts, and a NULL never overwrites a stored value, so re-running a sync is always safe.
- A hand-kept log beats a device's auto-detection (Garmin's rep counting was off by about 12 %). Join the two by
  day and use the device only for physiology.

### 3. Adapt the seam, then the config

1. Point `config.yaml → source` at your database and change the SQL in `src/adapters/source.py` so every loader
   returns the canonical columns. Nothing else should touch your raw storage.
2. Copy `config.example.yaml` to `config.yaml` and set your HRmax, FTP, timezone, split, commute endpoints and
   segments (`python -m src.segments_suggest` helps pick segment points).
3. Map your exercise names to muscle regions in `src/exercise_map.yaml`. `make review-exercises` lists the
   unmapped ones.
4. Run the tests and the reconciliation check:

```sh
cd garmin-dashboards
make setup        # venv + pandas, duckdb, jinja2, fitdecode, pytest
make inventory    # what's in your data, and how complete it is
make all          # metrics + site → build/site/index.html
make test         # formula unit tests + payload reconciliation
make verify       # last 5 rides/nights: dashboard value next to the raw value
```

### 4. Optional: the weekly review

Requires the [Claude Code](https://claude.com/claude-code) CLI. Copy `SKILL.example.md` to `SKILL.md` in the
same folder (it's gitignored) and describe yourself: your goals, split, injuries, and what a good week looks like.
`weekly_review.py --dry-run` prints the brief without calling the model. **This is the only step that sends
data off the machine:** an aggregated weekly brief goes to Anthropic, at about $1 per week.

### 5. Optional: schedule it

`daily.sh` is the daily job. On macOS a launchd agent that runs it on a calendar time *and* at login catches up
after days with the laptop off. cron or a systemd timer work just as well.

### Letting an agent do it

Point Claude Code (or another coding agent) at this repo and your data. A prompt that works:

> Read `AGENTS.md` and `garmin-dashboards/README.md`. My data comes from *X* (export at `path/`). Write an
> importer that fills the same tables with the same conventions, then adapt `src/adapters/source.py` until
> `make all && make test` pass. Show me `make inventory` before and after.

---

## Privacy

The `.gitignore` is strict on purpose: databases, FIT/GPX/TCX files, exports, notes snapshots, logs, `config.yaml`,
the real skill file, build output (which embeds all the data) and every token stay local. Keep it that way in your
copy. Health data and GPS tracks show where you live and work. The map page also drops the first and last 200 m of
every ride.

## Requirements

Python 3.12, `fitdecode` for FIT parsing, `pandas`, `duckdb`, `jinja2` and `pyyaml` for the dashboards. The Apple
Notes import uses `osascript`, so it needs macOS. ECharts and Leaflet are vendored.
