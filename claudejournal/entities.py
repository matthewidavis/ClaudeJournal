"""Entity extraction from session briefs.

Pipeline stage [2e]: named-entity mentions (people, vendors, libraries, AI
models) are extracted from briefs via a haiku-model call and stored in the
`entities` / `brief_entities` tables.

Canonical name resolution is v1-simple: lowercase dedup.  "Claude" and
"claude" normalise to the same entity id; a proper merge/alias system is
deferred.

Designed to run incrementally: briefs whose brief_json hash is already in
brief_entities are skipped.  On first run this triggers a full backfill of
all historical briefs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

# ── Extraction prompt ─────────────────────────────────────────────────────────

ENTITY_EXTRACT_VERSION = "v2"

ENTITY_SYSTEM_PROMPT = """You extract named entities from software engineering journal briefs.
Return ONLY the JSON object — no prose, no markdown fences."""

ENTITY_EXTRACT_PROMPT = """\
Given the following journal brief fields, extract specific proper-noun entities
the author worked WITH or ON. Be conservative — when in doubt, omit. Quality
over quantity.

PRECISE TYPE DEFINITIONS:
- "person": a named human collaborator (e.g. "Matthew", "Sarah Chen"). NOT
  the author themselves implied by first-person narration. NOT generic roles.
- "library": a software library, framework, or specific dev tool with a
  proper product name (e.g. "React", "FastAPI", "Pillow", "pytest", "D3.js").
  EXCLUDE programming languages (JavaScript, Python, TypeScript, Go, Rust),
  EXCLUDE protocols / web standards (WebRTC, HTTP, WebSocket, OAuth),
  EXCLUDE generic categories ("database", "frontend", "compiler"),
  EXCLUDE plain nouns that happen to be capitalised mid-sentence.
- "ai_model": a specific machine-learning model or assistant by its product
  name (e.g. "Claude", "GPT-4", "Gemini Pro", "Llama 3", "Whisper"). NOT
  generic terms like "AI", "LLM", "the model", "an assistant". NOT general
  AI tooling like Claude Code or Cursor (those are services if at all).
- "service": a hosted product or cloud service that performs work for the
  user (e.g. "GitHub", "AWS S3", "Vercel", "Cloudflare", "Stripe", "PTZOptics
  cameras" if that's the vendor product). NOT category nouns like "cloud",
  "CDN", "the API".

HARD EXCLUSIONS — never extract these regardless of type:
{project_blocklist_block}- Programming languages of any kind.
- Web standards, protocols, file formats, encoding schemes.
- Generic technical nouns ("database", "server", "endpoint", "module",
  "config", "schema", "pipeline", "dashboard", "agent", "framework").
- Operating systems unless directly worked on (Linux, Windows, macOS are
  usually noise — only include if the brief is explicitly about an OS-level
  task with that OS as the subject).
- Browser names mentioned only as a target environment (Chrome, Firefox)
  unless directly worked on.
- The author's own first name if it appears (briefs are first-person).

Brief content:
GOAL: {goal}
DID: {did}
LEARNED: {learned}
FRICTION: {friction}
WINS: {wins}

Return a JSON object with a single key "entities" containing an array of
{{"name", "type"}} objects. The "type" must be exactly one of:
"person", "library", "ai_model", "service".

If no entities are clearly present, return {{"entities": []}}. An empty list
is a valid answer — do not invent entities to fill the response."""

ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["person", "library", "ai_model", "service"]},
                },
                "required": ["name", "type"],
            },
        }
    },
    "required": ["entities"],
}

# ── CLI helpers ───────────────────────────────────────────────────────────────

@contextmanager
def _no_session_leak() -> Generator[None, None, None]:
    """Prevent the claude CLI from writing a new session directory."""
    import os
    old = os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
    os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
        else:
            os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = old


def _call_extraction(text: str, binary: str = "claude", model: str = "haiku") -> list[dict]:
    """Call the claude CLI to extract entities from text.

    Returns list of {name, type} dicts.  Raises on CLI errors.
    """
    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", ENTITY_SYSTEM_PROMPT,
        "--json-schema", json.dumps(ENTITY_SCHEMA),
    ]
    try:
        with _no_session_leak():
            proc = subprocess.run(
                cmd, input=text, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI timed out (60s) during entity extraction")

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:300]}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"couldn't parse CLI envelope: {e}; stdout head: {proc.stdout[:300]}"
        )

    if envelope.get("is_error"):
        raise RuntimeError(f"CLI reported error: {envelope.get('result', '')[:300]}")

    result = envelope.get("structured_output")
    if not isinstance(result, dict):
        # Fallback: try parsing result text as JSON
        try:
            result = json.loads(envelope.get("result", "") or "{}")
        except json.JSONDecodeError:
            result = {}

    return result.get("entities") or []


# ── Core logic ────────────────────────────────────────────────────────────────

def _project_blocklist_block(project_names: list[str]) -> str:
    """Render the per-call HARD-EXCLUSIONS line listing the user's own
    project names. Empty string when no projects to exclude.

    Project names are passed as proper-noun forms (e.g. "ChromaKey",
    "ClaudeJournal"). The model will exclude any case-insensitive match.
    """
    if not project_names:
        return ""
    # Cap the list at 60 to keep the prompt sane; longer lists get
    # truncated with an ellipsis.
    pruned = sorted({p.strip() for p in project_names if p and p.strip()})[:60]
    if not pruned:
        return ""
    listing = ", ".join(f'"{p}"' for p in pruned)
    return (
        f"- The author's own project names — these are work artefacts, "
        f"not entities. Never extract: {listing}.\n"
    )


def _brief_text(brief: dict, project_names: list[str] | None = None) -> str:
    """Flatten the extractable fields of a brief dict into the prompt body.

    `project_names` is a list of the user's own project display names; they
    are inserted into the HARD EXCLUSIONS section so the extractor doesn't
    classify the author's own work as third-party libraries or AI models.
    """
    def _fmt_list(items) -> str:
        if not items:
            return ""
        if isinstance(items, list):
            return "; ".join(str(x) for x in items if x)
        return str(items)

    goal = brief.get("goal") or ""
    did = _fmt_list(brief.get("did"))
    learned = _fmt_list(brief.get("learned"))
    friction = _fmt_list(brief.get("friction"))
    wins = _fmt_list(brief.get("wins"))
    return ENTITY_EXTRACT_PROMPT.format(
        project_blocklist_block=_project_blocklist_block(project_names or []),
        goal=goal or "(none)",
        did=did or "(none)",
        learned=learned or "(none)",
        friction=friction or "(none)",
        wins=wins or "(none)",
    )


def _brief_hash(brief_json: str) -> str:
    """Stable hash of brief_json content for incremental skip."""
    h = hashlib.sha256()
    h.update(ENTITY_EXTRACT_VERSION.encode())
    h.update(brief_json.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _canonical(name: str) -> str:
    """V1 canonical name resolution: lowercase + strip whitespace."""
    return name.strip().lower()


def _upsert_entity(conn: sqlite3.Connection, name: str, etype: str, date: str) -> str:
    """Insert or merge entity; returns the entity id (canonical_name)."""
    eid = _canonical(name)
    existing = conn.execute("SELECT id FROM entities WHERE id = ?", (eid,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO entities (id, name, type, canonical_name, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, name.strip(), etype, eid, date),
        )
    return eid


# Hardcoded post-filter: programming languages, web standards, and generic
# nouns that v1 frequently mis-classified. The prompt now excludes these
# explicitly, but we double-filter to be robust against model drift.
_LANGUAGE_AND_STANDARD_NAMES = frozenset(s.lower() for s in [
    "javascript", "typescript", "python", "go", "rust", "java", "c++",
    "c#", "ruby", "php", "swift", "kotlin", "scala", "elixir", "dart",
    "html", "css", "sql", "bash", "shell", "powershell",
    "webrtc", "websocket", "websockets", "http", "https", "tcp", "udp",
    "rest", "graphql", "grpc", "json", "xml", "yaml", "toml", "csv",
    "oauth", "saml", "jwt",
])


def extract_entities(brief_json: str, session_id: str, date: str, *,
                     project_names: list[str] | None = None,
                     binary: str = "claude", model: str = "haiku",
                     verbose: bool = False) -> list[dict]:
    """Extract entities from one brief JSON string.

    `project_names`: list of the user's own project display names; passed
    to the prompt as a hard exclusion list AND used as a post-filter so
    even if the model ignores the prompt, the user's project names never
    end up classified as libraries or AI models.

    Returns list of {name, type} dicts (raw, before persistence).
    """
    try:
        brief = json.loads(brief_json)
    except json.JSONDecodeError:
        return []

    text = _brief_text(brief, project_names=project_names or [])
    try:
        entities = _call_extraction(text, binary=binary, model=model)
    except Exception as exc:
        if verbose:
            print(f"  ! entity extraction failed for {session_id}/{date}: {exc}")
        return []

    valid_types = {"person", "library", "ai_model", "service"}
    project_lc = {p.strip().lower() for p in (project_names or []) if p}

    out = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        etype = e.get("type")
        if not name or etype not in valid_types:
            continue
        nlc = name.strip().lower()
        # Drop the user's own project names regardless of how the model
        # classified them.
        if nlc in project_lc:
            if verbose:
                print(f"    skip {name!r}: matches user project")
            continue
        # Drop hardcoded languages/standards.
        if nlc in _LANGUAGE_AND_STANDARD_NAMES:
            if verbose:
                print(f"    skip {name!r}: language/standard")
            continue
        out.append(e)
    return out


def run(cfg,
        conn: sqlite3.Connection | None = None,
        *,
        force: bool = False,
        verbose: bool = False,
        progress=None,
        sanity_limit: int | None = None) -> dict:
    """Process all un-extracted briefs and persist entities.

    `sanity_limit`: if set, process at most this many briefs (for test runs).
    `force`: reprocess all briefs even if already extracted.

    Returns stats dict.
    """
    from claudejournal.db import connect as _connect

    _own_conn = conn is None
    if _own_conn:
        conn = _connect(cfg.db_path)

    def _tick(done: int, total: int, label: str = "") -> None:
        if progress:
            try:
                progress(done, total, label)
            except Exception:
                pass

    stats = {"processed": 0, "skipped": 0, "entities_added": 0, "errors": 0}

    try:
        # Load the user's own project display names once; they're the
        # single biggest source of v1 misclassifications (e.g. ChromaKey
        # extracted as an AI model). Passed to every brief-level
        # extraction call so the model excludes them, and used as a
        # post-filter for safety.
        project_names = [
            r["display_name"] for r in conn.execute(
                "SELECT display_name FROM projects WHERE display_name IS NOT NULL"
            ).fetchall()
        ]

        # Fetch all (session_id, date, brief_json) rows
        rows = conn.execute(
            "SELECT session_id, date, brief_json, project_id "
            "FROM session_briefs "
            "WHERE brief_json IS NOT NULL AND brief_json != '' "
            "ORDER BY date ASC"
        ).fetchall()

        total = len(rows)
        if sanity_limit is not None:
            rows = rows[:sanity_limit]
            if verbose:
                print(f"  [entities] sanity_limit={sanity_limit}; processing {len(rows)}/{total} briefs")
            total = len(rows)

        binary = getattr(cfg, "claude_binary", "claude")
        model = getattr(cfg, "entity_model", "haiku")

        for idx, row in enumerate(rows, 1):
            session_id = row["session_id"]
            date = row["date"]
            brief_json = row["brief_json"]

            _tick(idx, max(total, 1), f"{date} / {session_id[:8]}")

            bhash = _brief_hash(brief_json)

            if not force:
                # Check if this brief was already extracted at this hash
                already = conn.execute(
                    "SELECT 1 FROM brief_entities "
                    "WHERE session_id = ? AND date = ? AND brief_hash = ? LIMIT 1",
                    (session_id, date, bhash),
                ).fetchone()
                if already:
                    stats["skipped"] += 1
                    continue

            # Brief was updated or never extracted — remove old brief_entities rows
            conn.execute(
                "DELETE FROM brief_entities WHERE session_id = ? AND date = ?",
                (session_id, date),
            )

            entities = extract_entities(
                brief_json, session_id, date,
                project_names=project_names,
                binary=binary, model=model, verbose=verbose,
            )

            if verbose:
                label = f"{date}/{session_id[:8]}: {len(entities)} entities"
                print(f"  [entities] {label}")

            for ent in entities:
                name = ent["name"]
                etype = ent["type"]
                try:
                    eid = _upsert_entity(conn, name, etype, date)
                    conn.execute(
                        "INSERT OR REPLACE INTO brief_entities "
                        "(session_id, date, entity_id, brief_hash) VALUES (?, ?, ?, ?)",
                        (session_id, date, eid, bhash),
                    )
                    stats["entities_added"] += 1
                except Exception as exc:
                    if verbose:
                        print(f"    ! upsert failed for {name!r}: {exc}")
                    stats["errors"] += 1

            conn.commit()
            stats["processed"] += 1

            # Small rate-limit courtesy between calls
            if idx < total:
                time.sleep(0.1)

    finally:
        if _own_conn:
            conn.close()

    _tick(total, max(total, 1), "done")

    if verbose:
        print(
            f"  [entities] done: {stats['processed']} processed, "
            f"{stats['skipped']} skipped, "
            f"{stats['entities_added']} entity-rows added, "
            f"{stats['errors']} errors"
        )
    return stats


def get_entities_for_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Return all entities extracted from briefs on `date`.

    Returns [{id, name, type, canonical_name}] sorted by type then name.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT e.id, e.name, e.type, e.canonical_name
        FROM brief_entities be
        JOIN entities e ON e.id = be.entity_id
        WHERE be.date = ?
        ORDER BY e.type, e.name
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_entities_with_counts(conn: sqlite3.Connection) -> list[dict]:
    """Return all entities with day-counts, sorted by day-count descending.

    Used by render.py to build the entity filter option list.
    Returns [{id, name, type, canonical_name, day_count}].
    """
    rows = conn.execute(
        """
        SELECT e.id, e.name, e.type, e.canonical_name,
               COUNT(DISTINCT be.date) AS day_count
        FROM entities e
        JOIN brief_entities be ON be.entity_id = e.id
        GROUP BY e.id
        ORDER BY day_count DESC, e.name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]
