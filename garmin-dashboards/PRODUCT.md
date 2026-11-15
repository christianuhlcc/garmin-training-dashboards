# Product

## Register

product

## Platform

web

## Users

One person: Christian, the athlete whose data this is. Nobody else reads it. He lifts (upper-body push / pull / shoulder split, legs restarting), rides (commutes, occasional long rides) and tracks recovery with a Garmin Venu 3. The dashboards are new, so the usual reading moment isn't settled yet; design for both a short glance on the phone and a longer look at a desk.

## Product Purpose

A local, static site built daily from his Garmin data and his Apple Notes strength log. Its main job is to help him **understand his own body**: how sleep, stress, recovery and training relate for him specifically. Secondary jobs: show progress over weeks and months (motivation) and support small training decisions (add a ride, hold a weight, take it easy). The Overview page's single job is to **spot problems early**: an HRV drift, a stalled lift, grey-zone creep, an overdue long ride.

## Positioning

One honest picture: Garmin, the strength log and the rides in one place, with the caveats shown, so he can see how sleep, stress and training connect for him.

## Brand Personality

Clean, precise, quiet. The voice is calm and gentle: when something is off it says so plainly and without alarm, and small dips are not treated as warnings. Figures are exact and come with their context (baseline, range, data source). The feel he wants to borrow is Strava's: clean, pretty and easy to read.

## Anti-references

- Gamified fitness apps: badges, streaks, confetti, "you crushed it!".
- Corporate BI dashboards: grids of KPI tiles, the generic SaaS look.
- Alarmist health apps: red warnings everywhere, anxiety-inducing.

## Design Principles

1. **Calm by default, clear when it matters.** Most days nothing is wrong, and the page should feel like that. A real problem is stated once, plainly, in words, not by turning the page red.
2. **Honest numbers.** Show the caveat next to the figure (wrist vs strap HR, eFTP as a lower bound, missing data). Never smooth over a gap or invent a value.
3. **Connections over isolated metrics.** Put sleep, stress, recovery and training side by side so relationships are visible; that understanding is the point.
4. **Easy to read at a glance, deep on demand.** The headline reading takes seconds; detail (tables, longer ranges) is one interaction away.
5. **Quiet craft.** Clean and pretty through restraint, spacing and type, not decoration.

## Accessibility & Inclusion

No specific requirements beyond sensible defaults: readable contrast (WCAG AA), keyboard-usable controls, and charts that don't rely on colour alone (already the case: legends, direct labels and table views). Respect `prefers-reduced-motion`.
