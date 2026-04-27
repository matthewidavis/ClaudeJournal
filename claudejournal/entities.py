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

ENTITY_EXTRACT_VERSION = "v1"

ENTITY_SYSTEM_PROMPT = """You extract named entities from software engineering journal briefs.
Return ONLY the JSON object — no prose, no markdown fences."""

ENTITY_EXTRACT_PROMPT = """\
Given the following journal brief fields, extract ALL named entities that are
specific proper nouns: people, software libraries/frameworks, AI models, and
vendor/cloud services. Do NOT include generic terms like "database", "API",
"server", "code", "script", "function", "feature", "tool" unless they are
proper names.

Brief content:
GOAL: {goal}
DID: {did}
LEARNED: {learned}
FRICTION: {friction}
WINS: {wins}

Return a JSON object with a single key "entities" containing an array of
objects with "name" (string) and "type" (one of: "person", "library",
"ai_model", "service").

Examples of valid entities:
- {{"name": "Claude", "type": "ai_model"}}
- {{"name": "React", "type": "library"}}
- {{"name": "Matthew", "type": "person"}}
- {{"name": "AWS S3", "type": "service"}}

Extract only what is clearly present in the text. Return {{"entities": []}} if
there are no specific named entities."""

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

def _brief_text(brief: dict) -> str:
    """Flatten the extractable fields of a brief dict into a single string."""
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


def extract_entities(brief_json: str, session_id: str, date: str, *,
                     binary: str = "claude", model: str = "haiku",
                     verbose: bool = False) -> list[dict]:
    """Extract entities from one brief JSON string.

    Returns list of {name, type} dicts (raw, before persistence).
    """
    try:
        brief = json.loads(brief_json)
    except json.JSONDecodeError:
        return []

    text = _brief_text(brief)
    try:
        entities = _call_extraction(text, binary=binary, model=model)
    except Exception as exc:
        if verbose:
            print(f"  ! entity extraction failed for {session_id}/{date}: {exc}")
        return []

    # Filter to valid types only
    valid_types = {"person", "library", "ai_model", "service"}
    return [e for e in entities
            if isinstance(e, dict) and e.get("name") and e.get("type") in valid_types]


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
