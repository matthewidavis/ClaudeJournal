"""Orchestrates discover + extract + persist."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from claudejournal.config import Config
from claudejournal.db import clear_session_events, connect, session_is_current
from claudejournal.discover import ProjectDir, SessionInputs, discover
from claudejournal.extract import Event, Snippet, parse_session, session_time_bounds
from claudejournal.redact import Redactor


def _upsert_project(conn: sqlite3.Connection, proj: ProjectDir, cwd: str | None, when: str) -> None:
    conn.execute(
        """
        INSERT INTO projects (id, display_name, cwd, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name = excluded.display_name,
            cwd = COALESCE(excluded.cwd, projects.cwd),
            last_seen = excluded.last_seen
        """,
        (proj.project_id, proj.display_name, cwd, when, when),
    )


def _insert_session(
    conn: sqlite3.Connection,
    inputs: SessionInputs,
    events: list[Event],
) -> None:
    started, ended = session_time_bounds(events)
    counts = {"user_prompt": 0, "tool_use": 0, "correction": 0}
    for e in events:
        if e.kind in counts:
            counts[e.kind] += 1
        elif e.kind == "file_edit":
            counts["tool_use"] += 1

    main_path = str(inputs.main_jsonl) if inputs.main_jsonl else ""
    conn.execute(
        """
        INSERT INTO sessions (id, project_id, jsonl_path, jsonl_mtime, jsonl_size,
            inputs_signature, has_main_transcript, subagent_count,
            started_at, ended_at, event_count, user_prompt_count, tool_use_count,
            correction_count, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            jsonl_path = excluded.jsonl_path,
            jsonl_mtime = excluded.jsonl_mtime,
            jsonl_size = excluded.jsonl_size,
            inputs_signature = excluded.inputs_signature,
            has_main_transcript = excluded.has_main_transcript,
            subagent_count = excluded.subagent_count,
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            event_count = excluded.event_count,
            user_prompt_count = excluded.user_prompt_count,
            tool_use_count = excluded.tool_use_count,
            correction_count = excluded.correction_count,
            extracted_at = excluded.extracted_at
        """,
        (
            inputs.session_id, inputs.project_id,
            main_path, inputs.max_mtime, inputs.total_size,
            inputs.signature,
            1 if inputs.main_jsonl else 0,
            len(inputs.subagent_jsonls),
            started, ended, len(events),
            counts["user_prompt"], counts["tool_use"], counts["correction"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _insert_events(conn: sqlite3.Connection, sess_id: str, project_id: str, events: list[Event]) -> None:
    conn.executemany(
        """
        INSERT INTO events (session_id, project_id, ts, date, kind, tool_name,
            path, summary, sentiment, raw_uuid, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (sess_id, project_id, e.ts, e.date, e.kind, e.tool_name,
             e.path, e.summary, e.sentiment, e.raw_uuid, e.source)
            for e in events
        ],
    )


def _insert_snippets(conn: sqlite3.Connection, sess_id: str, project_id: str, snippets: list[Snippet]) -> None:
    if not snippets:
        return
    # snippet table has no source column by design — snippets get used for
    # narrator "notable moments" regardless of origin. Source is still tracked
    # on the Snippet dataclass for any future filtering.
    conn.executemany(
        """
        INSERT INTO assistant_snippets (session_id, project_id, ts, date, text, raw_uuid)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(sess_id, project_id, s.ts, s.date, s.text, s.raw_uuid) for s in snippets],
    )


def _update_files_touched(conn: sqlite3.Connection, project_id: str, events: list[Event]) -> None:
    # Count file_edit events per (date, path)
    touches: dict[tuple[str, str], int] = {}
    for e in events:
        if e.kind == "file_edit" and e.path and e.date:
            touches[(e.date, e.path)] = touches.get((e.date, e.path), 0) + 1
    for (date, path), n in touches.items():
        conn.execute(
            """
            INSERT INTO files_touched (project_id, date, path, touch_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, date, path) DO UPDATE SET
                touch_count = files_touched.touch_count + excluded.touch_count
            """,
            (project_id, date, path, n),
        )


def scan(cfg: Config, force: bool = False, verbose: bool = True, progress=None) -> dict:
    redactor = Redactor(cfg.redact_patterns)
    projects = discover(cfg.claude_home, cfg.include_projects, cfg.exclude_projects)

    conn = connect(cfg.db_path)
    stats = {"projects": 0, "sessions_scanned": 0, "sessions_skipped": 0,
             "events_written": 0, "errors": 0}
    now_iso = datetime.now(timezone.utc).isoformat()

    total_sessions = sum(len(p.sessions) for p in projects)
    processed = 0
    try:
        for proj in projects:
            _upsert_project(conn, proj, cwd=None, when=now_iso)
            stats["projects"] += 1

            for s in proj.sessions:
                processed += 1
                if progress:
                    try: progress(processed, total_sessions, proj.display_name)
                    except Exception: pass
                if not force and session_is_current(conn, s.session_id, s.signature):
                    stats["sessions_skipped"] += 1
                    continue

                try:
                    items = list(parse_session(
                        s,
                        cfg.correction_patterns,
                        cfg.appreciation_patterns,
                        redactor,
                        cfg.max_prompt_chars,
                    ))
                except Exception as exc:
                    stats["errors"] += 1
                    if verbose:
                        print(f"  ! error parsing {s.session_id[:8]}: {exc}")
                    continue

                events = [x for x in items if isinstance(x, Event)]
                snippets = [x for x in items if isinstance(x, Snippet)]

                clear_session_events(conn, s.session_id)
                _insert_session(conn, s, events)
                _insert_events(conn, s.session_id, s.project_id, events)
                _insert_snippets(conn, s.session_id, s.project_id, snippets)
                _update_files_touched(conn, s.project_id, events)

                stats["sessions_scanned"] += 1
                stats["events_written"] += len(events)
                stats.setdefault("snippets_written", 0)
                stats["snippets_written"] += len(snippets)

                if verbose:
                    origin = ""
                    if s.main_jsonl and s.subagent_jsonls:
                        origin = f"main+{len(s.subagent_jsonls)}sub"
                    elif s.main_jsonl:
                        origin = "main"
                    else:
                        origin = f"{len(s.subagent_jsonls)}sub-only"
                    print(f"  {proj.display_name:30s}  {s.session_id[:8]}  "
                          f"{len(events):5d} events  {len(snippets):4d} snippets  [{origin}]")

            conn.commit()
    finally:
        conn.close()

    return stats
