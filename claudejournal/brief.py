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
    # Stable hash over content that should invalidate the brief cache.
    # The date is in the PK and the event/snippet/file lists are already
    # date-filtered upstream, so the hash doesn't need the date itself —
    # but we do include the prior-brief hint: when the previous day's
    # brief changes, today's continuity hint changes, so regen is correct.
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
    h.update(inp.prior_brief_hint.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _load_session_input(conn: sqlite3.Connection, session_id: str, date: str,
                        claude_home: Path) -> BriefInput | None:
    """Build a BriefInput scoped to one (session, date) pair.

    Long-running Claude sessions span many days; we emit one brief per day
    of activity so each calendar day's events contribute to that day's
    narration. All event/snippet/file queries filter by the date column
    rather than pulling the full session's history."""
    s = conn.execute(
        """
        SELECT s.id, s.project_id, s.started_at, s.ended_at,
               p.display_name AS project_name
        FROM sessions s JOIN projects p ON p.id = s.project_id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    if not s:
        return None

    # First/last event timestamp ON THIS DATE — gives the brief a real
    # start/end window for the day, not the whole session's span.
    span = conn.execute(
        """SELECT MIN(ts) AS started_at, MAX(ts) AS ended_at
           FROM events WHERE session_id = ? AND date = ?""",
        (session_id, date),
    ).fetchone()

    prompts = [dict(r) for r in conn.execute(
        """
        SELECT ts, kind, summary FROM events
        WHERE session_id = ? AND date = ?
          AND kind IN ('user_prompt','correction','appreciation')
        ORDER BY ts ASC
        """,
        (session_id, date),
    ).fetchall()]

    snippets = [dict(r) for r in conn.execute(
        """
        SELECT ts, text FROM assistant_snippets
        WHERE session_id = ? AND date = ?
          AND length(text) BETWEEN 40 AND 380
        ORDER BY ts ASC
        """,
        (session_id, date),
    ).fetchall()]

    # Files edited ON THIS DATE.
    files = [dict(r) for r in conn.execute(
        """
        SELECT path, COUNT(*) AS touch_count FROM events
        WHERE session_id = ? AND date = ? AND kind = 'file_edit' AND path IS NOT NULL
        GROUP BY path ORDER BY touch_count DESC LIMIT 20
        """,
        (session_id, date),
    ).fetchall()]

    memory = _load_memory_text(claude_home, s["project_id"])

    # Continuity: compact hint from the most recent prior-day brief on the
    # same session. Helps the narrator understand "where we left off" for
    # long-running work without dumping the whole history into context.
    prior_hint = _prior_brief_hint(conn, session_id, date)

    return BriefInput(
        session_id=s["id"],
        project_name=s["project_name"],
        project_id=s["project_id"],
        date=date,
        started_at=(span["started_at"] if span else None) or s["started_at"],
        ended_at=(span["ended_at"] if span else None) or s["ended_at"],
        user_prompts=prompts,
        assistant_snippets=snippets,
        files_touched=files,
        memory_text=memory,
        prior_brief_hint=prior_hint,
    )


def _prior_brief_hint(conn: sqlite3.Connection, session_id: str, date: str,
                      max_chars: int = 600) -> str:
    """Compact summary of the most recent prior-day brief for this session.
    Format: goal + 2 bullets from `did` + mood. Kept short so it steers the
    new brief ("we're continuing from X") without drowning it in context."""
    row = conn.execute(
        """SELECT date, brief_json FROM session_briefs
           WHERE session_id = ? AND date < ? AND date != ''
           ORDER BY date DESC LIMIT 1""",
        (session_id, date),
    ).fetchone()
    if not row:
        return ""
    try:
        b = json.loads(row["brief_json"] or "{}")
    except json.JSONDecodeError:
        return ""
    parts = [f"(previous day {row['date']})"]
    if b.get("goal"):
        parts.append(f"goal: {b['goal']}")
    did = [x for x in (b.get("did") or []) if isinstance(x, str)][:2]
    if did:
        parts.append("did: " + "; ".join(did))
    if b.get("mood"):
        parts.append(f"mood: {b['mood']}")
    hint = " | ".join(parts)
    return hint[:max_chars]


def _brief_is_current(conn: sqlite3.Connection, session_id: str, date: str,
                      input_hash: str) -> bool:
    row = conn.execute(
        """SELECT input_hash, prompt_version FROM session_briefs
           WHERE session_id = ? AND date = ?""",
        (session_id, date),
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
        ON CONFLICT(session_id, date) DO UPDATE SET
            project_id = excluded.project_id,
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


def _pick_session_dates(conn: sqlite3.Connection, session_id: str | None,
                        date: str | None, all_: bool,
                        min_events: int) -> list[tuple[str, str]]:
    """Return (session_id, date) pairs eligible for briefing.

    Eligibility: the session has at least `min_events` events on that
    specific date. This replaces the old one-brief-per-session rule so
    long-running sessions contribute a brief to every calendar day they
    had real activity on.
    """
    sql_base = """
      SELECT e.session_id AS sid, e.date AS d, COUNT(*) AS n
      FROM events e
      WHERE e.date IS NOT NULL AND e.date != ''
    """
    params: list = []
    if session_id:
        sql_base += " AND e.session_id = ?"
        params.append(session_id)
    if date:
        sql_base += " AND e.date = ?"
        params.append(date)
    elif not all_ and not session_id:
        # Default window: last 7 days of activity.
        sql_base += " AND e.date >= date('now', '-7 days')"
    sql_base += " GROUP BY e.session_id, e.date HAVING n >= ? ORDER BY e.date ASC, e.session_id ASC"
    params.append(min_events)
    return [(r["sid"], r["d"]) for r in conn.execute(sql_base, params).fetchall()]


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
        pairs = _pick_session_dates(conn, session_id, date, all_, min_events)
        if verbose:
            print(f"candidate (session, date) pairs: {len(pairs)}  (workers: {workers})")

        # Pre-compute inputs + cache check on the main thread. Briefs for
        # earlier dates of the same session run first (ordered by date in
        # _pick_session_dates) so each day's prior-brief hint reflects the
        # freshest upstream content.
        to_run: list[tuple[str, object, str]] = []  # (sid, inp, input_hash)
        for sid, d in pairs:
            inp = _load_session_input(conn, sid, d, cfg.claude_home)
            if not inp:
                continue
            ih = _input_hash(inp)
            if not force and _brief_is_current(conn, sid, d, ih):
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
