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

        # 2. Sessions eligible for brief (enough events) with no current brief.
        brief_pending = [
            r["id"] for r in conn.execute(
                """SELECT s.id FROM sessions s
                   LEFT JOIN session_briefs b
                     ON b.session_id = s.id AND b.prompt_version = ?
                   WHERE s.event_count >= ? AND b.session_id IS NULL""",
                (PROMPT_VERSION, cfg.min_events_for_brief),
            ).fetchall()
        ]

        # 3. Dates with briefs that don't yet have a current-version daily narration.
        daily_pending = [
            r["date"] for r in conn.execute(
                """SELECT DISTINCT b.date FROM session_briefs b
                   LEFT JOIN narrations n
                     ON n.scope='daily' AND n.key = b.date
                        AND n.prompt_version = ?
                   WHERE b.date != '' AND n.prose IS NULL""",
                (NARRATION_PROMPT_VERSION,),
            ).fetchall()
        ]

        # 4. project×date pairs with briefs but no current-version project_day narration.
        proj_day_pending = [
            (r["project_id"], r["date"]) for r in conn.execute(
                """SELECT DISTINCT b.project_id, b.date FROM session_briefs b
                   LEFT JOIN narrations n
                     ON n.scope='project_day'
                        AND n.project_id = b.project_id
                        AND n.date = b.date
                        AND n.prompt_version = ?
                   WHERE b.date != '' AND n.prose IS NULL""",
                (NARRATION_PROMPT_VERSION,),
            ).fetchall()
        ]

        # 5. Weeks with daily narrations but no weekly rollup.
        from claudejournal.rollup import weeks_with_activity
        existing_weeks = {
            r["key"] for r in conn.execute(
                "SELECT key FROM narrations WHERE scope='weekly'"
            ).fetchall()
        }
        rollup_pending = [w for w in weeks_with_activity(conn) if w not in existing_weeks]

        total = (len(scan_pending) + len(brief_pending) + len(daily_pending)
                 + len(proj_day_pending) + len(rollup_pending))

        return {
            "scan": len(scan_pending),
            "brief": len(brief_pending),
            "daily_narration": len(daily_pending),
            "project_day_narration": len(proj_day_pending),
            "weekly_rollup": len(rollup_pending),
            "total_pending": total,
            "has_updates": total > 0,
            "samples": {
                "scan": scan_pending[:5],
                "brief": brief_pending[:5],
                "daily": daily_pending[:5],
                "weekly": rollup_pending[:5],
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
    ]:
        n = result.get(key, 0)
        if n:
            lines.append(f"  {n:4d}  {label}")
    return "\n".join(lines)
