"""Project arc narration module — first-person retrospective page per project.

Each project with at least one project_day narration gets an arc page that
synthesizes the trajectory of work: intent, obstacles, shifts, current state.
Arc pages are stored in narrations (scope='project_arc', key=<project_id>) and
rendered as `out/projects/<name>/index.html`, replacing the redirect stubs.

Arc inputs are project_day narrations (already condensed prose), NOT raw briefs.
This keeps the token count bounded and focuses the synthesis on narrative shape
rather than raw detail.
"""
from __future__ import annotations

import hashlib
import json
import re
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


# Bump when the arc prompt meaningfully changes — forces regeneration of all
# arc pages cleanly via hash invalidation.
# v2: annotation prompt-pins added (Phase E v2).
ARC_PROMPT_VERSION = "v2"


ARC_SYSTEM = """You produce a personal retrospective of a multi-day project — its arc, not its log. You're writing as the person who did the work, looking back on what they built and learned.

Rules:
1. Structure: open with the project's intent and initial approach. Middle: the obstacles, pivots, and shifts that shaped the work. Close: current state and what's next or what was completed.
2. First person, reflective, past tense for completed phases and present tense for ongoing work.
3. Do NOT restate daily content — condense and distill. If five days all involved debugging the same issue, describe the issue and its resolution once, well.
4. Length: 400–800 words. Substantive enough to be a real retrospective; tight enough to read in one sitting.
5. Plain prose paragraphs only — no headers, no bullet lists, no markdown. Flowing text that reads like a well-written journal entry.
6. Do not start with "I" — vary the opening sentence."""


def _load_project_day_narrations(conn: sqlite3.Connection,
                                  project_id: str) -> list[dict]:
    """Load all project_day narrations for this project, sorted by date ascending."""
    rows = conn.execute(
        """SELECT date, prose, input_hash FROM narrations
           WHERE scope='project_day' AND project_id = ?
           AND prose IS NOT NULL AND prose != ''
           ORDER BY date ASC""",
        (project_id,),
    ).fetchall()
    return [{"date": r["date"], "prose": r["prose"], "input_hash": r["input_hash"]}
            for r in rows]


def _projects_with_narrations(conn: sqlite3.Connection) -> list[dict]:
    """Return all projects that have at least one project_day narration."""
    rows = conn.execute(
        """SELECT DISTINCT p.id, p.display_name
           FROM projects p
           JOIN narrations n ON n.project_id = p.id
           WHERE n.scope = 'project_day' AND n.prose IS NOT NULL AND n.prose != ''
           ORDER BY p.display_name"""
    ).fetchall()
    return [{"id": r["id"], "name": r["display_name"]} for r in rows]


def _arc_input_hash(project_id: str, narrations: list[dict],
                    annotations: list[dict] | None = None) -> str:
    """Deterministic hash over prompt version + project_id + narrations + annotations.

    When project_day narrations change (new days, updated prose) the hash
    changes and the arc page regenerates on the next cycle. When annotations
    change (user edits a correction for this arc), the hash also changes,
    triggering re-narration with the updated PINNED CORRECTIONS block.
    """
    h = hashlib.sha256()
    h.update(ARC_PROMPT_VERSION.encode())
    h.update(b"\x00")
    h.update(project_id.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    # Sort by date for determinism
    for n in sorted(narrations, key=lambda x: x.get("date", "")):
        h.update((n.get("date") or "").encode("utf-8", errors="replace"))
        h.update(b"\x01")
        h.update((n.get("input_hash") or "").encode("utf-8", errors="replace"))
        h.update(b"\x02")
    if annotations:
        h.update(b"\x03annotations\x03")
        h.update(_annotations_hash_contribution(annotations))
    return h.hexdigest()[:16]


def _already_current(conn: sqlite3.Connection, project_id: str,
                     input_hash: str) -> bool:
    """True if narrations already has a current arc page for this project."""
    row = conn.execute(
        "SELECT input_hash, prompt_version FROM narrations "
        "WHERE scope='project_arc' AND key=?",
        (project_id,),
    ).fetchone()
    if not row:
        return False
    return row["input_hash"] == input_hash and row["prompt_version"] == ARC_PROMPT_VERSION


def _build_arc_message(project_name: str, project_id: str,
                       narrations: list[dict],
                       annotations: list[dict] | None = None,
                       max_chars: int = 14000) -> str:
    """Build the user message for the arc synthesis prompt.

    annotations: list of annotation rows for this project arc
    (scope='project_arc', key=project_id). If non-empty, a PINNED CORRECTIONS
    block is inserted after the source material and before the final instruction.
    """
    lines = [
        f"PROJECT: {project_name}",
        f"PROJECT_ID: {project_id}",
        "",
        f"This project has {len(narrations)} days of activity. Below are the per-day "
        f"narrations in chronological order:",
        "",
    ]
    body_parts = []
    for n in narrations:
        body_parts.append(f"[{n['date']}]\n{n['prose'].strip()}")

    body = "\n\n".join(body_parts)
    if len(body) > max_chars:
        # Truncate from the middle to keep both the beginning (intent) and
        # the end (current state) — these are the most narrative-critical parts.
        half = max_chars // 2
        head = body[:half]
        tail = body[-half:]
        body = (head + "\n\n...[middle section truncated for length]\n\n" + tail)

    lines.append(body)
    lines.append("")

    # PINNED CORRECTIONS — user annotations for this project arc (Phase E v2).
    # Placed after source material, before the final instruction.
    if annotations:
        lines.append(format_pinned_corrections(annotations))

    lines.append(
        "Write a first-person retrospective arc for this project. "
        "Plain paragraphs only — no headers, no bullets, no markdown. 400–800 words."
    )
    return "\n".join(lines)


def _call_claude_prose(user_msg: str, system: str, model: str,
                       binary: str = "claude") -> str:
    """Call the Claude CLI for free-prose output. Returns the prose string."""
    from claudejournal.narrator.claude_code import _no_session_leak

    # Map shorthand model aliases to full model IDs
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


def _persist_arc(conn: sqlite3.Connection, project_id: str, prose: str,
                 input_hash: str, model: str) -> None:
    """Upsert a project arc narration row."""
    conn.execute(
        """
        INSERT INTO narrations (scope, key, date, project_id, prose,
            prompt_version, input_hash, generated_at, model)
        VALUES ('project_arc', ?, '', ?, ?, ?, ?, ?, ?)
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
            project_id, project_id, prose,
            ARC_PROMPT_VERSION, input_hash,
            datetime.now(timezone.utc).isoformat(), model,
        ),
    )


def summarize_arc(conn: sqlite3.Connection, project_id: str, project_name: str, *,
                  model: str = "sonnet", force: bool = False,
                  verbose: bool = True) -> dict:
    """Generate (or regenerate) the arc narration for a single project.

    Returns a stats dict with generated/skipped counts.
    """
    narrations = _load_project_day_narrations(conn, project_id)
    if not narrations:
        if verbose:
            print(f"  skip {project_name!r}  (no project_day narrations)")
        return {"generated": 0, "skipped": 1, "reason": "no narrations"}

    # Phase E v2: load arc-scoped annotations so they participate in the hash
    # and are injected into the prompt as PINNED CORRECTIONS.
    annotations = load_annotations_for_scope(conn, "project_arc", project_id)

    ih = _arc_input_hash(project_id, narrations, annotations)

    if not force and _already_current(conn, project_id, ih):
        if verbose:
            print(f"  skip {project_name!r}  (cache hit)")
        return {"generated": 0, "skipped": 1, "reason": "cache"}

    user_msg = _build_arc_message(project_name, project_id, narrations, annotations)
    prose = _call_claude_prose(user_msg, ARC_SYSTEM, model=model)

    _persist_arc(conn, project_id, prose, ih, model)
    conn.commit()
    if verbose:
        print(f"  arc {project_name!r}  ({len(prose)} chars, model={model})")
    return {"generated": 1, "skipped": 0}


def list_arcs(conn: sqlite3.Connection) -> list[dict]:
    """Return all projects with their arc page status."""
    projects = _projects_with_narrations(conn)
    results = []
    for p in projects:
        narrations = _load_project_day_narrations(conn, p["id"])
        annotations = load_annotations_for_scope(conn, "project_arc", p["id"])
        ih = _arc_input_hash(p["id"], narrations, annotations)
        row = conn.execute(
            "SELECT input_hash, prompt_version, generated_at FROM narrations "
            "WHERE scope='project_arc' AND key=?",
            (p["id"],),
        ).fetchone()
        if not row:
            status = "missing"
        elif row["input_hash"] == ih and row["prompt_version"] == ARC_PROMPT_VERSION:
            status = "current"
        else:
            status = "stale"
        results.append({
            "project_id": p["id"],
            "project_name": p["name"],
            "status": status,
            "days": len(narrations),
            "generated_at": row["generated_at"] if row else None,
        })
    return results


def run(cfg: Config, *, all_: bool = True, force: bool = False,
        model: str | None = None, verbose: bool = True,
        progress=None) -> dict:
    """Run the full arc synthesis sweep for all projects with narrations.

    Returns aggregate stats.
    """
    def _tick(done: int, total: int, label: str = "") -> None:
        if progress:
            try: progress("arc_summary", done, total, label)
            except Exception: pass

    m = model or cfg.arc_model
    conn = connect(cfg.db_path)
    try:
        projects = _projects_with_narrations(conn)
        total = len(projects)
        stats = {"generated": 0, "skipped": 0, "errors": 0, "total": total}
        _tick(0, max(total, 1), "starting")
        for idx, p in enumerate(projects, 1):
            _tick(idx, max(total, 1), p["name"])
            try:
                s = summarize_arc(conn, p["id"], p["name"], model=m,
                                   force=force, verbose=verbose)
                stats["generated"] += s.get("generated", 0)
                stats["skipped"] += s.get("skipped", 0)
            except Exception as exc:
                stats["errors"] += 1
                if verbose:
                    import sys
                    print(f"  ! arc {p['name']!r}: {exc}", file=sys.stderr)
        _tick(total, max(total, 1), "done")
    finally:
        conn.close()
    return stats
