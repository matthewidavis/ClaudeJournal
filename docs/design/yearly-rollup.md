# Yearly rollup — design note (DEFERRED)

**Status:** Deferred — design captured for future implementation.
**Date:** 2026-05-04.
**Originating discussion:** session that shipped monthly v3 (gate +
prompt hardening for role drift) and the matching weekly v3.

---

## Premise

The chip widget already exposes a Year axis for filtering entries.
Long-term, a Year value should also surface a synthesised
retrospective the same way Project, Topic, Week, and Month already
do. This document captures the design before implementing — small
plan in itself, but the work waits until the corpus has data worth
synthesising against (today: 3 monthlies, threshold ~3+, just at
the edge).

---

## Why now (note) vs. now (code)

Today's corpus has Feb / Mar / Apr 2026 monthlies. May is in flight
(generated under v3, partial). A yearly retrospective generated
today would draw from 3-4 monthly inputs at most — thin material
for a year-shape retrospective.

When May settles and June lands, the corpus will cross "enough"
and shipping the feature has real visible payoff. Until then, code
without renderable output is a half-tested feature.

The work itself is a few hours; the documentation is the
load-bearing part to capture *now* while the design conversation is
fresh.

---

## Design decisions (resolved during the originating discussion)

### 1. Source material

Yearly synthesis takes **monthly rollups as primary input**, with
weekly rollups and daily narrations as a CITEABLE-DATES list (not
fed as prose) so the model can cite specific days without bloating
the prompt.

Rationale: 12 monthlies × ~500 words = ~6000 words of prose, well
within token budget. Weeklies + dailies as prose would push to
30k+ words and dilute the year-shape lens. Citation list keeps
specificity without the bulk.

### 2. Generation threshold (gate)

Skip yearly synthesis when fewer than **3 monthlies** exist for the
year. Below that, the existing monthlies are richer than any
synthesis would be, and the model has nothing to weave a year-arc
from.

This matches the structural pattern of monthly's `>=1 weekly OR
>=5 anchors`: at least one substantial layer of cascade input
must exist, not just raw daily anchors.

### 3. Length target

**600-1000 words.** A year is bigger than a month; the prose
deserves more space to thread arcs. Still reflective, not
exhaustive — err shorter when in doubt.

### 4. Regeneration semantics

Same as monthly: regenerate freely whenever the input hash changes.
Yearly's `input_hash` includes each monthly's `input_hash`, so
edits cascade up the chain. Editing a daily annotation eventually
re-renders the yearly automatically.

### 5. Schema

Reuse the existing `narrations` table. New scope value:
`scope='yearly'`, `key='2026'`. No schema migration required —
`scope` and `key` are already TEXT columns accepting any string.

### 6. Pipeline placement

New stage `[3e] yearly rollups` after `[3d] monthly rollups`.
Same `narrate_year()` / `run()` shape as monthly. Iteration:
yearly is fast (handful of years × one sonnet call each), runs
last in the cascade because it depends on monthlies being current.

### 7. Annotation prompt-pin compliance

Phase E v2 contract applies. New annotation `target_scope='yearly'`,
`target_key='2026'`. `format_pinned_corrections` injection identical
to topic / arc / weekly / monthly.

### 8. Render integration

**Standalone page at `out/yearly/2026.html`** following the monthly
pattern. Reachable via:

  - Direct URL.
  - The Year axis chip widget — when a Year value is selected,
    surface an "Overview" mode chip alongside the current "Timeline"
    that navigates to the yearly retrospective page (mirroring the
    Project + Topic Overview/Timeline fork).

The rendered page reuses `render_site_header` for the navigation
chip bar (consistency with weekly / monthly / topic / arc pages).

### 9. Hash cascade

`yearly._yearly_input_hash` includes:

  - `YEARLY_PROMPT_VERSION`
  - For each contributing monthly, sorted by year_month:
    `(year_month, monthly.input_hash)`
  - For annotations on this yearly target: the standard
    `_annotations_hash_contribution` from narrate.py

Editing any daily → daily.input_hash changes → its weekly's
input_hash changes → its monthly's input_hash changes → yearly's
input_hash changes → next render regenerates yearly. Cascade
propagation is automatic via the existing infrastructure.

### 10. Filter widget interaction

Today the Year axis chip filters entries by `data-year` attribute.
After this lands:

  - Year axis becomes one of the AXES_WITH_MODE set (currently
    `['project', 'topic']`).
  - "Overview" sub-chip on a year value navigates to
    `yearly/<year>.html`.
  - "Timeline" sub-chip behaves as today (filters the feed).
  - The chip widget's Overview-mode wiring already exists for
    project/topic; year just opts into the same code path.

### 11. Prompt hardening from day one

The yearly prompt should ship with the same hardened framing the
monthly v3 / weekly v3 prompts have:

  - Explicit role framing ("You are NOT an assistant…").
  - Sparse-input edge-case clause for the case where the gate
    lets too-thin input through (e.g., 3 monthlies but one is
    itself thin from being early-month).
  - "Year is still unfolding — N months on record so far" framing
    when source is below comfortable density.

`YEARLY_PROMPT_VERSION = "v1"` from the start, with the v3-
equivalent guards baked in. No subsequent rapid version churn.

---

## Implementation order (when picked up)

1. `claudejournal/yearly.py` — new module mirroring `monthly.py`'s
   structure: `narrate_year`, `_build_yearly_message`,
   `_yearly_input_hash`, `_has_enough_material`, `run` orchestrator,
   `years_with_activity` discovery helper.
2. `claudejournal/pipeline.py` — wire `[3e] yearly rollups` after
   `[3d] monthly rollups`.
3. `claudejournal/render.py` — load yearly narrations,
   `render_yearly_page` template, emit `out/yearly/<year>.html`.
4. `claudejournal/templates.py` — `render_yearly_page()` mirroring
   `render_month_break` / monthly standalone page templates;
   adjust the Year axis chip wiring to support Overview mode.
5. `claudejournal/cli.py` — optional `claudejournal yearly` CLI
   subcommand for manual regeneration (matching `claudejournal
   monthly`).
6. Annotation suppression: ensure render-time contradiction guard
   pre-pass extends to `target_scope='yearly'` (mirrors what Phase
   E v2 already does for topic/arc/weekly/monthly).

Estimated effort: 2-3 hours of focused implementation. Most of it
is mechanical mirroring of monthly.

---

## Open questions

1. **Cross-year boundary cases.** Yearly's input — monthlies — are
   strictly within-year, so no edge cases there. But a yearly
   anchor citing a [YYYY-MM-DD] from an adjacent year should be
   forbidden by the ALLOWED ANCHORS rule. Verify the prompt and
   anchor-list construction enforce this.

2. **Multi-year-projects.** A project that spans two years should
   probably appear in both yearly retrospectives. The connections
   model already handles cross-year project arcs via the existing
   `links` table; yearly prose is downstream of those. No special
   handling needed at the yearly synthesis layer — just write what
   the year contains.

3. **Display order.** Render order on the home feed: should yearly
   entries appear at all in the main feed (alongside weekly /
   monthly breaks), or only on the standalone page? Plain feed
   would be a "break" entry like weekly + monthly already are.
   I'd lean **standalone-only** for yearly — it's a destination,
   not a break. Worth confirming when implementing.

4. **Backlinks.** Should the yearly page render a "Referenced
   from" section? Probably yes for symmetry with topic / arc / doc
   pages. Cheap.

5. **Connections / cross-project signals.** Yearly is high enough
   level that the connections section may feel redundant — every
   project active in the year is "connected" to every other. Skip
   the connections section on yearly pages, OR show only the
   strongest cross-project signals (tier-2 textual similarity
   matches above some threshold). Decide at implementation time.

---

## What this isn't (non-goals)

- **Not a replacement for monthlies.** The monthlies remain
  primary; yearly is a higher-altitude lens above them.
- **Not real-time.** Yearly regenerates with the cascade but isn't
  meant to be checked on Jan 2nd. The first yearly is meaningful
  in February of the following year at the earliest.
- **Not a "year in review" report.** Just a continuation of the
  existing reflective-prose contract — first-person, past-tense,
  no headings, no enumeration.

---

## When to revisit

When at least 3 monthlies exist with substantive prose (Feb / Mar /
Apr 2026 already qualify). Implementation can ship at any time
after that; visible payoff scales with corpus density.

The simpler trigger: when you're already in the codebase touching
synthesis-layer code and want to fold this in alongside other work.
Mechanical mirror of monthly, low risk, high symbolic completion
value (the cascade goes daily → weekly → monthly → yearly cleanly).
