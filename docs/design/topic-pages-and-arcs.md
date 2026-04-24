# Topic Pages + Project Arc Pages -- Design Note

**Status:** planned (Phase 1 + Phase 2 scoped for implementation)
**Drafted:** 2026-04-24
**Context:** extending the journal with living wiki-style synthesis pages per tag and per project

## The idea

Add two new narration scopes that synthesize across the journal's existing data:

- **Topic pages** (scope `topic`, key = tag string): one wiki-style page per tag that qualifies. Reflects the user's evolving understanding of a subject across all projects and all time. Not a glossary entry -- a first-person synthesis.

- **Project arc pages** (scope `project_arc`, key = project_id): one retrospective page per project. Tells the story of a multi-day project -- its intent, its obstacles, its shifts, its current state. Replaces the current redirect stubs at `out/projects/<name>/index.html`.

Both are **living artifacts**: regenerated on every pipeline cycle when their inputs change, hash-gated so unchanged work is skipped.

## Why this is worth doing

The journal currently has two kinds of narration: chronological (daily, weekly, monthly) and per-source (document summaries). Both are anchored to time.

Missing: synthesis across time. "What have I come to understand about SQLite?" or "What's the story of the ChromaKey project?" require the user to mentally piece together entries spanning weeks or months. Topic and arc pages do that synthesis automatically, producing durable reference artifacts the user can return to.

This closes the loop between Karpathy's Wiki model (living topic pages compiled from structured data) and the journal's existing compile-from-ground-truth architecture.

## Cascade shape: parallel branch

```
briefs ------> daily ------> weekly ------> monthly
  |
  +----------> topic pages   (parallel, independent)
  |
  +----------> project_day narrations ---> project arc pages   (parallel, independent)
```

Topics and arcs consume data from the journal but do NOT feed back into the time-scoped chain. Daily prose stays as a historical artifact; topic/arc pages are living wiki artifacts. This is deliberate:

- **No upward invalidation**: changing a topic page does not invalidate any daily, weekly, or monthly narration. The time-scoped chain is a closed system.
- **No cycles**: topics read briefs; arcs read project_day narrations. Neither reads from the other. No fan-out explosion.
- **Independent prompt versioning**: `TOPIC_PROMPT_VERSION` and `ARC_PROMPT_VERSION` each control their own invalidation. Bumping the topic prompt re-generates all topic pages without touching anything else.

## Topic pages in detail

### Qualification threshold

A tag qualifies for a topic page only if it appears on **>=3 distinct days** across briefs. Day-distinctness, not brief-count -- a tag that appears 10 times on a single busy day doesn't qualify, but one that appears once per day across three separate days does. This filters burst tags (debugging spikes, one-off experiments) while keeping tags that represent genuine recurring themes.

Current data: 36 tags qualify at threshold 3.

### Input

All briefs whose `tags` JSON array contains the qualifying tag, sorted by date. The topic narration prompt receives a condensed view of these briefs: for each, the date, project name, goal, did items, learned items, and friction items. Tags are session-scoped, so one topic page may span many projects.

### Input hash

`sha256(TOPIC_PROMPT_VERSION + tag + sorted(session_id, input_hash) of contributing briefs)[:16]`

Changes when:
- A new brief lands with this tag
- An existing brief's input_hash changes (session grew, user edited)
- The prompt version is bumped

### Model

Haiku. Topics are numerous (36+) and the prompt is simpler than daily narration (no mood, no interleaved projects, no temporal threading). Haiku is sufficient quality-wise and keeps per-cycle cost to ~$0.10 for a full regen.

### Prompt voice

First-person reflective wiki. "What I've come to understand about X through my own work." Not a glossary, not a news article, not a diary. Synthesizes the user's language and framing from the briefs. References specific projects and moments where useful but doesn't retell chronologically. Length: 250-500 words.

### Output

Stored in `narrations` table with `scope='topic'`, `key=<tag>`. Rendered to `out/topics/<safe-slug>.html`. Indexed in RAG as `kind='topic'`. Pre-rendered to `out/audio/topic-<safe-slug>.wav`.

## Project arc pages in detail

### Input

All `project_day` narrations for the project, sorted by date. These are already condensed prose (150-350 words each, first-person, project-scoped). Arcs build on top of them rather than raw briefs -- this is more efficient and produces better synthesis because the project_day narrations already distill the day's work.

### Input hash

`sha256(ARC_PROMPT_VERSION + project_id + sorted(key, input_hash) of contributing project_day narrations)[:16]`

### Model

Sonnet. Arcs are fewer (~56 projects) and should feel more reflective, reading like a personal retrospective. The higher quality justifies the cost (~$1.50 for full regen).

### Prompt voice

First-person retrospective. Opening with intent, middle with obstacles and shifts, current state. Past tense for completed phases, present for ongoing. Length: 400-800 words. Does not restate daily content -- condenses it.

### Output

Stored in `narrations` table with `scope='project_arc'`, `key=<project_id>`. Rendered to `out/projects/<name>/index.html` (replacing the current redirect stub). Indexed in RAG as `kind='project_arc'`. Pre-rendered to `out/audio/arc-<project_id_hash>.wav`.

## UI surface: Overview / Timeline fork

When the user clicks the **Project** or **Topic** axis chip in the filter bar's Row 1, a sub-row appears with two meta-chips before the value list:

- **Overview** -- navigates to the synthesized wiki page
- **Timeline** -- filters the feed (today's behavior)

This fork only applies to Project and Topic axes. Other axes (Year, Month, Week, Mood, Aha moment) skip the mode step and go straight to value selection.

### State machine

```
state = { axis, mode, value, views }

axis = null        --> Row 1 shows axis chips only
axis = 'topic'     --> sub-row shows [Overview] [Timeline]
  mode = null      --> waiting for mode selection
  mode = 'overview'--> sub-row shows value list (only tags with pages)
    value = 'sqlite' --> navigate to topics/sqlite.html
  mode = 'timeline'--> sub-row shows value list (all tags)
    value = 'sqlite' --> filter feed to tag=sqlite (today's behavior)
axis = 'year'      --> sub-row shows value list directly (no mode step)
```

### URL encoding

`#axis=topic&mode=overview&value=sqlite`

Backward compatible: URLs without `mode` default to timeline behavior.

### Terminology

"Overview" and "Timeline" are the user-facing labels. "Wiki" and "brief" are internal terms. The mode chips use the same `.filter-chip.mode` CSS class as the Find/Daily/Weekly/Monthly chips in Row 0, keeping visual language consistent.

## Linkification of topic names

Same post-processing pattern as `link_doc_titles` in `post_process.py`. After HTML-escaping and anchor-linking, a new `link_topic_titles()` pass wraps any occurrence of a qualifying tag name in `<a class="topic-link" href="topics/<slug>.html">`. Word-boundary guards prevent false matches ("main" in "mainframe"). Longest tags first to prevent substring races.

This is a render-time transformation. The set of topic pages is NOT hashed into daily/weekly/monthly inputs. Adding a new topic page does not invalidate any time-scoped narration -- it just means the next render pass will linkify that topic name in prose that mentions it.

## Pipeline placement

```
[1/5]  scan
[2/5]  brief
[2b]   doc-summaries
[2c]   topic-summaries     <-- NEW (Phase 1)
[2d]   project-arcs        <-- NEW (Phase 2)
[3/5]  narrate (daily + project_day)
[3b]   interludes
[3c]   weekly rollups
[3d]   monthly rollups
[4/5]  index (RAG)
[5/5]  render
[6/6]  audio
```

Topics and arcs are numbered 2c/2d (after briefs, before narrate) because they are parallel branches that consume briefs/narrations but don't feed into the time-scoped chain. Placing them before narrate means topic pages are available for linkification during the render pass.

## Cost model

| Scope | Count | Model | Approx cost per full regen |
|---|---|---|---|
| Topic pages | 36 | haiku | ~$0.10 |
| Project arcs | 56 | sonnet | ~$1.50 |
| **Total first run** | | | **~$1.60** |
| Subsequent runs | only stale | mixed | $0.01-$0.30 typical |

Hash gating is the throttle. No per-cycle budget cap needed.

## Honest concerns

### Topic page quality variance

Some qualifying tags are natural topic-page subjects ("sqlite", "tts", "rag") while others are activity labels ("refactor", "debugging") that may produce thin or generic synthesis. The 3-day threshold filters the worst cases, but some tags above threshold will still produce mediocre pages. Acceptable for Phase 1 -- the user evaluates quality and can raise the threshold if needed.

### Arc input length

Projects with months of activity accumulate dozens of project_day narrations. Concatenating them all into a single prompt may exceed model context or produce unfocused arcs. Mitigation: start simple (concat all), and only add input truncation or chunking if quality suffers. The 14K char limit used by doc summaries is a reasonable ceiling to adopt if needed.

### Prompt iteration

The topic and arc prompts are new voice modes. The daily narrator has been tuned over four prompt versions. Expect the topic/arc prompts to need at least one revision. TOPIC_PROMPT_VERSION and ARC_PROMPT_VERSION exist for exactly this purpose -- bumping the version cleanly invalidates all pages.

### Safe slug collisions

Tag names like "ml/ops" and "ml-ops" could produce the same filesystem slug. At 36 tags this is unlikely but the slug derivation should include a collision check with disambiguation (hash suffix or counter).

## What this design deliberately avoids

- **Feeding topic/arc pages back into daily narrations.** The time-scoped chain is a historical record. Topic/arc pages are living synthesis. Mixing them would create circular invalidation and blur the boundary between "what happened" and "what I think now."

- **Editable topic/arc pages.** Same rule as all narrations: if something's wrong, fix the source (briefs, tags, project_day narrations) and regenerate. Never edit the compiled output.

- **Topic pages for non-qualifying tags.** Tags below the 3-day threshold don't get pages. No "generate on demand" button. The threshold is the quality gate.

- **Project arc pages that incorporate weekly/monthly rollups.** Arcs build from project_day narrations only. Weekly and monthly narrations are cross-project synthesis; mixing them in would dilute the project-specific narrative.

## Phase 3: On-demand summarize (deferred)

A "summarize current view" button visible when any filter is active. Clicking it would call Claude CLI with the currently-visible entries as context and render the response inline below the filter bar. This is query-time synthesis, not write-time -- a fundamentally different architectural axis. No DB persistence, no cascade, no audio pre-render.

Why deferred: the write-time topic/arc pages cover the most common "summarize by topic/project" use cases. On-demand summarize is for ad-hoc queries ("summarize everything from March where mood was frustrated") which are lower priority and harder to get right (prompt needs to handle arbitrary filter combinations). Design separately when the need arises.

## Relationship to existing design notes

This design extends the cascade architecture documented in `docs/design/document-curation.md`. Where document curation added a new input stream (external documents) that flows into the existing time-scoped cascade, topic/arc pages add new output streams (synthesis pages) that branch off the cascade without feeding back in. Same ground-truth-to-compiled-artifact philosophy, same hash-gated regeneration, same never-edit-output rule.
