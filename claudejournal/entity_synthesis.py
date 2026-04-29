"""Entity synthesis narration module — first-person wiki prose per qualifying entity.

For each entity that qualifies for a profile page (2+ projects OR 5+ dates),
generates a 100-250 word first-person synthesis paragraph that frames the
entity's role across the user's work. Stored in narrations table with
scope='entity_profile', key=<canonical_name>.

Follows the same cache-invalidation, annotation prompt-pin, and Claude CLI
patterns as topics.py and arcs.py.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrate import (
    _annotations_hash_contribution,
    format_pinned_corrections,
    load_annotations_for_scope,
)


# Bump when the entity synthesis prompt meaningfully changes — participates in
# the input hash so bumping forces regeneration of all entity pages cleanly.
ENTITY_PROMPT_VERSION = "v1"


ENTITY_SYSTEM = """You produce a short, personal synthesis of everything the user has done with a specific tool, library, service, or person across their projects. You are writing in the user's voice — first person, past tense for completed work, present for ongoing patterns.

Rules:
1. Write in first person, as if the user is reflecting on their own experience with this entity.
2. Frame the entity's role across the user's work: what problems it solved, what patterns emerged, any recurring frictions or wins.
3. Do NOT produce a generic description of the entity — this is personal experience, not documentation.
4. Reference specific projects and contexts where useful, but do not retell events chronologically.
5. Length: 100–250 words. Dense and useful; no padding.
6. Output plain prose paragraphs only. No headers, no bullet lists, no markdown.
7. Do not start with "I" — vary the opening sentence.
8. Do not meta-reference the journal, notes, or this synthesis process."""


def _load_briefs_for_entity(conn: sqlite3.Connection, entity_id: str) -> list[dict]:
    """Load all session briefs that contain this entity, sorted by date ascending.

    Returns dicts with keys: session_id, date, brief_json (parsed), project_name.
    Only briefs where the entity is actually recorded in brief_entities are included.
    """
    rows = conn.execute(
        """
        SELECT be.session_id, be.date,
               sb.brief_json,
               p.display_name AS project_name,
               p.id AS project_id
        FROM brief_entities be
        JOIN session_briefs sb
          ON sb.session_id = be.session_id
         AND sb.date = be.date
        JOIN projects p ON p.id = sb.project_id
        WHERE be.entity_id = ?
          AND be.date != ''
        ORDER BY be.date ASC, p.display_name ASC
        """,
        (entity_id,),
    ).fetchall()

    results = []
    for r in rows:
        try:
            brief = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            brief = {}
        brief["_session_id"] = r["session_id"]
        brief["_date"] = r["date"]
        brief["_project_name"] = r["project_name"]
        brief["_project_id"] = r["project_id"]
        results.append(brief)
    return results


def _entity_input_hash(canonical_name: str, entity_type: str | None,
                       briefs: list[dict],
                       annotations: list[dict] | None = None) -> str:
    """Deterministic hash over prompt version + entity identity + briefs + annotations.

    Changes when new briefs land that mention this entity, when existing briefs
    change, or when user annotations for this entity are edited.
    """
    h = hashlib.sha256()
    h.update(ENTITY_PROMPT_VERSION.encode())
    h.update(b"\x00")
    h.update(canonical_name.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update((entity_type or "").encode("utf-8", errors="replace"))
    h.update(b"\x00")
    # Sort by (date, session_id) for determinism across runs
    for b in sorted(briefs, key=lambda x: (x.get("_date", ""), x.get("_session_id", ""))):
        sid = (b.get("_session_id") or "").encode("utf-8", errors="replace")
        date = (b.get("_date") or "").encode("utf-8", errors="replace")
        goal = (b.get("goal") or "").encode("utf-8", errors="replace")
        h.update(sid); h.update(b"\x01")
        h.update(date); h.update(b"\x02")
        h.update(goal); h.update(b"\x03")
    if annotations:
        h.update(b"\x04annotations\x04")
        h.update(_annotations_hash_contribution(annotations))
    return h.hexdigest()[:16]


def _already_current(conn: sqlite3.Connection, canonical_name: str,
                     input_hash: str) -> bool:
    """True if narrations already has a current entity_profile page for this entity."""
    row = conn.execute(
        "SELECT input_hash, prompt_version FROM narrations "
        "WHERE scope='entity_profile' AND key=?",
        (canonical_name,),
    ).fetchone()
    if not row:
        return False
    return (row["input_hash"] == input_hash
            and row["prompt_version"] == ENTITY_PROMPT_VERSION)


def _build_entity_message(entity_name: str, entity_type: str | None,
                          briefs: list[dict],
                          annotations: list[dict] | None = None,
                          max_chars: int = 10000) -> str:
    """Build the user message for the entity synthesis prompt.

    Groups briefs by project. Extracts goal, learned, friction, wins excerpts.
    Injects annotation pins if present.
    """
    type_label = f" ({entity_type})" if entity_type else ""
    lines = [f"ENTITY: {entity_name}{type_label}", ""]

    # Group by project, sorted by project name
    by_project: dict[str, list[dict]] = {}
    for b in briefs:
        pname = b.get("_project_name", "unknown")
        by_project.setdefault(pname, []).append(b)

    # Sort projects by earliest date
    project_order = sorted(
        by_project.keys(),
        key=lambda p: min(b.get("_date", "9999") for b in by_project[p])
    )

    lines.append(
        f"This entity appears across {len(project_order)} project(s). "
        f"Below are excerpts from session briefs that mention it (oldest first per project):"
    )
    lines.append("")

    body_parts: list[str] = []
    for pname in project_order:
        pbriefs = by_project[pname]
        # Get the date range for this project
        dates = sorted(b.get("_date", "") for b in pbriefs if b.get("_date"))
        date_range = f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "")
        parts = [f"Project: {pname}  [{date_range}]"]
        for b in sorted(pbriefs, key=lambda x: x.get("_date", "")):
            date = b.get("_date", "")
            goal = b.get("goal", "")
            learned = b.get("learned") or []
            friction = b.get("friction") or []
            wins = b.get("wins") or []
            excerpt_parts = []
            if goal:
                excerpt_parts.append(f"    [{date}] Goal: {goal}")
            if learned:
                excerpt_parts.append(
                    f"    [{date}] Learned: " + " | ".join(str(x) for x in learned[:3])
                )
            if friction:
                excerpt_parts.append(
                    f"    [{date}] Friction: " + " | ".join(str(x) for x in friction[:2])
                )
            if wins:
                excerpt_parts.append(
                    f"    [{date}] Wins: " + " | ".join(str(x) for x in wins[:2])
                )
            if excerpt_parts:
                parts.extend(excerpt_parts)
        body_parts.append("\n".join(parts))

    body = "\n\n".join(body_parts)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n...[truncated for length — earliest entries retained]"

    lines.append(body)
    lines.append("")

    # PINNED CORRECTIONS — user annotations for this entity (Phase E v2).
    if annotations:
        lines.append(format_pinned_corrections(annotations))

    lines.append(
        "Write a first-person synthesis of the user's experience with this entity across their projects. "
        "Plain paragraphs only — no headers, no bullets, no markdown. 100–250 words."
    )
    return "\n".join(lines)


def _call_claude_prose(user_msg: str, system: str, model: str,
                       binary: str = "claude") -> str:
    """Call the Claude CLI for free-prose output. Returns prose string.

    Identical to the helpers in topics.py and arcs.py — kept local to avoid
    cross-module coupling. Raises RuntimeError on CLI failure.
    """
    from claudejournal.narrator.claude_code import _no_session_leak

    model_map = {
        "haiku":  "claude-haiku-4-5",
        "sonnet": "claude-sonnet-4-5",
        "opus":   "claude-opus-4-5",
    }
    full_model = model_map.get(model, model)

    cmd = [
        binary, "-p",
        "--model", full_model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", system,
    ]
    with _no_session_leak():
        proc = subprocess.run(
            cmd, input=user_msg, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"CLI error: {envelope.get('result', '')[:500]}")
    prose = (envelope.get("result") or "").strip()
    if not prose:
        raise RuntimeError(
            f"empty result from CLI; envelope keys: {list(envelope.keys())}"
        )
    return prose


def _persist_entity(conn: sqlite3.Connection, canonical_name: str,
                    prose: str, input_hash: str, model: str) -> None:
    """Upsert an entity_profile narration row."""
    conn.execute(
        """
        INSERT INTO narrations (scope, key, date, project_id, prose,
            prompt_version, input_hash, generated_at, model)
        VALUES ('entity_profile', ?, '', NULL, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, key) DO UPDATE SET
            date = excluded.date,
            project_id = excluded.project_id,
            prose = excluded.prose,
            prompt_version = excluded.prompt_version,
            input_hash = excluded.input_hash,
            generated_at = excluded.generated_at,
            model = excluded.model
        """,
        (
            canonical_name, prose,
            ENTITY_PROMPT_VERSION, input_hash,
            datetime.now(timezone.utc).isoformat(), model,
        ),
    )


def synthesize_entity(conn: sqlite3.Connection,
                      entity_id: str, entity_name: str,
                      entity_type: str | None, canonical_name: str, *,
                      model: str = "haiku",
                      force: bool = False,
                      verbose: bool = True) -> dict:
    """Generate (or regenerate) the synthesis narration for a single entity.

    Returns a stats dict: {generated, skipped, cost_usd (if available), reason}.
    """
    briefs = _load_briefs_for_entity(conn, entity_id)
    if not briefs:
        if verbose:
            print(f"  skip {entity_name!r}  (no briefs found)")
        return {"generated": 0, "skipped": 1, "reason": "no briefs"}

    # Load entity-scoped annotations so they participate in the hash and
    # are injected into the prompt as PINNED CORRECTIONS.
    annotations = load_annotations_for_scope(conn, "entity_profile", canonical_name)

    ih = _entity_input_hash(canonical_name, entity_type, briefs, annotations)

    if not force and _already_current(conn, canonical_name, ih):
        if verbose:
            print(f"  skip {entity_name!r}  (cache hit)")
        return {"generated": 0, "skipped": 1, "reason": "cache"}

    user_msg = _build_entity_message(entity_name, entity_type, briefs, annotations)
    try:
        prose = _call_claude_prose(user_msg, ENTITY_SYSTEM, model=model)
    except Exception as exc:
        if verbose:
            import sys
            print(f"  ! entity {entity_name!r}: {exc}", file=sys.stderr)
        return {"generated": 0, "skipped": 0, "errors": 1, "reason": str(exc)}

    _persist_entity(conn, canonical_name, prose, ih, model)
    conn.commit()
    if verbose:
        projects_count = len({b["_project_id"] for b in briefs})
        print(f"  entity {entity_name!r}  ({len(prose)} chars, "
              f"{projects_count} projects, model={model})")
    return {"generated": 1, "skipped": 0, "errors": 0}


def load_entity_synthesis(conn: sqlite3.Connection,
                          canonical_name: str) -> str | None:
    """Load the synthesis prose for an entity from the narrations table.

    Returns None if no synthesis has been generated yet.
    Used by render.py to pass synthesis prose to render_entity_profile_page().
    """
    row = conn.execute(
        "SELECT prose FROM narrations WHERE scope='entity_profile' AND key=?",
        (canonical_name,),
    ).fetchone()
    return row["prose"] if row else None


def run(cfg: Config, *, all_: bool = True, force: bool = False,
        model: str | None = None, verbose: bool = True,
        progress=None) -> dict:
    """Run the full entity synthesis sweep for all qualifying entities.

    Loops over all qualifying entities, skipping those with a current hash
    match (unless force=True). Returns aggregate stats.

    On malformed output from the model, logs the error and continues —
    does not fail the whole batch.
    """
    def _tick(done: int, total: int, label: str = "") -> None:
        if progress:
            try: progress("entity_synthesis", done, total, label)
            except Exception: pass

    from claudejournal.entity_pages import qualifying_entities

    m = model or "haiku"
    conn = connect(cfg.db_path)
    try:
        entities = qualifying_entities(conn)
        total = len(entities)
        stats = {
            "generated": 0, "skipped": 0, "errors": 0,
            "total": total,
        }
        _tick(0, max(total, 1), "starting")
        for idx, ent in enumerate(entities, 1):
            label = ent.get("entity_name", "")
            _tick(idx, max(total, 1), label)
            try:
                s = synthesize_entity(
                    conn,
                    entity_id=ent["entity_id"],
                    entity_name=ent["entity_name"],
                    entity_type=ent.get("entity_type"),
                    canonical_name=ent.get("canonical_name") or ent["entity_name"],
                    model=m,
                    force=force,
                    verbose=verbose,
                )
                stats["generated"] += s.get("generated", 0)
                stats["skipped"] += s.get("skipped", 0)
                stats["errors"] += s.get("errors", 0)
            except Exception as exc:
                stats["errors"] += 1
                if verbose:
                    import sys
                    print(f"  ! entity {label!r}: {exc}", file=sys.stderr)
        _tick(total, max(total, 1), "done")
    finally:
        conn.close()
    return stats
