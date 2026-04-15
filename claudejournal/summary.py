"""Human-readable activity reports from the SQLite store."""
from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

from claudejournal.db import connect


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def summarize_day(db_path: Path, day: str) -> str:
    conn = connect(db_path)
    lines = [f"== {day} =="]
    rows = conn.execute(
        """
        SELECT p.display_name AS name, p.id AS pid,
               COUNT(*) AS events,
               SUM(CASE WHEN e.kind='user_prompt' THEN 1 ELSE 0 END) AS prompts,
               SUM(CASE WHEN e.kind='file_edit' THEN 1 ELSE 0 END) AS edits,
               SUM(CASE WHEN e.kind='tool_use' THEN 1 ELSE 0 END) AS tool_uses,
               SUM(CASE WHEN e.kind='correction' THEN 1 ELSE 0 END) AS corrections,
               SUM(CASE WHEN e.kind='appreciation' THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN e.kind='error' THEN 1 ELSE 0 END) AS errors
        FROM events e
        JOIN projects p ON p.id = e.project_id
        WHERE e.date = ?
        GROUP BY p.id
        ORDER BY events DESC
        """,
        (day,),
    ).fetchall()

    if not rows:
        conn.close()
        return f"{lines[0]}\n  (no activity)"

    for r in rows:
        files = conn.execute(
            "SELECT COUNT(DISTINCT path) AS n FROM files_touched WHERE project_id = ? AND date = ?",
            (r["pid"], day),
        ).fetchone()["n"] or 0
        lines.append(
            f"  {r['name']:30s}  {r['prompts']:3d} prompts  "
            f"{r['edits']:3d} edits ({files} files)  "
            f"{r['tool_uses']:3d} tools  "
            f"{r['corrections']:2d} corrections  "
            f"{r['wins']:2d} wins  "
            f"{r['errors']:2d} errors"
        )

    total_events = sum(r["events"] for r in rows)
    total_edits = sum(r["edits"] for r in rows)
    total_files = conn.execute(
        "SELECT COUNT(DISTINCT path) AS n FROM files_touched WHERE date = ?",
        (day,),
    ).fetchone()["n"] or 0
    lines.append(
        f"  {'TOTAL':30s}  {len(rows)} projects  "
        f"{total_edits} edits across {total_files} files  "
        f"{total_events} events"
    )
    conn.close()
    return "\n".join(lines)


def summarize_range(db_path: Path, days: int = 7) -> str:
    end = date_cls.today()
    start = end - timedelta(days=days - 1)
    out = []
    d = start
    while d <= end:
        out.append(summarize_day(db_path, d.isoformat()))
        d += timedelta(days=1)
    return "\n\n".join(out)


def overall_stats(db_path: Path) -> str:
    conn = connect(db_path)
    p = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    s = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    e = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    dates = conn.execute(
        "SELECT MIN(date) AS first, MAX(date) AS last FROM events WHERE date != ''"
    ).fetchone()
    conn.close()
    return (
        f"database: {db_path}\n"
        f"  projects:  {p}\n"
        f"  sessions:  {s}\n"
        f"  events:    {e}\n"
        f"  range:     {dates['first']} -> {dates['last']}"
    )
