"""Session brief generation — assembles input from DB, calls narrator, persists."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrator import ClaudeCodeNarrator, Narrator
from claudejournal.narrator.base import BriefInput
from claudejournal.narrator.claude_code import PROMPT_VERSION


def _load_memory_text(claude_home: Path, project_id: str, max_bytes: int = 4000) -> str:
    mem_dir = claude_home / "projects" / project_id / "memory"
    if not mem_dir.exists():
        return ""
    out: list[str] = []
    total = 0
    for md in sorted(mem_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        chunk = f"### {md.name}\n{text}\n"
        out.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return "".join(out)[:max_bytes]


def _input_hash(inp: BriefInput) -> str:
    # Stable hash over content that should invalidate the brief cache
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update((inp.started_at or "").encode())
    h.update((inp.ended_at or "").encode())
    for p in inp.user_prompts:
        h.update(p.get("summary", "").encode("utf-8", errors="replace"))
    for s in inp.assistant_snippets:
        h.update(s.get("text", "").encode("utf-8", errors="replace"))
    for f in inp.files_touched:
        h.update(f.get("path", "").encode("utf-8", errors="replace"))
    h.update(inp.memory_text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _load_session_input(conn: sqlite3.Connection, session_id: str, claude_home: Path) -> BriefInput | None:
    s = conn.execute(
        """
        SELECT s.id, s.project_id, s.started_at, s.ended_at,
               p.display_name AS project_name,
               COALESCE(date(s.started_at), '') AS date
        FROM sessions s JOIN projects p ON p.id = s.project_id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    if not s:
        return None

    prompts = [dict(r) for r in conn.execute(
        """
        SELECT ts, kind, summary FROM events
        WHERE session_id = ? AND kind IN ('user_prompt','correction','appreciation')
        ORDER BY ts ASC
        """,
        (session_id,),
    ).fetchall()]

    snippets = [dict(r) for r in conn.execute(
        """
        SELECT ts, text FROM assistant_snippets
        WHERE session_id = ? AND length(text) BETWEEN 40 AND 380
        ORDER BY ts ASC
        """,
        (session_id,),
    ).fetchall()]

    # Top files edited in this session
    files = [dict(r) for r in conn.execute(
        """
        SELECT path, COUNT(*) AS touch_count FROM events
        WHERE session_id = ? AND kind = 'file_edit' AND path IS NOT NULL
        GROUP BY path ORDER BY touch_count DESC LIMIT 20
        """,
        (session_id,),
    ).fetchall()]

    memory = _load_memory_text(claude_home, s["project_id"])

    date = s["date"] or (s["started_at"] or "")[:10]

    return BriefInput(
        session_id=s["id"],
        project_name=s["project_name"],
        project_id=s["project_id"],
        date=date,
        started_at=s["started_at"],
        ended_at=s["ended_at"],
        user_prompts=prompts,
        assistant_snippets=snippets,
        files_touched=files,
        memory_text=memory,
    )


def _brief_is_current(conn: sqlite3.Connection, session_id: str, input_hash: str) -> bool:
    row = conn.execute(
        "SELECT input_hash, prompt_version FROM session_briefs WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return False
    return row["input_hash"] == input_hash and row["prompt_version"] == PROMPT_VERSION


def _persist_brief(conn: sqlite3.Connection, inp: BriefInput, result, input_hash: str) -> None:
    conn.execute(
        """
        INSERT INTO session_briefs (session_id, project_id, date, prompt_version,
            input_hash, brief_json, generated_at, cost_usd, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            date = excluded.date,
            prompt_version = excluded.prompt_version,
            input_hash = excluded.input_hash,
            brief_json = excluded.brief_json,
            generated_at = excluded.generated_at,
            cost_usd = excluded.cost_usd,
            model = excluded.model
        """,
        (
            inp.session_id, inp.project_id, inp.date, PROMPT_VERSION,
            input_hash, json.dumps(result.brief, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
            result.cost_usd, result.model,
        ),
    )


def _pick_sessions(conn: sqlite3.Connection, session_id: str | None, date: str | None,
                   all_: bool, min_events: int) -> list[str]:
    if session_id:
        return [session_id]

    sql = """
      SELECT s.id FROM sessions s
      WHERE s.event_count >= ?
    """
    params: list = [min_events]
    if date:
        sql += " AND date(s.started_at) = ?"
        params.append(date)
    sql += " ORDER BY s.started_at ASC"
    if not all_ and not date:
        # default: last 7 days of sessions
        sql = ("SELECT s.id FROM sessions s WHERE s.event_count >= ? "
               "AND date(s.started_at) >= date('now', '-7 days') ORDER BY s.started_at ASC")
        params = [min_events]

    return [r["id"] for r in conn.execute(sql, params).fetchall()]


def run(cfg: Config, *, narrator: Narrator | None = None,
        session_id: str | None = None, date: str | None = None,
        all_: bool = False, force: bool = False, dry_run: bool = False,
        min_events: int = 5, verbose: bool = True, progress=None,
        max_workers: int | None = None) -> dict:
    """Brief generation — parallelized across sessions.

    Narrator calls are independent per session, so we fan them out to a thread
    pool (each `claude -p` subprocess runs isolated). DB writes stay on the
    main thread — workers return results, main thread persists via a simple
    loop over `as_completed`.
    """
    narrator = narrator or ClaudeCodeNarrator()
    workers = max_workers if max_workers is not None else getattr(cfg, "max_workers", 4)
    stats = {"generated": 0, "skipped": 0, "errors": 0}

    conn = connect(cfg.db_path)
    try:
        session_ids = _pick_sessions(conn, session_id, date, all_, min_events)
        if verbose:
            print(f"candidate sessions: {len(session_ids)}  (workers: {workers})")

        # Pre-compute inputs + cache check on the main thread.
        to_run: list[tuple[str, object, str]] = []  # (sid, inp, input_hash)
        for sid in session_ids:
            inp = _load_session_input(conn, sid, cfg.claude_home)
            if not inp:
                continue
            ih = _input_hash(inp)
            if not force and _brief_is_current(conn, sid, ih):
                stats["skipped"] += 1
                if verbose:
                    print(f"  skip {sid[:8]}  ({inp.project_name} · {inp.date})")
                continue
            to_run.append((sid, inp, ih))

        total = len(to_run)
        if total == 0:
            return stats

        def _work(sid, inp, ih):
            return sid, inp, ih, narrator.narrate_session(inp, dry_run=dry_run), None

        def _work_safe(sid, inp, ih):
            try:
                return _work(sid, inp, ih)
            except Exception as exc:
                return sid, inp, ih, None, exc

        completed = 0
        # Single worker => same thread, makes tracebacks easier when debugging.
        if workers <= 1:
            iterator = (_work_safe(sid, inp, ih) for sid, inp, ih in to_run)
        else:
            ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="brief")
            futs = [ex.submit(_work_safe, sid, inp, ih) for sid, inp, ih in to_run]
            iterator = (f.result() for f in as_completed(futs))

        try:
            for sid, inp, ih, result, err in iterator:
                completed += 1
                if progress:
                    try: progress(completed, total, inp.project_name)
                    except Exception: pass

                if err is not None:
                    stats["errors"] += 1
                    if verbose: print(f"  ! {sid[:8]} ({inp.project_name}): {err}")
                    continue

                if dry_run:
                    stats["generated"] += 1
                    continue

                _persist_brief(conn, inp, result, ih)
                conn.commit()
                stats["generated"] += 1
                if verbose:
                    brief = result.brief
                    print(f"  [{completed}/{total}] {sid[:8]}  {inp.project_name:20s}  "
                          f"{inp.date}  mood={brief.get('mood','')[:30]!r}")
        finally:
            if workers > 1:
                ex.shutdown(wait=True)
    finally:
        conn.close()
    return stats
