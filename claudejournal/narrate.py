"""Daily + project-day narration orchestration."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrator import ClaudeCodeNarrator, Narrator
from claudejournal.narrator.base import NarrationInput
from claudejournal.narrator.claude_code import NARRATION_PROMPT_VERSION
from claudejournal.mood import lexical_signals
from claudejournal.threads import available_anchors, compute_threads


def _load_briefs_for_day(conn: sqlite3.Connection, date: str,
                         project_id: str | None = None) -> list[dict]:
    sql = """
      SELECT b.session_id, b.project_id, b.brief_json, p.display_name AS project_name
      FROM session_briefs b JOIN projects p ON p.id = b.project_id
      WHERE b.date = ?
    """
    params: list = [date]
    if project_id:
        sql += " AND b.project_id = ?"
        params.append(project_id)
    sql += " ORDER BY p.display_name, b.session_id"

    out = []
    for r in conn.execute(sql, params).fetchall():
        try:
            data = json.loads(r["brief_json"])
        except json.JSONDecodeError:
            continue
        data["_session_id"] = r["session_id"]
        data["_project_name"] = r["project_name"]
        data["_project_id"] = r["project_id"]
        data["_lexical"] = lexical_signals(conn, r["session_id"])
        out.append(data)
    return out


def _prior_entry(conn: sqlite3.Connection, scope: str, key: str, date: str,
                 project_id: str | None) -> str:
    if scope == "daily":
        row = conn.execute(
            "SELECT prose FROM narrations WHERE scope='daily' AND date < ? ORDER BY date DESC LIMIT 1",
            (date,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT prose FROM narrations WHERE scope='project_day'
               AND project_id = ? AND date < ? ORDER BY date DESC LIMIT 1""",
            (project_id, date),
        ).fetchone()
    return row["prose"] if row else ""


def _persist(conn: sqlite3.Connection, scope: str, key: str, date: str,
             project_id: str | None, prose: str, model: str) -> None:
    conn.execute(
        """
        INSERT INTO narrations (scope, key, date, project_id, prose,
            prompt_version, generated_at, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, key) DO UPDATE SET
            prose = excluded.prose,
            prompt_version = excluded.prompt_version,
            generated_at = excluded.generated_at,
            model = excluded.model
        """,
        (scope, key, date, project_id, prose,
         NARRATION_PROMPT_VERSION, datetime.now(timezone.utc).isoformat(), model),
    )


def _already_current(conn: sqlite3.Connection, scope: str, key: str) -> bool:
    row = conn.execute(
        "SELECT prompt_version FROM narrations WHERE scope=? AND key=?",
        (scope, key),
    ).fetchone()
    return bool(row and row["prompt_version"] == NARRATION_PROMPT_VERSION)


def run(cfg: Config, *, narrator: Narrator | None = None,
        date: str | None = None, all_: bool = False,
        project_only: bool = False, daily_only: bool = False,
        force: bool = False, dry_run: bool = False,
        verbose: bool = True, progress=None) -> dict:
    narrator = narrator or ClaudeCodeNarrator()
    stats = {"daily_generated": 0, "project_day_generated": 0,
             "skipped": 0, "errors": 0}

    conn = connect(cfg.db_path)
    try:
        if date:
            dates = [date]
        elif all_:
            dates = [r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM session_briefs WHERE date != '' ORDER BY date"
            ).fetchall()]
        else:
            dates = [r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM session_briefs WHERE date >= date('now','-7 days') ORDER BY date"
            ).fetchall()]

        if verbose:
            print(f"narrating {len(dates)} day(s)")

        # Pre-compute total narration calls across all dates so the progress
        # bar ticks per actual narration, not per date.
        date_briefs: dict[str, list[dict]] = {}
        total = 0
        for d in dates:
            bb = _load_briefs_for_day(conn, d)
            if not bb:
                continue
            date_briefs[d] = bb
            if not project_only:
                total += 1  # daily
            if not daily_only:
                total += len({b["_project_id"] for b in bb})

        done = 0
        for d, all_briefs in date_briefs.items():

            # ---- Daily narration (full day across projects) ----
            if not project_only:
                key = d
                done += 1
                if progress:
                    try: progress(done, total, f"daily {d}")
                    except Exception: pass
                if not force and _already_current(conn, "daily", key):
                    stats["skipped"] += 1
                    if verbose:
                        print(f"  skip daily {d}")
                else:
                    prior = _prior_entry(conn, "daily", key, d, None)
                    pids_today = sorted({b["_project_id"] for b in all_briefs})
                    threads = compute_threads(conn, d)
                    anchors = available_anchors(conn, d, pids_today)
                    ninp = NarrationInput(
                        date=d, scope="daily",
                        briefs=all_briefs, prior_entry=prior,
                        threads=threads, anchors=anchors,
                    )
                    if verbose:
                        pnames = sorted({b["_project_name"] for b in all_briefs})
                        print(f"  daily {d}  [{len(all_briefs)} briefs across {len(pnames)}: {', '.join(pnames)}]")
                    try:
                        res = narrator.narrate_day(ninp, dry_run=dry_run)
                    except Exception as exc:
                        stats["errors"] += 1
                        if verbose: print(f"    ! {exc}")
                        continue
                    if dry_run:
                        if verbose: print(res.prose[:600])
                    else:
                        _persist(conn, "daily", key, d, None, res.prose, res.model)
                        stats["daily_generated"] += 1
                        if verbose: print(f"    -> {len(res.prose)} chars")

            # ---- Per-project-day narrations ----
            if not daily_only:
                projects = {}
                for b in all_briefs:
                    projects.setdefault(b["_project_id"], []).append(b)
                for pid, pbriefs in projects.items():
                    key = f"{pid}|{d}"
                    pname = pbriefs[0]["_project_name"]
                    done += 1
                    if progress:
                        try: progress(done, total, f"{pname} · {d}")
                        except Exception: pass
                    if not force and _already_current(conn, "project_day", key):
                        stats["skipped"] += 1
                        continue
                    prior = _prior_entry(conn, "project_day", key, d, pid)
                    # Project-scoped threads and anchors
                    threads = [t for t in compute_threads(conn, d) if t["project_id"] == pid]
                    anchors = available_anchors(conn, d, [pid])
                    ninp = NarrationInput(
                        date=d, scope="project_day",
                        project_name=pname, project_id=pid,
                        briefs=pbriefs, prior_entry=prior,
                        threads=threads, anchors=anchors,
                    )
                    if verbose:
                        print(f"  proj  {d}  {pname}  [{len(pbriefs)} briefs]")
                    try:
                        res = narrator.narrate_day(ninp, dry_run=dry_run)
                    except Exception as exc:
                        stats["errors"] += 1
                        if verbose: print(f"    ! {exc}")
                        continue
                    if dry_run:
                        if verbose: print(res.prose[:400])
                    else:
                        _persist(conn, "project_day", key, d, pid, res.prose, res.model)
                        stats["project_day_generated"] += 1
                        if verbose: print(f"    -> {len(res.prose)} chars")

            conn.commit()
    finally:
        conn.close()
    return stats
