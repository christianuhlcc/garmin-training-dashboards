# Garmin data — agent guide

Personal Garmin Connect data (one user) pulled from the official **Garmin Connect Personal API**
into a local SQLite database. Use this file to understand what is where before querying.

## Files

| Path | What |
|---|---|
| `garmin.db` | SQLite database (WAL mode). Query with `sqlite3 garmin.db`. |
| `garmin_sync.py` | Fetcher: API → SQLite. Stdlib only, plus optional `fitdecode` for FIT parsing. |
| `sync.sh` | Wrapper: creates `.venv` with `fitdecode` if needed, then runs `garmin_sync.py`. |
| `weekly_review.py` | Once per completed ISO week: builds a brief (`garmin-dashboards/src/weekly_brief.py`), asks `claude -p` with the skill `.claude/skills/weekly-training-review` and stores the review in table `weekly_reviews` + `data/weekly_reviews/<week>.{md,json}`. Run with `garmin-dashboards/.venv/bin/python`. Sends the brief to Anthropic. |
| `notes_import.py` | Parses the hand-written strength log in Apple Notes ("Workout 2026") into `manual_*` tables. Called by the sync, or runnable on its own. |
| `deploy/` | Hosting on the shopmate EC2 instance: nginx snippet, setup, systemd job, Mac push, deploy + secrets scripts. See `deploy/README.md`. |
| `notes/<title>.txt` | Plain-text snapshot of each imported note (refreshed on every import). |
| `data/fit/<activity_id>.zip` | Original activity files (FIT inside a zip), as uploaded by the watch. |
| `.env` | `GARMIN_API_KEY=...` (secret, never print or commit it). |
| `.garmin_token` | Cached 24h bearer token (secret, auto-managed). |
| `garmin-dashboards/` | Local Cycling / Strength / Health dashboards (static HTML in `garmin-dashboards/build/site/index.html`, rebuilt by `daily.sh`). Reads `garmin.db` read-only, writes only its own `build/`. See its `README.md`, `DATA_INVENTORY.md`, `DATA_GAPS.md`. Derived metrics (TRIMP, CTL/ATL, zones, NP, e1RM, muscle sets, baselines) are queryable in `garmin-dashboards/build/metrics.duckdb`. |

## Refreshing data

```sh
./sync.sh                # incremental: (last synced day − 3 days) .. today, new activities, new FIT files
./sync.sh --days 14      # re-fetch the last 14 days
./sync.sh --full         # everything since the first day with data (~3.5k API calls, ~35 min)
./sync.sh --start 2026-01-01 --end 2026-01-31
./sync.sh --skip-fit     # don't download/parse FIT files
./sync.sh --reparse-fit  # re-parse every FIT zip already on disk (after changing parse_fit)
./sync.sh --skip-notes   # don't re-import the Apple Notes strength log
.venv/bin/python notes_import.py   # re-import only the notes (no Garmin API calls)
```

**Hosting (live since 2026-11-22, see `deploy/README.md`):** the dashboards run at https://logbook.uhl-steine-scherben.org
on the shopmate EC2 instance (password-protected). The **server owns `garmin.db`** and does the Garmin sync (systemd
timer 07:30/13:30/20:30 + uploads from the iPhone Shortcut / "Sync now" button / Mac). The local `garmin.db` is only a
copy: `deploy/mac/push.py` replaces it with the server's latest snapshot (local writes are lost), pushes the Apple Note,
and generates + uploads the weekly review. Don't run `./sync.sh` on the Mac any more (it would only write the copy).
Server log: `aws ssm start-session --target i-0fda569fb7ada49b5`, then `sudo journalctl -u garmin-sync`.

**Schedule (before 2026-11-22, Mac only; launchd job removed):** `daily.sh` (sync + dashboard rebuild) runs via launchd (`~/Library/LaunchAgents/com.christianuhl.garmin-sync.plist`)
daily at 08:30 *and* at every login/boot, because the Mac is often off for days and launchd drops calendar runs missed
while powered off. It waits up to 10 min for the network. Catch-up is automatic: an incremental run always covers
`last_window_end − 3 days .. today`, and `last_window_end` only advances after an error-free run, so a failed catch-up is
retried next time. Log: `logs/sync.log`. Gaps longer than ~4.5 months still lose intraday detail (see below).

**Intraday retention:** the Personal API serves intraday detail (2-min HR, HRV readings, body-battery curve,
SpO2 samples) only for roughly the last 4.5 months; older days return null arrays while daily values stay. In this
DB intraday data starts 2026-07-01. Run the sync regularly so new intraday data is saved before it expires.
Re-fetching old days never erases stored samples: they are replaced only when the API returns data, and
upserts never overwrite a stored value with NULL.

All writes are upserts, so re-running is always safe. Child tables (samples, records, sets) are replaced
per day / per activity. Today's data is partial until the day is over; the 3-day overlap makes the
next incremental run pick up the final values (sleep and HRV are finalized the following morning).
Every run is logged in `sync_runs` (window, API call count, errors).

## Conventions (read before writing queries)

- **Dates** `calendar_date` is `YYYY-MM-DD` text in the user's local calendar day. It is the join key across
  all daily tables.
- **Timestamps** are `YYYY-MM-DD HH:MM:SS` text. `*_gmt` = UTC, `*_local` = local wall-clock time.
  String comparison and `strftime()`/`date()` work on them directly. Local times come from Garmin's
  per-day offset (they follow travel, and switch at DST).
- **Sleep belongs to the morning it ends**: `sleep.calendar_date = 2026-11-13` is the night of 12→13 Nov.
  The same goes for `hrv` (overnight HRV) and `hrv_readings`.
- **Units** are SI unless the column name says otherwise: `_s` seconds, `_m` meters, `_mps` m/s, `_kg`, `_ml`,
  `_min` minutes, `_pct` percent. HR in bpm, power in W, cadence rpm (bike) / steps per min (run).
  `activities.avg_run_cadence` is steps/min; FIT `activity_records.cadence` for running is *per leg* (×2 for spm).
- **`raw` columns** hold the full original JSON for each row, so nothing the API returned is lost.
  Use `json_extract(raw, '$.path')` for fields that have no column of their own. Examples are below.
- Values are `NULL` when the device didn't measure something (no GPS indoors, no power without a meter, etc.).

## Tables

### Daily wellness (one row per `calendar_date`)

| Table | Content / notable columns |
|---|---|
| `daily_summary` | The main day record. Steps / step_goal / distance_m, floors, calories (total/active/resting), hr_min/max/resting, intensity minutes (moderate, vigorous, goal), stress avg/max + seconds per stress bucket (rest/low/medium/high/activity), body battery min/max/charged/drained/latest, respiration, SpO2, hydration. `raw` has extra fields such as body battery feedback events. |
| `sleep` | Sleep window (gmt + local), sleep_s, nap_s, deep/light/rem/awake seconds, awake_count, `score` (0-100) + qualifier, avg HR/SpO2/respiration/stress during sleep, `sleep_need_min`. `raw.sleepScores` has per-component qualifiers (deep %, REM %, restlessness...). |
| `hrv` | Overnight HRV: last_night_avg, last_night_5min_high, weekly_avg (ms), baseline band, `status` (BALANCED / UNBALANCED / LOW / POOR). |
| `heart_rate_daily` | min/max/resting HR + 7-day resting average (from the HR endpoint; mostly the same as daily_summary). |
| `spo2` | Daily SpO2 avg/lowest/latest, avg during sleep, 7-day avg. |
| `fitness_age` | chronological_age, fitness_age, achievable_fitness_age, components (rhr, vigorous days/minutes, bmi). `raw.components` has targets and priorities. |
| `food_logs` | Only days with logged food (`raw` = meals + foods). Often empty. |

### Intraday time series

| Table | Resolution | Key |
|---|---|---|
| `heart_rate_samples` | every 2 min, all day | `ts_gmt`. Has `calendar_date`, `ts_local`, `bpm`. |
| `body_battery_samples` | min+max per 15-min window | `ts_gmt`, `level` 0-100. |
| `body_battery_events` | per event | Sleep/activity/stress events with `impact` (+charge / −drain) and feedback text. |
| `hrv_readings` | every 5 min during sleep | `hrv_ms`. |
| `spo2_samples` | hourly averages (`kind='hourly'`), spot checks (`kind='single'`) | `(ts_gmt, kind)`. |

### Activities

| Table | Content |
|---|---|
| `activities` | One row per workout (`activity_id`). Name, `type_key` (e.g. `road_biking`, `strength_training`), `parent_type_key` (e.g. `cycling`), start times, duration/moving/elapsed, distance, calories, HR avg/max, speed, elevation, power (avg/max/normalized/20-min), cadence, sets/reps, training-effect labels, intensity minutes, body battery delta, start/end lat/lon, `location_name`, seconds in HR zones 1-5. `raw` = activity-list JSON (it also has `maxAvgPower_*` power curve, `powerTimeInZone_*`, `summarizedExerciseSets`, `splitSummaries`). `detail_raw` = activity-detail JSON (device, file format, `summaryDTO` incl. minHR). `workouts_raw` = linked planned workouts. `fit_path` = local FIT zip. |
| `activity_records` | Per-second(ish) samples from the FIT file: `ts_gmt`, `lat`/`lon` (degrees), `altitude_m`, `distance_m` (cumulative), `speed_mps`, `heart_rate`, `cadence`, `power`, `temperature_c`. Indexed by `(activity_id, ts_gmt)`. This is by far the largest table. |
| `activity_laps` | FIT laps: time, distance, HR, speed, power, cadence, ascent/descent, calories, trigger. |
| `activity_sets` | Strength-training sets from the FIT file: `set_type` (`active`/`rest`), `repetitions`, `weight_kg`, `duration_s`, `category`/`category_subtype` (Garmin FIT exercise-category IDs; NULL if the watch didn't recognize the exercise). |
| `activity_types` | `type_key` → `parent_key` hierarchy. |

### Body & fitness

| Table | Content |
|---|---|
| `vo2max` | VO2 max estimates by date. `kind` = `generic` (running/walking or overall) or `cycling`. Only dates when a new estimate was recorded. |
| `weight` | Weigh-ins: weight_kg, bmi, body fat/water %, bone/muscle mass kg, source_type. |
| `wellness_activities` | Health Snapshot style sessions: avg HR, stress, SpO2, RMSSD / SDRR HRV. |
| `heart_rate_zones` | Current HR zone floors (zone1..zone5), max HR, resting HR used, method. |
| `profile` | Locale, units, time zone (`raw` has all settings). |

### Manual strength log (Apple Notes) — **source of truth for strength training**

The user logs every gym session by hand in the Apple Note **"Workout 2026"**. For exercises, sets, reps
and weights this log beats Garmin's auto-detected sets (`activity_sets`, `activities.total_sets/total_reps`),
which are often wrong or unlabeled. Use Garmin only for the physiology of a session (HR, calories,
duration, body battery). The two are joined via `manual_workouts.garmin_activity_id`.

| Table | Content |
|---|---|
| `manual_workouts` | One row per dated note entry. `workout_id` = `YYYYMMDD*10 + n` (stable across re-imports). `title` = text after the date (`Workout A`, `müde`, `nach Erkältung`...). `comments` = non-exercise lines (mood, cardio like `42km rad`). `date_flag` is set when a date was auto-corrected. `garmin_activity_id` + `garmin_match` (`same_day` / `+1 day` / `-1 day`) link to `activities`. `raw_block` = the original text. |
| `manual_workout_activities` | Every Garmin activity belonging to a note session: `role='primary'` (= `garmin_activity_id`) plus `'joined'` ones. When the watch was stopped and restarted (two Garmin strength activities, one note entry that day), the leftover activities are joined to that entry instead of counting as unlogged. Only when exactly one note entry links to that day. `v_strength_sessions` and the dashboards sum duration/calories over all linked activities (`garmin_activities` = count). |
| `manual_sets` | One row per set. `exercise` = canonical name (e.g. `Bench Press`, `Overhead Press`, `DB Row`, `Pull-up`), `exercise_raw` = as written. `reps`; `weight_kg` = weight **per implement**; `implements` = 2 for a dumbbell pair (`2x6kg`), 1 otherwise, NULL for bodyweight; `load_kg` = weight_kg × implements. `to_failure`, `superset`, `skipped` (exercise written as `-`, one row with set_no 0). `parse_flag` marks interpretations worth double-checking. `line_raw` = original line. |

Notation rules in the note (as parsed): a date line `D.M` (no year, taken from the note title) starts a session,
and the newest session is at the top. `3x12 19kg` = 3 sets × 12 reps at 19 kg; `3x12 2x6kg` = with two 6 kg dumbbells;
`10/10/8/5 32,5kg` = reps per set; `6 6 6 5` = reps per set; `4x10 20/30/30/30 kg` = weight per set;
`2x10 1x7 32,5kg 1x8 30kg` = weights apply to the set groups before them; `(failure)` marks the last set;
`Pullups` without weight are bodyweight. Early entries (April/May) sometimes use `5x60` = 5 reps at 60 kg
(`parse_flag='reps_x_kg'`) or a bare trailing weight `3x5 75` (`weight_without_unit`).

Views:
- **`v_strength_sessions`**: each note session with sets/reps/volume/exercise list plus the linked Garmin activity's
  duration, HR, calories and Garmin's own (unreliable) set/rep counts.
- **`v_exercise_progress`**: per exercise per day: sets, reps per set, top weight, volume, Epley est. 1RM (per implement).

```sql
-- Bench press progression
SELECT workout_date, reps_per_set, top_weight_kg, est_1rm_kg FROM v_exercise_progress
WHERE exercise = 'Bench Press' ORDER BY workout_date;

-- Session volume vs Garmin HR/calories
SELECT workout_date, title, exercises, volume_kg, garmin_duration_min, garmin_avg_hr, garmin_calories
FROM v_strength_sessions ORDER BY workout_date DESC;

-- Pull-up total reps trend
SELECT workout_date, SUM(reps) FROM manual_sets WHERE exercise = 'Pull-up' GROUP BY 1 ORDER BY 1;
```

When the user says they did a workout, check the note-derived tables first. If a session looks wrong, compare
`manual_sets.line_raw` / `manual_workouts.raw_block` with `notes/workout_2026.txt`. Fixes belong in the
parser (`notes_import.py`), never in hand-edited rows, because every import replaces all rows of a note.

### Weekly reviews

`weekly_reviews` (week_iso PK, week_start/end, generated_at, model, cost_usd, skill_used, headline, review_json,
review_md, brief_json): AI review per ISO week. `review_json` follows the schema in `weekly_review.py` (headline,
summary, ratings per area, highlights, concerns, ≤3 recommendations, confidence). `brief_json` is the exact input.
To discuss a week interactively, use the `weekly-training-review` skill.

### Bookkeeping

`sync_state` (key/value: `last_window_end`, `first_data_date`, `last_sync_at`), `sync_runs` (one row per run),
`raw_documents` (endpoints with no table yet, e.g. `weight_goals`).

## Views (start here)

- **`v_daily`**: one row per day joining daily_summary + sleep + hrv + fitness_age + spo2 + activity counts.
  Columns: steps, resting HR, stress, body battery, `intensity_minutes` (moderate + 2×vigorous, Garmin's way),
  `sleep_h`, `sleep_score`, sleep stages, `hrv_last_night`, `hrv_status`, `fitness_age`, `n_activities`, `activity_min`.
- **`v_activities`**: activities with human units (`duration_min`, `distance_km`, `avg_kmh`).
- **`v_weekly`**: weekly aggregates of `v_daily` (week label `%Y-W%W`, Monday-based).

## Example queries

```sql
-- Last 14 days at a glance
SELECT calendar_date, steps, hr_resting, sleep_h, sleep_score, hrv_last_night, stress_avg, bb_max, n_activities
FROM v_daily ORDER BY calendar_date DESC LIMIT 14;

-- Monthly training volume by sport family
SELECT strftime('%Y-%m', calendar_date) AS month, parent_type_key,
       COUNT(*) AS n, ROUND(SUM(duration_s)/3600.0, 1) AS hours, ROUND(SUM(distance_m)/1000) AS km
FROM activities GROUP BY 1, 2 ORDER BY 1, 4 DESC;

-- Does a hard training day lower next-night HRV?
SELECT d.calendar_date, d.intensity_vigorous_min, n.hrv_last_night AS next_night_hrv
FROM v_daily d JOIN v_daily n ON n.calendar_date = date(d.calendar_date, '+1 day')
ORDER BY d.intensity_vigorous_min DESC LIMIT 20;

-- Cycling power curve bests (from raw activity JSON)
SELECT MAX(json_extract(raw, '$.maxAvgPower_60'))   AS best_1min_w,
       MAX(json_extract(raw, '$.maxAvgPower_300'))  AS best_5min_w,
       MAX(json_extract(raw, '$.maxAvgPower_1200')) AS best_20min_w
FROM activities WHERE parent_type_key = 'cycling';

-- Heart-rate trace of one activity, resampled to 1-minute buckets
SELECT substr(ts_gmt, 1, 16) AS minute, ROUND(AVG(heart_rate)) AS hr, ROUND(AVG(power)) AS w,
       ROUND(AVG(speed_mps) * 3.6, 1) AS kmh
FROM activity_records WHERE activity_id = ? GROUP BY minute ORDER BY minute;

-- GPS track (e.g. to export as GeoJSON or CSV)
SELECT ts_gmt, lat, lon, altitude_m FROM activity_records
WHERE activity_id = ? AND lat IS NOT NULL ORDER BY ts_gmt;

-- Strength training: total volume per session
SELECT a.calendar_date, a.name, COUNT(*) AS sets, SUM(s.repetitions) AS reps,
       ROUND(SUM(s.repetitions * s.weight_kg)) AS volume_kg
FROM activity_sets s JOIN activities a USING (activity_id)
WHERE s.set_type = 'active' GROUP BY s.activity_id ORDER BY a.calendar_date DESC;

-- Night-time heart rate (local clock)
SELECT calendar_date, MIN(bpm), ROUND(AVG(bpm), 1) FROM heart_rate_samples
WHERE time(ts_local) BETWEEN '01:00:00' AND '05:00:00' GROUP BY calendar_date;

-- Sleep score components from raw JSON
SELECT calendar_date, score,
       json_extract(raw, '$.sleepScores.deepPercentage.value') AS deep_pct,
       json_extract(raw, '$.sleepScores.remPercentage.value')  AS rem_pct,
       json_extract(raw, '$.sleepScores.restlessness.qualifierKey') AS restlessness
FROM sleep ORDER BY calendar_date DESC LIMIT 7;
```

## API notes (for changing the fetcher)

- Host: `https://personal.connectapi.garmin.com`, all GET, read-only. Token: `POST
  /personalapitoken-service/personal-api/personal-api-key/token` with `Authorization: ApiKey <key>`. The
  response body is the raw JWT (plain text), valid 24h, with no refresh token. Requests use `Authorization: Bearer <jwt>`.
- Reference client (endpoint list): https://github.com/garmin/connect-personal-api-python. Paths outside
  the documented set return 404 (no training readiness/status, devices, gear, or workout library).
- **Range limits** (found by probing; longer ranges return HTTP 400): steps & stress stats 28 days;
  sleep summaries, body battery, hydration 31 days; daily summaries ~365 days; HRV/VO2/weight/wellness ≥ 365.
- The FIT download needs `Accept: application/zip, application/x-zip-compressed, application/octet-stream`
  (anything else returns 406).
- Body battery takes `chunks=N` (min/max pairs per day); the fetcher uses 96.
- Not fetched on purpose: `stats/steps`, `stats/stress`, `stats/hydration` (subsets of `daily_summary`),
  per-day `dailySleep` (identical to the sleep summaries), menstrual-cycle endpoints (return 204 for this account).
- No published rate limits. The fetcher uses 4 parallel workers and retries 429/5xx with backoff.
