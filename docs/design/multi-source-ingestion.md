# Multi-source ingestion — design note (DEFERRED)

**Status:** Deferred — design captured for future implementation.
**Date:** 2026-04-30.
**Originating discussion:** end of session that shipped Cross-Project
Connections (Phases A/B/C + task 12) and the MCP cleanup pass.

---

## Premise

ClaudeJournal currently has exactly one ingestion source: Claude Code
session transcripts at `~/.claude/projects/<project>/<session>.jsonl`.
The rest of the system — RAG, briefs, narrations, topic pages, arcs,
entity extraction, connections, the entity profile pages, the MCP
layer — operates on **briefs**, the structured per-session summaries
produced by haiku extraction over those transcripts.

This means the brief schema, not the transcript format, is the real
contract. Everything downstream consumes briefs:

```
{ goal, did, learned, friction, wins, mood, tags, files }
```

Once we recognize that, **transcripts become an implementation detail
of one specific source**. Other surfaces (manual notes, git logs,
Claude Chat exports, eventually Slack / Gmail / calendar) can produce
the same brief shape from their own raw inputs, and ClaudeJournal
absorbs them with no changes downstream.

The user explicitly flagged this future direction during the session:
they want the option to monitor other file systems, apps, and possibly
services, while acknowledging services are a much further reach.

---

## Two possible paths

### Path A — Forge transcripts (rejected)

Generate fake `.jsonl` files in `~/.claude/projects/<source>/` that
look enough like Claude Code sessions for the existing scanner to
ingest them. **Pros:** zero downstream changes. **Cons:**

- We're now in the business of forging files in a format we don't
  control. Anthropic has changed the JSONL schema before and may
  again.
- Forged files could collide with real Claude Code sessions if naming
  isn't carefully namespaced.
- Synthetic event streams require fake timestamps, fake assistant
  turns, fake tool calls — a lot of detail that doesn't exist in the
  source data and pollutes downstream stages that key on those fields.
- Schema drift is a permanent surface area we'd own.

### Path B — Source plugins at the brief layer (recommended)

A new `sources/` package. Each source plugin produces structured items
that feed directly into the brief stage. The transcript format
becomes one source plugin among several. **Pros:**

- We own the contract. No schema-fragility risk.
- Sources are isolated — adding gmail doesn't risk breaking claude_code.
- Source identifiers namespace globally, no collision risk.
- Existing functionality unchanged on day one (Claude Code is just a
  source plugin like any other).

**Path B is the right answer.**

---

## Architectural sketch (Path B)

### `SourceItem` — the contract

```python
@dataclass
class SourceItem:
    source_kind: str       # 'claude_code', 'manual_note', 'git_commit', ...
    source_id: str         # globally namespaced, e.g. 'claude_code:abc-123'
    project_id: str        # which project does this belong to?
    occurred_at: str       # ISO timestamp
    raw_text: str          # the substance — fed to the brief generator
    metadata: dict         # source-specific context (e.g. file paths,
                           # commit hashes, message-ids); opaque to
                           # downstream stages
    inputs_signature: str  # stable hash for cache-skip semantics
                           # (matches existing sessions.inputs_signature
                           # pattern)
```

### Source plugin contract

```python
class Source:
    kind: str  # the source_kind string

    def discover(self, cfg: Config, conn) -> Iterator[SourceItem]:
        """Yield items found since last scan. Stateless from the
        plugin's perspective — incrementality is handled by the
        caller checking inputs_signature against the existing
        sessions table."""
```

A simple registry maps `source_kind → Source` instance. The pipeline
runs `discover()` for every registered source, dedups by `source_id`,
and feeds new items into the existing brief stage.

### Brief-stage refactor

`claudejournal/brief.py`'s `run()` currently iterates `(session_id,
date)` pairs from the `sessions` table. Refactor to iterate over
`SourceItem`s. The existing logic for hashing inputs, calling haiku,
storing brief_json into `session_briefs` stays the same — only the
input shape changes. Rename `session_id` to `source_id` throughout
for clarity (or alias one to the other for back-compat — the existing
schema treats `session_id` as a string already, no DB change needed).

### Per-source brief prompt variation

The current brief system prompt assumes the input is a Claude Code
session log. Different source kinds have different shapes — a git
commit's diff is structurally different from a journal note. The
prompt should accept a small "source kind hint" prefix:

```
This is a {source_kind_label} from {project_label}.
{kind-specific framing — 1-2 sentences}

Content:
{raw_text}
```

Where `kind-specific framing` is a tiny dict-lookup, e.g.:

- `claude_code` → "This is a Claude Code session log..."
- `manual_note` → "This is a journal note the user wrote directly..."
- `git_commit` → "This is a git commit's message and diff summary..."
- `claude_chat` → "This is a conversation from Claude.ai..."

Keep the brief schema identical. Only the framing prefix varies.

### Schema considerations

Minimal. Reuse `session_briefs` and `sessions` as-is. Add one new
column on `sessions` if we want explicit source-kind filtering:

```sql
ALTER TABLE sessions ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'claude_code';
```

The default keeps existing rows valid. Filter queries (e.g. "show me
git_commit-derived briefs only") become trivial.

---

## Phasing

### Phase 1 — `SourceItem` refactor (architecture-only, no new sources)

- Create `claudejournal/sources/` package.
- Move existing Claude Code scanner into `sources/claude_code.py`.
- Define `SourceItem` and the `Source` plugin contract.
- Refactor `brief.py` to consume `SourceItem`s.
- No user-visible change. Site renders identically. Just proves the
  contract works.

**Required for everything else.** Any new source plugin needs this
foundation.

### Phase 2 — Manual notes (simplest non-Claude-Code source)

- New CLI: `claudejournal note add --project X "..."` writes a
  `manual_note` SourceItem.
- Optional: scan a directory of markdown files (`~/journal/`,
  `~/Obsidian/`) and emit one SourceItem per file.
- First test of whether the framework works for non-transcript inputs.

**Highest ROI per unit effort once Phase 1 lands.** Anyone with
existing notes gets retroactive integration.

### Phase 3 — Git commit log

- Periodically scan repos under a configured root.
- Each new commit since last scan becomes a `git_commit` SourceItem
  with `(message + short diff stat + first-N-lines of changed files)`
  as `raw_text`.
- Project mapping: repo directory name (or user-mapped via config).
- Real work-signal that ClaudeJournal is currently blind to.

### Phase 4+ — Speculative

Each is its own design exercise.

- **Claude.ai chat exports.** Anthropic provides a JSON export.
  Parser + project-classifier plugin.
- **Filesystem watcher.** New/modified files in configured paths.
  Lots of noise; probably needs strong project-mapping heuristics
  and an exclusion list.
- **Email / calendar / Slack.** Each is its own auth dance, rate
  limit, and noise problem. Real value but high effort.
- **OS-level activity monitor.** Window focus, app usage. Privacy
  and noise concerns probably make this not worth it.

**Order matters.** Phase 1 unlocks everything; Phase 2 is essentially
free once Phase 1 lands; Phase 3 is the first time we test whether
the architecture survives contact with a meaningfully different source
shape.

---

## Open questions for the implementation pass

These don't need answers now — capturing them so the implementation
plan can address them.

1. **Project mapping for sources without an obvious project signal.**
   Manual notes can be tagged at write-time. Git commits map to repo
   names. But emails or filesystem files may have no clear project
   association. Heuristics? Manual mapping config? An `unsorted`
   project bucket?

2. **Source-specific deduplication.** Claude Code uses session_id +
   mtime. Git uses commit hash. Email uses message-id. Filesystem
   uses path + content hash. Each plugin owns its own dedup logic
   via the `inputs_signature` field — but the framework should make
   the contract explicit.

3. **Per-source rate limiting / cost containment.** A filesystem
   source could trigger hundreds of new briefs in one scan if a
   user dumps 500 markdown files into the watched directory.
   Per-source-kind caps? Confirmation prompts at large batches?

4. **Cross-source entity extraction.** Today entities are extracted
   per brief. As sources diversify, the same entity will appear
   across kinds (e.g., "FastAPI" mentioned in both a Claude Code
   session and a git commit). The existing canonical_name dedup
   handles this naturally, but the entity profile page might want
   to show source-kind breakdowns ("FastAPI: 8 Claude Code sessions,
   3 git commits, 2 manual notes").

5. **Transfer-recall and source-kind weighting.** Should a learning
   in a manual note carry more weight than one in a git commit? Less?
   Defaults vs. per-user config?

6. **MCP visibility.** Should the MCP tools gain a `source_kind`
   filter argument so an external agent can ask "what have I learned
   about FastAPI in *journal notes specifically*"? Plumbing is
   trivial; UX value depends on whether sources stay siloed in
   practice.

---

## Why this matters strategically

The journal already does cross-project knowledge transfer well. But
its corpus is "everything I did with Claude Code." That's a
substantial slice of work but not all of it. Once `SourceItem` is
the contract:

- Manual notes get retroactively integrated.
- Git history becomes a first-class learning source.
- Claude.ai conversations stop being a parallel undocumented surface.
- Future sources are pure additive work — no architectural surgery
  required per source.

Every existing surface — connections, entity profile pages, transfer
recall, the wiki-style topic pages, the daily entry chip row —
immediately benefits, because they all operate on briefs, not
transcripts.

This is a small but meaningful inversion of the system's mental
model. Worth the time to do it cleanly.

---

## Implementation effort estimate

Rough order-of-magnitude:

- **Phase 1:** half a day. Mostly mechanical refactor + tests.
- **Phase 2:** ~1 hour after Phase 1 ships. Trivial plugin.
- **Phase 3:** half a day. Real new code (libgit2 / subprocess to
  `git log`, diff parsing).
- **Phase 4+:** open-ended.

Total to "Phase 3 ships" is roughly 1.5 days of focused work.
