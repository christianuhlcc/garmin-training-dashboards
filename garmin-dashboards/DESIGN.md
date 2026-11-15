---
name: Logbook (Garmin dashboards)
description: A personal training logbook. Recovery, strength and cycling on clean, quiet pages.
colors:
  page: "#f9f9f7"
  surface: "#fcfcfb"
  surface-raised: "#f3f2ee"
  ink: "#0b0b0b"
  ink-secondary: "#52514e"
  ink-muted: "#6f6d68"
  rule: "#e1e0d9"
  axis: "#c3c2b7"
  hairline: "#0b0b0b1a"
  series-blue: "#2a78d6"
  series-orange: "#eb6834"
  series-green: "#1baf7a"
  series-amber: "#eda100"
  series-pink: "#e87ba4"
  series-forest: "#008300"
  series-violet: "#6250d6"
  series-red: "#e34948"
  neutral-mark: "#b9b8b0"
  seq-0: "#ecebe6"
  seq-1: "#cde2fb"
  seq-2: "#9ec5f4"
  seq-3: "#6da7ec"
  seq-4: "#3987e5"
  seq-5: "#256abf"
  seq-6: "#184f95"
  status-good: "#0ca30c"
  status-warning: "#fab219"
  status-serious: "#ec835a"
  status-critical: "#d03b3b"
  text-good: "#006300"
  text-bad: "#a32626"
  focus: "#2a78d6"
  dark-page: "#0d0d0d"
  dark-surface: "#1a1a19"
  dark-surface-raised: "#232321"
  dark-ink: "#f0efec"
  dark-ink-secondary: "#c3c2b7"
  dark-rule: "#2c2c2a"
  dark-ink-muted: "#93918b"
  dark-axis: "#383835"
  dark-series-blue: "#3987e5"
  dark-series-orange: "#d95926"
  dark-series-green: "#199e70"
  dark-series-amber: "#c98500"
  dark-series-pink: "#d55181"
  dark-series-violet: "#9085e9"
  dark-series-red: "#e66767"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.25
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.3
  figure:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.15
    fontFeature: "tnum"
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  caption:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.3
  chart-label:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.3
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
components:
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "14px 16px 12px"
  kpi-tile:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "12px 14px"
  segmented-button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.md}"
    padding: "5px 10px"
  segmented-button-selected:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
  nav-link:
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.sm}"
    padding: "6px 10px"
  nav-link-current:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
  status-chip:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
  panel-tool-button:
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: Garmin dashboards

## 1. Overview

**Creative North Star: "The Training Logbook"**

These pages are a well-kept personal log, not a control room. Imagine a good notebook: ruled lines, exact figures written neatly, and a note in the margin wherever a number needs a caveat ("wrist HR", "lower bound", "2 nights missing"). Ink on paper does almost all the work. Colour appears only where it carries meaning: a series in a chart, a status dot, a change that went the right or wrong way. On an ordinary day the page reads calm, because on an ordinary day nothing is wrong.

The system is a product UI and is allowed to be dense. Tables run long, charts sit two to a row on desktop, and the type is one system sans in a few sizes and weights. What keeps the density quiet is consistency: every panel has the same anatomy (title, one-line caption, chart or table, a "Table" toggle), every number is tabular, and every caveat sits next to the figure it qualifies. The data-viz palette is the validated reference palette (colour-blind safe in light and dark), and it is used only for data.

This system rejects three things by name, taken from PRODUCT.md: **gamified fitness apps** (badges, streaks, confetti, "you crushed it!"), **corporate BI dashboards** (grids of KPI tiles, the generic SaaS look) and **alarmist health apps** (red warnings everywhere). The reference for feel is Strava: clean, pretty, easy to read.

Stacking uses named levels (`--z-sticky` for the top bar, `--z-tooltip` for tooltips). Layout: one centred column up to 1440px with 20px side gutters (16px on phones). Panels sit in a two-column grid that drops to one column below 1000px. The top bar is sticky on desktop and scrolls away on phones. Motion is limited to state changes (hover, toggles) of about 150ms. Charts don't animate in.

**Key Characteristics:**
- Ink-first, near-neutral surfaces. Colour is used only for data and status.
- One system sans. Hierarchy comes from size and weight, never from a second family.
- Flat surfaces with hairline borders, like ruled lines.
- Caveats sit in the margin: small muted notes next to the figure, never hidden.
- Soft, friendly controls: pill chips, gently rounded segmented buttons.
- Light and dark are both designed, with their own validated steps rather than an automatic inversion.

## 2. Colors

A near-neutral, very slightly warm grey "paper" with near-black ink. All saturation is reserved for the chart series and the four status hues.

### Primary
- **Logbook Blue** (series-blue): the first series in every chart, link colour, focus ring, and the selected state on the muscle map. It is the system's only interface accent, and even there it marks focus and links only, never decoration.

### Secondary
- **Ember Orange** (series-orange): the second series and the "over target" marker (`--over`), e.g. grey-zone minutes or overtrained regions.

### Tertiary
- **Data series 3–8** (green, amber, pink, forest, violet, red): categorical chart series, always assigned in this fixed order and never cycled. A ninth series folds into "Other".
- **Sequential blue ramp** (seq-0 → seq-6): magnitude only (muscle-map heat, calendar intensity). One hue, light to dark.

### Neutral
- **Page Paper** (page): the page background behind panels.
- **Sheet** (surface): panels, tiles, table headers and tooltips. It is barely lighter than the page; the hairline border does the separating.
- **Raised Sheet** (surface-raised): the selected or hover state for nav links, segmented buttons and chips, plus neutral body regions on the muscle map.
- **Ink** (ink): headings, values and body text.
- **Pencil** (ink-secondary): captions, labels, deltas and inactive controls (7.7:1 on Sheet).
- **Margin Note** (ink-muted): freshness stamps, footnotes and the "small" notes. It is for short annotations only, never body copy. It is 4.9:1 on Sheet and 4.6:1 on Raised Sheet (dark step #93918b: 5.0:1 or better), so it passes AA even at 11px.
- **Ruled Line** (rule) and **Axis** (axis): table row lines and chart grid; chart axes and the hover border on cards.
- **Hairline** (hairline, 10% ink): every panel, tile, chip and control border.

### Status
- **Good / Warning / Serious / Critical** (status-*): only as the small dot inside a status chip, always paired with an icon character and a word label. **Text Good / Text Bad** (text-good, text-bad) are the darker text-safe steps for coloured deltas.

Dark mode swaps the neutrals for dark-page / dark-surface / dark-surface-raised / dark-ink / dark-ink-secondary / dark-rule and uses the dark steps of each series from `style.css`. These were validated separately against the dark surface. The theme follows the system setting unless the Auto / Dark / Light button overrides it.

### Named Rules
**The Ink-First Rule.** Text is always ink, pencil or margin note, never a series colour. A coloured mark beside the text carries the identity.

**The Data-Only Saturation Rule.** Saturated colour is forbidden on surfaces, headings, buttons and decoration. If a colour isn't encoding a series, a magnitude or a status, it shouldn't be there.

**The Quiet Status Rule.** A status is a dot plus a word ("● HRV: in range"), never a filled red banner, a red panel or red body text. "Watch" is amber, not red; only "out of range" and "regressing" earn the critical dot.

## 3. Typography

**Body Font:** system-ui (with -apple-system, Segoe UI, sans-serif)

**Character:** one native sans in a tight scale (about 1.15 ratio between steps), so the pages feel like part of the operating system. Weight carries the hierarchy, and tabular figures keep columns of numbers aligned.

### Hierarchy
- **Headline** (700, 22px, 1.25): the page title only ("Cycling", "Strength").
- **Title** (700, 15px; 17px on Overview cards): panel and card headings. Wrap with `text-wrap: balance`.
- **Figure** (600, 26px, 1.15, tabular; 22px on phones; 24px on Overview cards): the headline number of a tile or card. The unit follows in 13px pencil at weight 400.
- **Body** (400, 14px, 1.45): sentences, the weekly review prose, the question under each page title. Keep prose within 75ch.
- **Caption** (400, 12.5px, pencil): the one-line explanation under each panel title, and table cells.
- **Label** (600, 12px): chips, table headers and control text. Sentence case. Never uppercase-tracked.
- **Margin note** (400, 11.5–12px, ink-muted): freshness, footnotes, caveats.
- **Chart label** (400, 11px): axis ticks, axis names, heatmap cell values. The floor for any text.

### Named Rules
**The One Family Rule.** No second typeface, no display font, no webfont download. The system sans does everything, and it keeps the site offline and instant.

**The Tabular Figures Rule.** Every number that can sit above or beside another number uses `font-variant-numeric: tabular-nums`.

## 4. Elevation

Flat. Depth comes from a hairline border (1px, 10% ink) and a barely lighter sheet on the page, the way ruled paper separates entries. Panels and tiles never cast shadows. The only shadow belongs to things that genuinely float above the page: chart and muscle-map tooltips.

### Shadow Vocabulary
- **Floating tooltip** (`box-shadow: 0 4px 16px rgba(0,0,0,.12)`): HTML tooltips (muscle map, sparkline hovers) and ECharts tooltips. Nothing else.

### Named Rules
**The Ruled-Paper Rule.** Surfaces are separated by hairlines and whitespace, never by shadow. A panel with a drop shadow is a bug.

## 5. Components

Soft and friendly, but quiet: generous rounding and pill shapes, gentle raised-sheet tints for selected states, no hard colour fills.

### Buttons
- **Shape:** gently rounded (8px) for segmented and theme buttons; 6px for the small panel tool buttons ("Table").
- **Segmented control** (date range): buttons share one hairline-bordered pill-ish group (8px), separated by inner hairlines. Unselected is pencil text on sheet; selected (`aria-pressed="true"`) is ink text at weight 600 on raised sheet.
- **Theme button:** same shape as the segmented buttons, standalone, labelled "Auto" / "Dark" / "Light".
- **Panel tool button:** transparent, hairline border, 12px pencil text; pressed state uses raised sheet.
- **Focus:** a 2px Logbook Blue outline offset by 2px on every button, link and focusable region. Hover shifts nav links to raised sheet.

### Chips
- **Style:** a soft pill (999px radius) tinted by its status: background is the status hue mixed 14% into Sheet (17% in dark mode), border 32%, then an 8px dot in the full status hue and 12px label text that stays ink. The weekly review chips also carry an icon character (✓ · ! ▲).
- **State:** the dot colour carries the status (in range or progressing = good, watch or flat = warning, restart = blue, ok = margin-note grey, out of range or regressing = critical). The word is always present. In forced-colours mode the dot keeps its colour.

### Cards / Containers
- **Corner Style:** softly rounded (12px).
- **Background:** sheet on page paper.
- **Shadow Strategy:** none (see Elevation).
- **Border:** hairline.
- **Internal Padding:** 14px 16px 12px for panels (12px on phones), 12px 14px for KPI tiles, 12px 16px for Overview area rows.
- **Panel anatomy (fixed):** title row with tools aligned right, a one-line plain-language caption in pencil, then a collapsed "How it's computed" disclosure (12px, margin-note grey) holding the method, then the chart (220 / 300 / 380px tall) or table, then an optional margin note. Every chart panel has a Table toggle.

### Inputs / Fields
- **Select** (weekly review week picker): native `<select>` with inherited font. Don't restyle it beyond that.

### Navigation
- **Top bar:** the "Logbook" wordmark in 600 weight, then the page links (Overview, Cycling, Strength, Health, Reviews), then a spacer, the date-range control and the theme button (labelled "Theme Auto / Dark / Light", its title says what the next click does). Links are pencil text with 6px rounded hover and current states on raised sheet; the current page is ink at weight 600 and marked `aria-current="page"`. Sticky on desktop with a hairline bottom border; it scrolls away on phones and wraps freely.

### Data table
- 12.5px text, 5px by 8px cells, ruled-line row separators, a sticky pencil header on sheet, right-aligned tabular numbers, and dimmed rows in margin-note grey. Inside a scroll container capped at 420px.

### Muscle map (signature)
- A front-and-back SVG body (inlined) whose regions fill from the sequential blue ramp by weekly hard sets, with Ember Orange for over the target band. Regions have a 2px sheet-coloured stroke as the gap between shapes; hover or selection adds an ink stroke. A ranked list beside it mirrors the map (hover or click one, the other highlights).

### Overview synthesis (signature)
- The Overview answers in one sentence (19px, 500 weight, max 58ch) that connects body and training, e.g. "HRV is below and resting HR above your usual range during a lighter training week, so training load is unlikely to be the reason." Below it, one link row per area (Body, Strength, Cycling): name and status chip on the left, a 500-weight line saying what is flagged (or that nothing is), and a pencil line with two plain numbers. Only a Body row that is off is tinted; training rows stay untinted with a neutral "Note" chip. Then a margin note naming the time windows and a link to last week's review. Sentence and rows are computed in the build (`src/metrics/attention.py`), never in the browser.

### Needs-attention list (detail pages)
- First thing under the question on each detail page: a heading that counts ("2 things to look at"), then one link row per flag (9px dot, 600-weight title, pencil detail line with the numbers, ↓ to the panel that explains it). Body signals are a soft tint of their status (9% background, 26% border, 10px radius); training reminders (`note`) are untinted, the dot alone carries them. With nothing flagged it collapses to one line: an "On track" chip and "Nothing flagged here right now."

### Page sections and jump list
- Detail pages group panels under quiet sentence-case section headings (17px, 700) with a one-line pencil intro, after a row of pill-shaped jump links. Specialist charts sit in a collapsed **More charts** section (a full-width hairline summary row listing what's inside); following a link to a panel inside it opens it. Panel titles are h3 under the section h2.

### Weekly review (own page)
- Lives on the Reviews page, not the Overview: it describes a finished calendar week and would contradict the live state next to it. A panel with a compact week selector ("W45 · 2–8 Nov"), a caption naming the week and when it was written, a bold headline sentence, the rating chips each with their reason as visible text (never only in a tooltip), the summary prose, highlights and concerns side by side, a numbered "Next week" list, and a margin note on provenance (model, date, what was sent to Anthropic).

## 6. Do's and Don'ts

### Do:
- **Do** keep every surface flat with hairline borders (1px, 10% ink) and 12px corners on panels and tiles.
- **Do** put the caveat next to the number, in margin-note grey ("eFTP ≥ … W (lower bound)", "wrist HR").
- **Do** show status as a dot, an icon character and a word in a softly tinted pill chip (14% tint, never a solid fill). Use colour plus text, never colour alone.
- **Do** use the series colours in their fixed order and keep each entity's colour stable across filters.
- **Do** give every chart a legend (two or more series), a Table toggle, and a hover tooltip.
- **Do** design dark mode with its own validated steps, and test both themes at 1440px and 390px.
- **Do** keep motion to roughly 150ms state transitions, and honour `prefers-reduced-motion` (style.css collapses all transitions).
- **Do** keep tile rows to 4. Extra metrics go into one inline line of chips, not more tiles.
- **Do** colour a change only when it matters (tiles: 20 % or more of the previous value; lift deltas: 5 % of e1RM), and never for a restart or a new lift.
- **Do** let chart legends wrap (the plot moves down to make room); never page them.
- **Do** keep chart text at 11px or larger, and pick heatmap label colour per cell (ink or white, whichever contrasts).

### Don't:
- **Don't** look like a **gamified fitness app**: no badges, streaks, confetti, trophies or "you crushed it!" copy.
- **Don't** look like a **corporate BI dashboard**: no wall of identical KPI tiles, no gradient hero numbers, no generic SaaS chrome. Tiles are allowed only as a short row (at most 4–5) that leads into charts.
- **Don't** look like an **alarmist health app**: no red banners, no red panels, no red body text, no warning icons for a value that's only "watch".
- **Don't** add drop shadows to panels, tiles or cards; only floating tooltips have a shadow.
- **Don't** use a second typeface, webfonts or uppercase tracked eyebrows above sections.
- **Don't** use `border-left` or `border-right` wider than 1px as a coloured accent stripe.
- **Don't** colour text with a series colour, or use status colours for anything but status.
- **Don't** use dual-axis charts; put two measures in two charts.
