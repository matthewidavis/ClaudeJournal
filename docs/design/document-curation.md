# Document Curation — Design Note

**Status:** deferred design (not implemented)
**Drafted:** 2026-04-23
**Context:** conversation thread exploring how ClaudeJournal relates to Karpathy's Wiki / OpenBrain patterns

## The idea

Let the user add **external documents** (papers, articles, meeting notes, PDFs, markdown, plain text) to the journal as first-class citizens. Each document produces a **summary narration** that lives alongside the existing narrations (daily / project_day / weekly / monthly) as a fifth scope. Documents participate in the journal's existing write-time cascade rather than being a parallel system bolted on.

## Why this is worth doing

The journal currently records what you **did**. It has no way to record what you **read, watched, or referenced** — even though reading reshapes work. A paper you spent an hour with on Tuesday can influence Thursday's architectural decision, and the journal today has no hook to capture that connection.

Adding document curation makes the journal closer to a memoir of your intellectual life, not just your coding output, without breaking its core memoir voice. It also opens the corpus to RAG and MCP retrieval, so `/ask` can answer "what have I read about quantization?" with real citations.

## Conceptual shape — why this doesn't break the memoir

The key design insight: **documents produce a summary narration, not just a stored blob.** That summary is the retrievable, filterable, referenceable artifact. The summary is the bridge between "a PDF sitting in a folder" and "something the journal knows about."

Each document summary is its own narration row — its own URL, its own prose, its own audio file (pre-rendered WAV), its own filterable entry. It's **adjacent** to the journal's chronological spine, not part of it.

But documents are also **referenced** (not absorbed) by the existing time-scoped narrations:

- **Daily** sees a compact block: `DOCUMENTS ADDED TODAY` with title + your one-line note + a 2-sentence takeaway from the doc summary. Prompt rule: mention in first-person reading voice ("picked up X today and the thing that stuck was Y") — don't summarize the doc, riff on how it connected to the day's work.
- **Weekly** sees the week's docs and treats persistent-through-line material as a thread (analogous to how `threads.py` already works for multi-day projects).
- **Monthly** sees count + dominant themes — individual docs rarely named unless load-bearing.

At no layer does the narration become a book review. At every layer, the document is treated as a data point in your intellectual life, surfaced at the right grain.

## Why this is right-shaped for the system

Documents-as-briefs — same structural idiom as existing components:

| Scope | Unit | Voice | Inputs |
|---|---|---|---|
| project_day | one project on one day | first-person, tight | that project's briefs for that day |
| daily | one day | first-person, broad | all briefs for that day + **doc-added hints** |
| weekly | one week | retrospective | dailies + **week's docs (thread-eligible)** |
| monthly | one month | bigger-lens retrospective | weeklies + anchor dates + **month's reading themes** |
| **document (new)** | **one source** | **expository-but-personal** | **document content + user's note** |

The hash cascade extends naturally: a document summary's hash participates in the daily's input-hash; daily's hash cascades to weekly; weekly's to monthly. Adding a paper on April 22 invalidates Apr 22 daily → W17 weekly → April monthly in order, because each layer's inputs genuinely changed. **Same cascade pattern as Phase 1** — no new invalidation logic needed, just new input sources.

## User surface — the "Manage library" button

Mirror the pattern of the existing "Ask the journal" floating button. A new floating button opens a modal for document management:

- **Add** — drag/drop a file, choose project(s), tags, write a one-line note ("why I'm reading this"). System extracts text, generates a summary narration, adds to RAG.
- **Update** — re-edit the note, change project/tag assignments. (Changing the note triggers re-summarization + cascade.)
- **Remove** — delete the document. Cascade removes its contributions to daily/weekly/monthly hashes, triggering regen of any narrations that referenced it.
- **Browse** — list of all docs with filters: project, tag, date-added range, search within summaries.

Each list item links to the document's dedicated summary page (its own narration). A "Library" chip on Row 0 of the filter bar exposes documents in the main feed, alongside the Daily/Weekly/Monthly view chips.

### Why a modal, not a separate page

Consistent with "Ask the journal": the journal is the primary surface. Library management is a side task that pops up when needed, not a parallel product. Same floating-button idiom reinforces that.

## What the daily narrator would actually say

Example: you add "Burgers & Burgers — Quantization-aware depth heads" on April 22 with the note "might explain training-time explosion in ChromaKey."

- **Without curation (today)**: April 22 narration talks about the debugging session. No mention of reading.
- **With curation**: "...the quantization paper I picked up today helped crystallize why the depth-head loss was exploding — I'd been chasing it in the wrong layer. ChromaKey session ended with a cleaner plan."

That's the payoff: reading enters the journal as an event connected to the work, not a separate list.

## Scope

### Phase 1 — core pipeline (~1 day of work, similar size to Phase 1 hash cascade)

1. **Schema**: `documents` table (id, path, title, added_at, added_date, project_ids JSON, tags JSON, content_hash, extracted_text, user_note). New narration scope `document` (keyed by document id). Existing `narrations` table extends cleanly via scope field.
2. **Extraction**: PDF / markdown / txt / HTML → plain text. Use `pypdf` or similar. Original file preserved in `db/docs/<id>.<ext>`.
3. **Summarization**: new pipeline stage between scan and brief. Each new/changed document produces a summary narration via `claude -p`. Summary includes a short hook, a 2-sentence takeaway, and 3-5 key points. Hash over (extracted text + user note + prompt version).
4. **Hash cascade extension**: daily's `_narration_input_hash` adds docs-added-that-date as sorted `(doc_id, summary_hash)` tuples. Weekly includes docs for week. Monthly sees counts + theme tags. Patterns identical to Phase 1.
5. **RAG indexing**: document summaries (and optionally excerpts of extracted text) get chunked into `rag_chunks` alongside briefs and narrations. Retrievable by `/api/ask` and MCP.
6. **CLI**: `python -m claudejournal doc {add|update|remove|list}` — the first usable surface. UI layers on top.
7. **MCP tool**: `journal_docs(project="", tag="", limit=20)` returns document summaries.

### Phase 2 — UI and richer ingestion (~1-2 days)

1. **Library modal** — floating button, drag-drop add, list/filter/edit/remove.
2. **Project-page Library section** — lists docs attached to that project with date-added.
3. **Row 0 `Library` chip** — view-mode toggle exposing documents in the main feed (parallel to Daily/Weekly/Monthly).
4. **URL ingestion** — paste a URL, fetch + archive + summarize. Requires offline-copy storage (WARC or simplified).

### Explicitly out of scope

- **Reading progress tracking** ("I'm 40% through X"). The journal's value is in connections, not completion.
- **Highlight / annotation system**. Different product (Readwise, Obsidian).
- **Due dates / review queues**. Different product (Anki, spaced repetition apps).
- **Multi-user library / shared docs**. Journal is single-user by design.

## Honest concerns

### Prompt fragility at the daily layer

The daily narrator currently works from briefs only. Adding `DOCUMENTS ADDED TODAY` to the prompt means:

- Risk of over-emphasis: narrator starts summarizing papers instead of narrating the day.
- Risk of hallucinated connections: narrator invents links between the paper and the work that weren't real.

Mitigation: give the narrator the **user's note**, not the doc's extracted content. Prompt rule: "If the user didn't connect this document to the day's work in their note, mention the act of reading it but don't invent a connection." The note is the user's own framing — the narrator echoes it rather than synthesizing new claims.

### Multi-day readings

What if you read a paper over three days? Options:
- **Add once, mention once** — document is an event at its add-date. Subsequent days can mention it via briefs/tags (already covered).
- **Multi-day mention** — daily narrator sees "docs in progress" as a separate category. More state to manage.

Phase 1 should do **add once, mention once**. Multi-day is a Phase 3 question if the simpler pattern feels thin.

### Summary quality baseline

Documents vary wildly in length and density. A 40-page technical paper and a 2-paragraph blog post both produce "a summary." Consistency requires either:
- A good generic summarization prompt (high bar),
- Or document-type detection (heavy).

Phase 1: single generic prompt tuned for "extract 1-line hook + 2-sentence takeaway + 3-5 key points + suggested tags." If quality is uneven, iterate the prompt, not the architecture.

### Storage growth

PDFs can be MB each. `db/docs/` will grow. Already gitignored via `db/` pattern, so no repo pollution, but disk usage is real. Acceptable — users self-manage.

### Cascade expense

A document added to many projects invalidates every project's daily/weekly/monthly for the span. Could be expensive (many regens). Mitigation: user note typically scopes a doc to one project. Multi-project docs are rare and the user pays the cascade cost consciously.

## What this design deliberately avoids

- **Becoming a second brain.** The journal stays first-person-memoir. Documents enter as "things you engaged with," not as a knowledge graph to be queried.
- **Topical organization as primary.** Time stays the spine. Tags + projects are cross-cuts, as today.
- **Editing generated content.** Same rule as narrations: if something's wrong, fix the source (doc summary or user note) and regenerate. Never edit the compiled output.

## Relationship to OpenBrain / Karpathy's Wiki

This design lands us precisely in the hybrid shape Nate B Jones proposes in "Karpathy's Wiki vs. Open Brain":

> OpenBrain stays the single source of truth -- all information goes into SQL first. A compilation agent runs on schedule, reads from structured data, and generates wiki pages with cross-references, topic summaries, and contradiction flags. Wiki pages are never edited directly -- if something's wrong, fix the source data and regenerate.

Mapped to us:
- SQLite DB = single source of truth (already true)
- Pipeline (scan → brief → doc-summarize → narrate → rollup) = compilation agent (extended)
- Generated HTML pages = wiki layer (already true)
- Hash cascade = automatic "rebuild from ground truth" mechanism (already true, extends to docs)

The piece we don't have today — external material as a first-class input — is what document curation adds.

## Open questions to revisit if/when we build this

1. **Where do document summaries live in the feed?** A dedicated "Library" view, mixed into daily entries by date-added, or both (view chip toggles)?
2. **Audio for document summaries?** Piper synthesizes daily/weekly/monthly. Documents are a natural candidate — "listen to the takeaway on a walk" — but per-doc WAVs grow storage fast. Opt-in via config flag?
3. **Should document summaries have their own prompt version** (like `NARRATION_PROMPT_VERSION`)? Yes, for hash-cascade correctness when prompt evolves.
4. **How much extracted text goes into RAG?** Summary only, or summary + excerpts? Latter is richer but bloats the index. Probably summary + first N KB of extracted text.
5. **Update semantics** — if user edits the note, does the summary regenerate automatically? (Yes — note is part of the input hash.)
6. **Removal semantics** — if user deletes a doc, do narrations that referenced it regenerate without it, or keep the old prose? Cascade says regenerate. User expectation probably matches.

## Why this isn't being built now

- Not a pain point yet. The journal currently captures work; adding reading is additive, not corrective.
- Prompt tuning for the daily narrator needs care — "mention documents naturally without inventing connections" is a non-trivial prompt.
- UI surface (Manage library modal) is meaningful work, and the CLI-first phase would feel incomplete without it.
- The existing per-day brief refactor + hash cascade are young — better to let them settle and surface real issues before adding another input stream.

When the journal starts feeling too focused on coding activity and you want to capture reading as part of your intellectual timeline — that's the trigger. Until then, this doc preserves the design so we don't re-derive it next time.
