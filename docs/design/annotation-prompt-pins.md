# Design Note: Annotation Prompt-Pin Extension to Remaining Scopes

**Phase**: E5 (WikiLLM/OpenBrain Parity Plan, 2026-04-27)
**Status**: IMPLEMENTED — 2026-04-27 (plan: 2026-04-27-annotation-prompt-pins-v2)
**Scope covered by v1 (Phase E)**: `daily` narration only
**Scope covered by v2 (Phase E5)**: `topic`, `project_arc`, `weekly`, `monthly`

---

## What was built in Phase E

Phase E wires user annotations into the **daily narration prompt only**. When a
user saves an annotation for a date (via `POST /api/annotations` with
`scope='daily'`, `key='YYYY-MM-DD'`), the annotation is:

1. Stored in the `annotations` table (db.py).
2. Loaded in `narrate.py:_load_annotations_for_day()` during narration.
3. Included in the narration input hash so any edit/add/delete triggers
   automatic re-narration of that day's entry.
4. Injected into the narration system prompt as a `PINNED CORRECTIONS` block
   inside `narrator/claude_code.py:_build_narration_message()`, guarded by
   `if inp.scope == "daily"`.

The framing in the prompt is: "USER CORRECTIONS (ground truth — integrate these
naturally, never contradict, never ignore)." This contract is the trust
guarantee of the annotation surface.

---

## Why remaining scopes are deferred

Topic, arc, weekly, and monthly narration scopes each have additional
architectural considerations:

- **Token budget**: Weekly and monthly prompts already include substantial
  summarized prose from constituent days. Adding annotation blocks from multiple
  contributing days could push prompts over model context limits.
- **Cascading invalidation**: When daily annotations change, the weekly/monthly
  input hash must also change to trigger upstream regeneration. The existing
  hash cascade (brief → daily → weekly → monthly) needs a new "annotations"
  dimension.
- **Scope ambiguity**: An annotation on `scope='daily', key='2026-04-15'` is
  clear. But how does a correction to a daily entry propagate into a topic page
  that synthesizes across months? The right answer depends on whether the topic
  narration is regenerated from raw briefs (it is) or from daily narrations (it
  isn't). Topic pages re-read briefs directly, so daily annotations don't
  automatically apply — they'd need to be explicitly associated with the tag.

Proceeding phase-by-phase limits blast radius and lets the system stabilize
before wiring the more complex cascade.

---

## Extension design for each scope

### topic narration (`scope='topic'`, `key=<tag>`)

**Current flow** (arcs.py / topics.py): narration prompt is built from all
session briefs tagged with the topic, across all time.

**Pin integration**:
1. Load annotations where `target_scope='topic' AND target_key=<tag>`.
2. Append a PINNED CORRECTIONS block to the topic system prompt (see
   `narrator/claude_code.py:_build_narration_message` for the daily pattern).
3. Include annotations in the topic input hash (`topics.py` equivalent of
   `_narration_input_hash`). The topic hash already covers brief hashes; add
   `\x03annotations\x03 + _annotations_hash_contribution(annotations)`.
4. Add `annotations: list[dict]` to whatever input dataclass the topic narrator
   uses (or add an `annotations` kwarg to the build-message helper).

**Blast radius**: Narrow. Topic narrations are generated independently; no
cascade to daily/weekly/monthly.

### project arc narration (`scope='project_arc'`, `key=<project_id>`)

**Current flow** (arcs.py): similar to topic — reads all briefs for the project.

**Pin integration**: identical pattern to topic. Load annotations for
`target_scope='project_arc', target_key=<project_id>`. Add to arc input hash.

### weekly rollup (`scope='weekly'`, `key=<ISO-week>`)

**Current flow** (rollup.py): reads the daily narrations for the week's days as
inputs to the rollup prompt.

**Pin integration**:
1. Load annotations for all constituent daily dates (the days in the week).
2. Cascade: weekly input hash must include the annotation hash for each
   constituent day. If any daily annotation changes, the weekly hash changes.
3. Add a condensed PINNED CORRECTIONS block to the weekly prompt summarizing
   corrections from the week's days. Framing: "USER CORRECTIONS on constituent
   days — these override AI interpretations. Integrate naturally."
4. Optionally: load annotations on `scope='weekly', key=<ISO-week>` for
   corrections targeted specifically at the weekly rollup.

**Blast radius**: Medium. Weekly regeneration cascades into monthly.

### monthly rollup (`scope='monthly'`, `key=<YYYY-MM>`)

**Current flow** (monthly.py): reads weekly rollup narrations as inputs.

**Pin integration**:
1. Cascade from weekly: if weekly annotations change → weekly hash changes →
   monthly hash changes.
2. Load annotations on `scope='monthly', key=<YYYY-MM>` for corrections
   targeted specifically at the monthly rollup.
3. Add to monthly hash and monthly prompt.

**Blast radius**: Widest. Monthly regeneration is the top of the cascade.

---

## Implementation order for the follow-up plan

Recommended sequence to minimize risk:

1. **topic** — isolated, no cascade, highest value per annotation
2. **project_arc** — isolated, no cascade, same pattern as topic
3. **weekly** — introduces cascading; implement + test thoroughly
4. **monthly** — depends on weekly being stable

Each scope requires:
- `_load_annotations_for_scope(conn, scope, key)` (or extend existing helper)
- Hash contribution in the relevant `_narration_input_hash` equivalent
- PINNED CORRECTIONS block injection in the relevant prompt builder
- UI: "annotate" button on topic/arc/weekly/monthly pages (E3 pattern)

---

## Token budget guidance

For v1, include all annotations unconditionally. When token pressure becomes an
issue (journal with hundreds of annotations per scope), apply this strategy:

1. Include `pin_priority=2` (high) annotations always.
2. Include `pin_priority=1` (normal) annotations up to a character budget
   (suggested: 2000 chars total for the PINNED CORRECTIONS block).
3. Truncate older or lower-priority annotations first; always keep the most
   recently edited annotation.

The `pin_priority` column already exists in the schema; the logic above is a
future `_annotations_for_prompt(annotations, char_budget)` helper.

---

## Cross-cutting concern: annotation cascade in input hashes

The key insight for the weekly/monthly cascade is that the hash function
currently flows: `briefs → daily`. The new flow is:
`briefs + daily_annotations → daily` and
`daily + daily_annotations → weekly` and
`weekly + weekly_annotations → monthly`.

To implement this without refactoring the entire hash chain, add a
`daily_annotation_hash` field to the weekly input alongside the existing
`daily_prose_hashes`. The monthly hash already reads weekly hashes, so if
weekly's hash changes, monthly's hash changes automatically.

This is the same pattern as `docs_summary_hash_contribution` — a stable bytes
representation of the new input dimension, folded in with a scope delimiter byte.
