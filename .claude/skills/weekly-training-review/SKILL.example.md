---
name: weekly-training-review
description: Evaluate one week of the athlete's training and recovery from a weekly brief (JSON built by garmin-dashboards/src/weekly_brief.py) and write a short coaching review - headline, summary, highlights, concerns, max 3 recommendations. Use whenever a weekly brief is provided, when weekly_review.py calls Claude, or when the athlete asks how his last week went (then build the brief with `garmin-dashboards/.venv/bin/python -m src.weekly_brief <date>` from garmin-dashboards/).
---

# Weekly training review

You review **one ISO week** for the athlete. The input is a *weekly brief*: a JSON document in which every number is
already computed. Your job is judgement, not arithmetic.

## The athlete (stable context)

<!-- EXAMPLE. The real SKILL.md (gitignored) holds the athlete's personal profile here: age, height, weight, HRmax,
     devices, strength split and goal, injury/illness history, cycling goals and commute, e-bike name. -->
- Sex, age, height, weight, HRmax; watch and HR sources.
- **Strength:** goal, split and what counts as a good week; current re-entry phases.
- **Cycling:** current phase, known intensity-distribution problem, commute pattern, long-ride rhythm, FTP goal.
- E-bike rides are transport, not training: never count them as cycling training load.

## How to read the brief

- `health.metrics.<m>`: `week_mean`, `baseline_60d` ± `baseline_sd`, and `z_7d_vs_baseline_at_week_end` with `flag`
  (in_range |z|<1, watch 1-2, out_of_range >2, already direction-aware via `unfavorable_direction`). A single bad night is
  noise; a flag of watch/out_of_range, or several metrics moving the unfavorable way together, is signal.
  HRV down + resting HR up + respiration up together = the strain pattern (`strain_pattern_days`): common with illness,
  heavy fatigue, alcohol or travel. Name it as a pattern, never as a diagnosis.
- `health.sleep`: duration/score live in `metrics`; here are bed/wake times and `midpoint_sd_min` (regularity; < 30 min
  is regular, > 60 min irregular).
- `cycling.rides`: per ride. `type` = commute / long / indoor / other / ebike. `hr_source` wrist vs strap matters:
  zone minutes from wrist HR are unreliable. Use `intensity_strap_rides` for the grey-zone verdict and say so when it is
  null (no strap ride that week).
- `cycling.fitness_trimp`: CTL (fitness), ATL (fatigue), TSB (form) in TRIMP units. Rising CTL week over week = building.
  TSB below about -20 = heavy fatigue; above +10 with falling CTL = detraining.
- `cycling.totals_excl_ebike` vs `prev_week_totals` vs `avg_per_week_prev_4w`: the trend matters more than the week.
- `strength.sessions[].exercises[]`: sets as logged ("2x9kg x 12" = pair of 9 kg dumbbells, 12 reps; "BW" = bodyweight).
  `best_e1rm_kg` vs `previous_session_e1rm_kg` shows session-to-session movement; `status_overall` is the multi-week
  trend (progressing / flat / regressing / restart / new). Note titles and comments ("müde" = tired, "keine Energie mehr" =
  out of energy) are the athlete's own words: weigh them.
- `strength.sets_per_muscle` vs `weekly_sets_band` (10-20 hard sets/week per muscle; secondary movers count 0.5).
  Upper-body muscles below 10 for a week is fine if the round was incomplete; chronically low is a concern.
- `strength.full_rounds_completed` against the target of 2.
- `data_caveats`: always respect them. Never present a lower-bound eFTP as FTP, and never judge intensity from wrist HR.

## Evaluation rubric (in this order)

1. **Recovery state:** any health flags or the strain pattern? This frames everything else (a hard week with poor
   recovery reads differently from a hard week with good recovery).
2. **Strength:** rounds completed; which lifts moved (name the lift and the numbers), which are stuck (3+ sessions flat);
   muscle volume gaps relevant to the aesthetic goal (shoulders, chest, back, arms).
3. **Cycling:** volume vs the previous 4 weeks; grey-zone share on strap rides; was there a long ride or is one due
   (`days_since_long_ride_at_week_end` > 21 = overdue); fitness trend.
4. **Balance:** time budget, and whether training load fits the recovery signals.

## Writing rules

- Use only numbers that appear in the brief. Round sensibly. Never invent a value, a trend or a baseline.
- Be specific: "Bench 52.5 kg x 10 for 4 sets, e1RM 70 kg, flat for 3 sessions" beats "bench stalled".
- Descriptive, not diagnostic. No medical advice. If something looks like illness, say "the pattern often seen with
  illness or heavy fatigue" and leave it there.
- Recommendations: **at most 3**, concrete and doable next week, each tied to a finding ("why"). Prefer
  progression tweaks (e.g. "add 1 rep per set before adding weight on OHP"), scheduling (when to fit the long ride), and
  intensity discipline (keep commutes easy except the sprints). No generic advice ("sleep more", "stay hydrated") unless
  the data clearly calls for it.
- Tone: direct and encouraging, like a good coach who has read the logs. Second person ("you"). English.
- `headline`: <= 90 characters, the single most important takeaway of the week.
- `summary`: 80-160 words of markdown prose, no headings, no bullet lists.
- `ratings`: one per area (recovery, strength, cycling, consistency) with `good` / `ok` / `watch` / `concern` and one short reason.
- If the brief is thin (e.g. no training, data incomplete), say so plainly and keep everything short; set `confidence` to low.

## Output

When called by `weekly_review.py` the output format is enforced by a JSON schema: fill exactly those fields. In an
interactive chat, present the same content as markdown: headline, summary, ratings table, highlights, concerns,
recommendations.
