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

- **OpenCode (with or without Oh My OpenAgent).** Verified during
  a 2026-04-30 inspection of a real OpenCode review session. See
  the dedicated section below — schema is documented, persistence
  is local SQLite + sibling JSON files, parts have a richer event
  taxonomy than Claude Code (text / reasoning / tool / step-start /
  step-finish). This is a Phase 4 candidate ranked HIGH because it
  multiplies the corpus across agents, not just sources, and the
  framing is "ClaudeJournal absorbs Claude Code AND OpenCode AND…"
  which is a real strategic differentiator.
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
- **Phase 4+:** open-ended. OpenCode source plugin specifically
  is roughly half a day given the schema verification below.

---

## Appendix: OpenCode source plugin — verified schema

Inspection on 2026-04-30 against a real OpenCode review session
(`ses_221755532ffeihHMNV3mt0jzR7`, "Code review and improvement
suggestions" run against the allThePictures-main codebase, 25
messages and 120 parts).

### Persistence locations

- **`~/.local/share/opencode/opencode.db`** — SQLite, Drizzle-managed
  (has `__drizzle_migrations` table; OpenCode versions its schema
  explicitly).
- **`~/.local/share/opencode/storage/session_diff/<session_id>.json`**
  — per-session aggregate file-diff JSON. Note: in the inspected
  session this was empty `[]` despite real file edits, so we can't
  rely on it as a shortcut. Diffs need to be derived from `tool` parts.
- **`~/.config/opencode/`** — per-user config (Bun project structure
  with `package.json`, `node_modules`). Doesn't carry session data
  but is where Oh My OpenAgent customizations live.

### Relevant DB tables

```
session   — id, project_id, slug, directory, title, version,
            time_created/updated, summary_files/additions/deletions
            (note: summary_* may be 0 even with real edits;
             session-level diff aggregate is unreliable)
message   — id, session_id, time_created, data (JSON: role/time/
            agent/model + per-message cost+token stats for assistant)
part      — id, message_id, session_id, time_created, data (JSON
            tagged-union by `type` field — see below)
project   — id, worktree, vcs, name, time_created/updated
            (project mapping is automatic via project.worktree +
             session.directory)
```

### `part.data` type taxonomy (verified)

| Type | Purpose | Notes for the brief stage |
|---|---|---|
| `text` | User and assistant text turns | Maps to user_prompt events and assistant message body |
| `reasoning` | Assistant chain-of-thought (private) | Novel — no analogue in Claude Code transcripts. Decision: strip by default to keep brief shapes consistent across sources; expose as a config knob |
| `tool` | Tool calls with `state.input` and `state.output` | Maps to tool_use events. File-edit tool calls are how we'd derive `files` for the brief; bash output is corpus material |
| `step-start` | Marker for the start of an assistant action step | Mostly structural; can be ignored by the brief generator |
| `step-finish` | Marker for end of step, carries cost + token stats | Useful for per-session cost rollups (already a thing in ClaudeJournal's brief-cost tracking) |

Real distribution from one ~25-message review session: 47 tool, 18
text, 18 step-start, 18 reasoning, 17 step-finish. Tool-call density
is comparable to Claude Code sessions of similar length.

### Project-mapping strategy

`project.worktree` + `session.directory` is enough. For a session
where `session.directory` is `C:\Users\Matt\Downloads\allThePictures-main - Copy`,
ClaudeJournal would map this to a project_id using the same path-→-id
mangling the Claude Code scanner already does (`_project_folder_name`
in `narrator/claude_code.py`). No new mapping layer needed.

### Strategic value

The user explicitly raised this during the 2026-04-30 design
discussion: "if a lot of my coworkers are using OpenCode with Oh My
OpenAgent, does that have a known pattern we would be able to use to
treat it this same way?" Yes — the schema above proves it.

The pitch becomes:

> *ClaudeJournal supports any agent that writes structured session
> logs. Today: Claude Code. Coming: OpenCode. Plug it in and the
> journal builds the same cross-project knowledge surface from your
> work, regardless of which agent you used.*

Cross-team value: someone using Claude Code and someone using OpenCode
can share a journal, see each other's entity profiles, get connection
nudges that span tools. No second-brain tool I know of currently does
this.

### Open implementation questions for OpenCode specifically

1. **`reasoning` part inclusion in raw_text.** Default off (consistent
   with Claude Code) or default on (richer briefs)? Prefer config knob
   with default off, document the tradeoff.
2. **Tool-output truncation.** OpenCode `tool` parts include full
   stdout/stderr. Long outputs (e.g. `ls -la` of a large directory)
   bloat the brief input. Same caps the Claude Code scanner uses
   should apply.
3. **Diff derivation.** Since `session_diff` is unreliable, derive
   file changes from `tool.state` of edit/write/bash-with-redirect
   calls. Reuse `files_touched` event-extraction logic where possible.
4. **Schema-version check.** `__drizzle_migrations` lets us assert a
   known schema version on plugin load. Fail loudly on unknown
   migrations rather than silently producing bad briefs.
5. **Concurrent access.** OpenCode keeps the DB open with WAL mode.
   Read-only access during scan is safe (concurrent readers are
   fine), but the source plugin should not write to OpenCode's DB
   under any circumstances. Treat it as a black-box read-only
   source — never use Drizzle's session migrations or write
   compaction even if "helpful."

Total to "Phase 3 ships" is roughly 1.5 days of focused work.
