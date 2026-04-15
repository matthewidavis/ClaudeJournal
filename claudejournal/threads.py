"""Deterministic thread detection + anchor list generation.

Principles (from design memory):
  - Threads and anchors must be computed from facts, never inferred by LLM.
  - Narrator gets them as constraints; it describes, it does not decide.
  - Anchors limit the narrator to only cite dates we know exist.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as date_cls, datetime, timedelta


THREAD_IDLE_DAYS = 5
ANCHOR_LOOKBACK_DAYS = 14


def _parse_date(s: str) -> date_cls:
    return datetime.strptime(s, "%Y-%m-%d").date()


def compute_threads(conn: sqlite3.Connection, as_of_date: str) -> list[dict]:
    """For each project active on `as_of_date`, look back THREAD_IDLE_DAYS
    days for prior activity. Return thread records sorted by recency of first touch.

    A thread = a project with prior briefed activity within the idle window.
    Projects active only today are NOT threads (no continuity yet).
    """
    today = _parse_date(as_of_date)
    window_start = (today - timedelta(days=THREAD_IDLE_DAYS)).isoformat()

    # Projects active today (have briefs today)
    today_projects = [r["project_id"] for r in conn.execute(
        """SELECT DISTINCT project_id FROM session_briefs WHERE date = ?""",
        (as_of_date,),
    ).fetchall()]

    threads = []
    for pid in today_projects:
        touches = conn.execute(
            """
            SELECT DISTINCT date FROM session_briefs
            WHERE project_id = ? AND date >= ? AND date <= ?
            ORDER BY date
            """,
            (pid, window_start, as_of_date),
        ).fetchall()
        touch_dates = [t["date"] for t in touches]
        if len(touch_dates) < 2:
            continue  # not a thread — just today's work

        pname_row = conn.execute(
            "SELECT display_name FROM projects WHERE id = ?", (pid,),
        ).fetchone()
        pname = pname_row["display_name"] if pname_row else pid

        # Mood/goal from earliest touch in window (the "where it started")
        first_brief = conn.execute(
            """SELECT brief_json FROM session_briefs
               WHERE project_id = ? AND date = ? ORDER BY session_id LIMIT 1""",
            (pid, touch_dates[0]),
        ).fetchone()
        goal_hint = ""
        if first_brief:
            try:
                goal_hint = (json.loads(first_brief["brief_json"]).get("goal") or "")[:120]
            except json.JSONDecodeError:
                pass

        # Status: simple rule — wins today + no friction tomorrow = resolved,
        # otherwise active. A proper stuck/abandoned detector is Stage 6 material.
        today_brief = conn.execute(
            """SELECT brief_json FROM session_briefs
               WHERE project_id = ? AND date = ? ORDER BY session_id LIMIT 1""",
            (pid, as_of_date),
        ).fetchone()
        status = "active"
        if today_brief:
            try:
                tb = json.loads(today_brief["brief_json"])
                if tb.get("wins") and not tb.get("friction"):
                    status = "resolved"
                elif tb.get("friction") and not tb.get("wins"):
                    status = "stuck"
            except json.JSONDecodeError:
                pass

        threads.append({
            "project_id": pid,
            "project_name": pname,
            "first_date": touch_dates[0],
            "last_date": as_of_date,
            "touches": touch_dates,
            "span_days": (_parse_date(as_of_date) - _parse_date(touch_dates[0])).days,
            "status": status,
            "goal_hint": goal_hint,
        })

    # Max 3 surfaced per day (design rule). Prioritize longest spans.
    threads.sort(key=lambda t: (-t["span_days"], t["project_name"]))
    return threads[:3]


def available_anchors(conn: sqlite3.Connection, as_of_date: str,
                      project_ids: list[str] | None = None) -> list[dict]:
    """Anchors the narrator is ALLOWED to cite as [YYYY-MM-DD] brackets.

    A valid anchor is any prior date (within ANCHOR_LOOKBACK_DAYS) for which
    we have a daily narration OR at least one briefed session. Scoped to
    projects active today unless project_ids is None.
    """
    today = _parse_date(as_of_date)
    window_start = (today - timedelta(days=ANCHOR_LOOKBACK_DAYS)).isoformat()

    pids_filter = ""
    params: list = [window_start, as_of_date]
    if project_ids:
        placeholders = ",".join("?" * len(project_ids))
        pids_filter = f" AND project_id IN ({placeholders})"
        params.extend(project_ids)

    rows = conn.execute(
        f"""
        SELECT DISTINCT b.project_id, p.display_name AS project_name, b.date
        FROM session_briefs b
        JOIN projects p ON p.id = b.project_id
        WHERE b.date >= ? AND b.date < ? {pids_filter}
        ORDER BY b.date DESC, p.display_name
        """,
        params,
    ).fetchall()

    anchors = []
    for r in rows:
        # Short label = first 60 chars of that date's brief goal
        brief_row = conn.execute(
            """SELECT brief_json FROM session_briefs
               WHERE project_id = ? AND date = ? ORDER BY session_id LIMIT 1""",
            (r["project_id"], r["date"]),
        ).fetchone()
        label = ""
        if brief_row:
            try:
                label = (json.loads(brief_row["brief_json"]).get("goal") or "")[:80]
            except json.JSONDecodeError:
                pass
        anchors.append({
            "date": r["date"],
            "project_name": r["project_name"],
            "project_id": r["project_id"],
            "label": label,
        })
    return anchors
