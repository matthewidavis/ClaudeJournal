"""What's pending? — a dry check before `run` so you know what will happen.

Fast: pure SQL + filesystem stat, no LLM calls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.discover import discover
from claudejournal.narrator.claude_code import NARRATION_PROMPT_VERSION, PROMPT_VERSION


def check(cfg: Config) -> dict:
    """Return a structured view of what `run` would do."""
    conn = connect(cfg.db_path)
    try:
        # 1. Sessions whose JSONL has changed or that we've never scanned.
        projects = discover(cfg.claude_home, cfg.include_projects, cfg.exclude_projects)
        scan_pending: list[str] = []
        for proj in projects:
            for s in proj.sessions:
                row = conn.execute(
                    "SELECT inputs_signature FROM sessions WHERE id = ?",
                    (s.session_id,),
                ).fetchone()
                if not row or row["inputs_signature"] != s.signature:
                    scan_pending.append(s.session_id)

        # 2. (session_id, date) pairs eligible for brief (enough events on
        #    that date) with no current brief. Mirrors brief.py's picker —
        #    a long-running session produces one brief per active day.
        brief_pending = [
            (r["sid"], r["d"]) for r in conn.execute(
                """SELECT e.session_id AS sid, e.date AS d, COUNT(*) AS n
                   FROM events e
                   LEFT JOIN session_briefs b
                     ON b.session_id = e.session_id AND b.date = e.date
                        AND b.prompt_version = ?
                   WHERE e.date IS NOT NULL AND e.date != ''
                     AND b.session_id IS NULL
                   GROUP BY e.session_id, e.date HAVING n >= ?""",
                (PROMPT_VERSION, cfg.min_events_for_brief),
            ).fetchall()
        ]

        # 3. Dates whose daily narration is either missing or stale
        #    (prompt_version bumped, or input_hash no longer matches the set
        #    of briefs for that day). Mirrors the logic in narrate.py.
        from claudejournal.narrate import _narration_input_hash, _load_briefs_for_day
        daily_dates = [
            r["date"] for r in conn.execute(
                "SELECT DISTINCT date FROM session_briefs WHERE date != '' ORDER BY date"
            ).fetchall()
        ]
        daily_pending: list[str] = []
        proj_day_pending: list[tuple[str, str]] = []
        for d in daily_dates:
            briefs = _load_briefs_for_day(conn, d)
            if not briefs:
                continue
            # Daily
            day_hash = _narration_input_hash(briefs, "daily")
            row = conn.execute(
                "SELECT prompt_version, input_hash FROM narrations WHERE scope='daily' AND key=?",
                (d,),
            ).fetchone()
            if not row or row["prompt_version"] != NARRATION_PROMPT_VERSION or (row["input_hash"] or "") != day_hash:
                daily_pending.append(d)
            # Per-project-day
            by_pid: dict[str, list] = {}
            for b in briefs:
                by_pid.setdefault(b["_project_id"], []).append(b)
            for pid, pbriefs in by_pid.items():
                phash = _narration_input_hash(pbriefs, "project_day", pid)
                prow = conn.execute(
                    """SELECT prompt_version, input_hash FROM narrations
                       WHERE scope='project_day' AND key=?""",
                    (f"{pid}|{d}",),
                ).fetchone()
                if not prow or prow["prompt_version"] != NARRATION_PROMPT_VERSION or (prow["input_hash"] or "") != phash:
                    proj_day_pending.append((pid, d))

        # 5. Weekly rollups — missing or stale (inputs changed / version bumped).
        from claudejournal.rollup import (weeks_with_activity, _load_daily_for_week,
                                          _weekly_input_hash, ROLLUP_PROMPT_VERSION)
        rollup_pending: list[str] = []
        for w in weeks_with_activity(conn):
            dailies = _load_daily_for_week(conn, w)
            if not dailies:
                continue
            whash = _weekly_input_hash(dailies)
            wrow = conn.execute(
                "SELECT prompt_version, input_hash FROM narrations WHERE scope='weekly' AND key=?",
                (w,),
            ).fetchone()
            if not wrow or wrow["prompt_version"] != ROLLUP_PROMPT_VERSION or (wrow["input_hash"] or "") != whash:
                rollup_pending.append(w)

        # 6. Monthly rollups — missing or stale.
        from claudejournal.monthly import (months_with_activity, _load_weeklies_overlapping,
                                           _load_daily_dates, _monthly_input_hash,
                                           MONTHLY_PROMPT_VERSION)
        monthly_pending: list[str] = []
        for m in months_with_activity(conn):
            weeklies = _load_weeklies_overlapping(conn, m)
            anchors = _load_daily_dates(conn, m)
            if not weeklies and not anchors:
                continue
            mhash = _monthly_input_hash(weeklies, anchors)
            mrow = conn.execute(
                "SELECT prompt_version, input_hash FROM narrations WHERE scope='monthly' AND key=?",
                (m,),
            ).fetchone()
            if not mrow or mrow["prompt_version"] != MONTHLY_PROMPT_VERSION or (mrow["input_hash"] or "") != mhash:
                monthly_pending.append(m)

        total = (len(scan_pending) + len(brief_pending) + len(daily_pending)
                 + len(proj_day_pending) + len(rollup_pending) + len(monthly_pending))

        return {
            "scan": len(scan_pending),
            "brief": len(brief_pending),
            "daily_narration": len(daily_pending),
            "project_day_narration": len(proj_day_pending),
            "weekly_rollup": len(rollup_pending),
            "monthly_rollup": len(monthly_pending),
            "total_pending": total,
            "has_updates": total > 0,
            "samples": {
                "scan": scan_pending[:5],
                "brief": [f"{sid[:8]}@{d}" for sid, d in brief_pending[:5]],
                "daily": daily_pending[:5],
                "weekly": rollup_pending[:5],
                "monthly": monthly_pending[:5],
            },
        }
    finally:
        conn.close()


def format_status(result: dict) -> str:
    if not result["has_updates"]:
        return "nothing to do — journal is up to date."
    lines = [f"{result['total_pending']} pending item(s):"]
    for key, label in [
        ("scan", "session scans"),
        ("brief", "briefs"),
        ("daily_narration", "daily narrations"),
        ("project_day_narration", "project-day narrations"),
        ("weekly_rollup", "weekly rollups"),
        ("monthly_rollup", "monthly rollups"),
    ]:
        n = result.get(key, 0)
        if n:
            lines.append(f"  {n:4d}  {label}")
    return "\n".join(lines)
