"""Render SQLite state into a feed-style warm-diary HTML site.

Layout:
  out/index.html                              — main feed (all days, newest first)
  out/projects/<name>/index.html              — same feed scoped to one project
  out/projects/<name>/<YYYY-MM-DD>.html       — single project-day deep link
  out/weekly/<ISO-week>.html                  — weekly retrospective standalone
  out/chat.html                               — chat deep-link (floating bubble is primary)
  out/daily/<YYYY-MM-DD>.html                 — compat redirect to index.html#date
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from claudejournal import interludes as interludemod
from claudejournal.db import connect
from claudejournal.templates import (
    esc,
    layout,
    render_chat_page,
    render_day_entry,
    render_feed,
    render_month_break,
    render_week_break,
)


def _iso_week_of(date: str) -> str:
    dt = datetime.strptime(date, "%Y-%m-%d")
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _day_counts(conn: sqlite3.Connection, date: str, pid: str | None = None) -> dict:
    sql = """
      SELECT
        SUM(CASE WHEN kind='user_prompt' THEN 1 ELSE 0 END) AS prompts,
        SUM(CASE WHEN kind='file_edit' THEN 1 ELSE 0 END) AS edits,
        SUM(CASE WHEN kind='tool_use' THEN 1 ELSE 0 END) AS tool_uses,
        SUM(CASE WHEN kind='correction' THEN 1 ELSE 0 END) AS corrections,
        SUM(CASE WHEN kind='appreciation' THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN kind='error' THEN 1 ELSE 0 END) AS errors,
        COUNT(*) AS events
      FROM events WHERE date = ?
    """
    params: list = [date]
    if pid:
        sql += " AND project_id = ?"
        params.append(pid)
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def _projects_active_on(conn: sqlite3.Connection, date: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT p.display_name FROM events e
           JOIN projects p ON p.id = e.project_id
           WHERE e.date = ? ORDER BY p.display_name""",
        (date,),
    ).fetchall()
    return [r["display_name"] for r in rows]


def _day_has_learning(conn: sqlite3.Connection, date: str) -> bool:
    """True if at least one brief for this date has a non-empty learned list."""
    rows = conn.execute(
        "SELECT brief_json FROM session_briefs WHERE date = ?", (date,)
    ).fetchall()
    for r in rows:
        try:
            b = json.loads(r["brief_json"])
        except json.JSONDecodeError:
            continue
        if b.get("learned"):
            return True
    return False


def _day_mood_label(conn: sqlite3.Connection, date: str) -> str:
    """Aggregate a day's lexical mood by majority across its session briefs."""
    from collections import Counter
    from claudejournal.mood import lexical_signals
    rows = conn.execute(
        "SELECT session_id FROM session_briefs WHERE date = ?", (date,)
    ).fetchall()
    if not rows:
        return ""
    labels = [lexical_signals(conn, r["session_id"])["label"] for r in rows]
    return Counter(labels).most_common(1)[0][0]


def _month_of(date: str) -> str:
    return date[:7] if len(date) >= 7 else ""


def _friendly_month_label(ym: str) -> str:
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
    except Exception:
        return ym


def _friendly_week_label(iso_week: str) -> str:
    """'2026-W15' -> 'Apr 7–13' (or 'Mar 31 – Apr 6' for cross-month)."""
    try:
        year, wk = iso_week.split("-W")
        mon = datetime.strptime(f"{year}-W{int(wk):02d}-1", "%G-W%V-%u").date()
        from datetime import timedelta
        sun = mon + timedelta(days=6)
        if mon.month == sun.month:
            return f"{mon.strftime('%b')} {mon.day}–{sun.day}"
        return f"{mon.strftime('%b')} {mon.day} – {sun.strftime('%b')} {sun.day}"
    except Exception:
        return iso_week


def _load_day_bundle(conn: sqlite3.Connection, date: str, pid: str | None = None) -> dict:
    """Everything needed to render one day entry: narration, counts, prompts, snippets, files, briefs."""
    if pid:
        nrow = conn.execute(
            "SELECT prose FROM narrations WHERE scope='project_day' AND date=? AND project_id=?",
            (date, pid),
        ).fetchone()
    else:
        nrow = conn.execute(
            "SELECT prose FROM narrations WHERE scope='daily' AND date=?", (date,),
        ).fetchone()
    narration = nrow["prose"] if nrow else ""

    counts = _day_counts(conn, date, pid)

    prompts_sql = """
        SELECT ts, kind, summary, p.display_name AS project_name
        FROM events e JOIN projects p ON p.id = e.project_id
        WHERE e.date = ? AND e.kind IN ('user_prompt','correction','appreciation')
    """
    snips_sql = """
        SELECT s.ts, s.text, p.display_name AS project_name
        FROM assistant_snippets s JOIN projects p ON p.id = s.project_id
        WHERE s.date = ? AND length(s.text) BETWEEN 50 AND 380
    """
    files_sql = """
        SELECT path, touch_count, project_id FROM files_touched WHERE date = ?
    """
    briefs_sql = """
        SELECT b.session_id, b.brief_json, p.display_name AS project_name
        FROM session_briefs b JOIN projects p ON p.id = b.project_id
        WHERE b.date = ?
    """
    params: list = [date]
    if pid:
        prompts_sql += " AND e.project_id = ?"
        snips_sql   += " AND s.project_id = ?"
        files_sql   += " AND project_id = ?"
        briefs_sql  += " AND b.project_id = ?"
        params_p = params + [pid]
    else:
        params_p = params

    prompts = [dict(r) for r in conn.execute(
        prompts_sql + " ORDER BY e.ts ASC", params_p
    ).fetchall()]
    snippets = [dict(r) for r in conn.execute(
        snips_sql + " ORDER BY s.ts ASC", params_p
    ).fetchall()]
    files = [dict(r) for r in conn.execute(
        files_sql + " ORDER BY touch_count DESC", params_p
    ).fetchall()]
    briefs_raw = conn.execute(briefs_sql, params_p).fetchall()
    briefs = []
    for br in briefs_raw:
        try:
            b = json.loads(br["brief_json"])
            b["_session_id"] = br["session_id"]
            b["_project_name"] = br["project_name"]
            briefs.append(b)
        except json.JSONDecodeError:
            continue

    # Prefer the first brief's mood for the meta line
    mood = ""
    if briefs:
        mood = (briefs[0].get("mood") or "")[:40]

    return {
        "narration": narration, "counts": counts,
        "prompts": prompts, "snippets": snippets,
        "files": files, "briefs": briefs, "mood": mood,
    }


def _active_dates(conn: sqlite3.Connection, pid: str | None = None) -> list[str]:
    if pid:
        rows = conn.execute(
            "SELECT DISTINCT date FROM events WHERE date != '' AND project_id = ? ORDER BY date DESC",
            (pid,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT date FROM events WHERE date != '' ORDER BY date DESC"
        ).fetchall()
    return [r["date"] for r in rows]


def _weekly_rollups(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["key"]: r["prose"]
        for r in conn.execute("SELECT key, prose FROM narrations WHERE scope='weekly'").fetchall()
    }


def _tags_index(conn: sqlite3.Connection) -> tuple[dict[str, list[str]], "Counter"]:
    """Aggregate tags per date from session_briefs.

    Returns ({date: [unique tag list]}, Counter(tag -> day-count))."""
    from collections import Counter
    per_date: dict[str, set[str]] = {}
    tag_day_counts: Counter = Counter()
    rows = conn.execute(
        "SELECT date, brief_json FROM session_briefs WHERE date IS NOT NULL AND date != ''"
    ).fetchall()
    for r in rows:
        try:
            b = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        raw = b.get("tags") or []
        if not isinstance(raw, list):
            continue
        clean = []
        for t in raw:
            if not isinstance(t, str):
                continue
            s = t.strip().lower()
            if not s or len(s) > 32:
                continue
            clean.append(s)
        if not clean:
            continue
        per_date.setdefault(r["date"], set()).update(clean)
    # day-count per tag (not session-count — filters are day-scoped)
    for date, tags in per_date.items():
        for t in tags:
            tag_day_counts[t] += 1
    return ({d: sorted(ts) for d, ts in per_date.items()}, tag_day_counts)


def _monthly_rollups(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["key"]: r["prose"]
        for r in conn.execute("SELECT key, prose FROM narrations WHERE scope='monthly'").fetchall()
    }


def _render_feed_pages(conn: sqlite3.Connection, dates: list[str], anchor_base: str,
                       pid: str | None = None,
                       tags_by_date: dict[str, list[str]] | None = None) -> list[str]:
    """Produce the feed entries + week/month breaks interleaved, newest first."""
    weekly = _weekly_rollups(conn)
    monthly = _monthly_rollups(conn)
    tags_by_date = tags_by_date or {}
    out: list[str] = []
    last_week: str | None = None
    last_month: str | None = None
    for date in dates:
        week = _iso_week_of(date)
        month = _month_of(date)
        # Week break when week changes
        if week != last_week and last_week is not None:
            out.append(render_week_break(last_week, weekly.get(last_week, ""), anchor_base))
        # Month break AFTER the week break (month is the bigger boundary —
        # visually it sits below the week break, so appears after in DOM order
        # given newest-first iteration).
        if month != last_month and last_month is not None:
            out.append(render_month_break(last_month, monthly.get(last_month, ""), anchor_base))
        last_week = week
        last_month = month
        bundle = _load_day_bundle(conn, date, pid)
        projects_today = _projects_active_on(conn, date)
        interlude = interludemod.get_for_date(conn, date) if not bundle["narration"] else None
        day_mood = _day_mood_label(conn, date)
        has_learn = _day_has_learning(conn, date)
        out.append(render_day_entry(
            date, bundle["narration"], bundle["mood"],
            bundle["counts"], bundle["prompts"], bundle["snippets"],
            bundle["files"], bundle["briefs"], anchor_base=anchor_base,
            projects_in_day=projects_today,
            interlude=interlude,
            month=_month_of(date),
            mood_label=day_mood,
            has_learning=has_learn,
            tags=tags_by_date.get(date, []),
        ))
    if last_week and last_week in weekly:
        out.append(render_week_break(last_week, weekly[last_week], anchor_base))
    if last_month and last_month in monthly:
        out.append(render_month_break(last_month, monthly[last_month], anchor_base))
    return out


def render_site(db_path: Path, out_dir: Path, claude_home: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "daily").mkdir(exist_ok=True)
    (out_dir / "projects").mkdir(exist_ok=True)
    (out_dir / "weekly").mkdir(exist_ok=True)
    (out_dir / "monthly").mkdir(exist_ok=True)

    conn = connect(db_path)
    stats = {"index": 0, "project_index": 0, "project_day": 0, "weekly": 0,
             "monthly": 0, "daily_redirect": 0}

    # ---------- Home feed ----------
    dates = _active_dates(conn)
    pr = conn.execute(
        """SELECT p.id, p.display_name, COUNT(DISTINCT e.date) AS ndays
           FROM projects p JOIN events e ON e.project_id = p.id
           GROUP BY p.id HAVING ndays > 0 ORDER BY MAX(e.date) DESC, p.display_name"""
    ).fetchall()
    project_names = [r["display_name"] for r in pr]

    # Weeks with any activity — newest first, friendly label
    week_rows = conn.execute(
        """SELECT DISTINCT date FROM events WHERE date != '' ORDER BY date DESC"""
    ).fetchall()
    seen_weeks: set[str] = set()
    week_opts: list[dict] = []
    for r in week_rows:
        iw = _iso_week_of(r["date"])
        if iw and iw not in seen_weeks:
            seen_weeks.add(iw)
            week_opts.append({"key": iw, "label": _friendly_week_label(iw)})

    # Months with activity
    seen_months: set[str] = set()
    month_opts: list[dict] = []
    for r in week_rows:
        m = _month_of(r["date"])
        if m and m not in seen_months:
            seen_months.add(m)
            month_opts.append({"key": m, "label": _friendly_month_label(m)})

    # Years with activity
    seen_years: set[str] = set()
    year_opts: list[dict] = []
    for r in week_rows:
        y = r["date"][:4] if len(r["date"]) >= 4 else ""
        if y and y not in seen_years:
            seen_years.add(y)
            year_opts.append({"key": y, "label": y})

    # Moods with activity — aggregate across all briefed days
    from collections import Counter
    mood_counts: Counter = Counter()
    for r in conn.execute("SELECT DISTINCT date FROM session_briefs WHERE date != ''").fetchall():
        lbl = _day_mood_label(conn, r["date"])
        if lbl:
            mood_counts[lbl] += 1
    mood_opts = [{"key": k, "label": k.replace("-", " ")} for k, _ in mood_counts.most_common()]

    # Tags: pulled from session_briefs JSON, aggregated per date, surfaced
    # both as a filter axis and annotated onto each entry's data-tags attr.
    tags_by_date, tag_counts = _tags_index(conn)
    tag_opts = [{"key": k, "label": k, "count": c} for k, c in tag_counts.most_common()]

    entries = _render_feed_pages(conn, dates, anchor_base="./", pid=None, tags_by_date=tags_by_date)
    body = render_feed(
        entries,
        site_title="ClaudeJournal",
        subtitle="a diary of what you built and what you learned",
        projects=project_names,
        weeks=week_opts,
        months=month_opts,
        moods=mood_opts,
        learnings=[{"key": "yes", "label": "Came clear"}, {"key": "no", "label": "In the fog"}],
        years=year_opts,
        tags=tag_opts,
    )
    (out_dir / "index.html").write_text(layout("Home", body, anchor_base="./"), encoding="utf-8")
    stats["index"] = 1

    # ---------- Per-project URLs: redirect stubs to home#axis=project&value=<name> ----------
    # Previously we rendered a full feed per project. The home-feed filter
    # now does that job, so we just emit lightweight redirects for back-compat
    # with any memory/external links people saved.
    import re as _re
    _SAFE_PROJECT = _re.compile(r"^[\w\-. ]+$")
    for r in pr:
        pname = r["display_name"]
        # Defensive: project names come from local Claude Code session paths
        # but we still treat them as untrusted before they hit the filesystem.
        # Reject anything that could escape out_dir/projects/ via traversal.
        if not pname or not _SAFE_PROJECT.match(pname) or ".." in pname:
            continue
        pdir = out_dir / "projects" / pname
        pdir.mkdir(parents=True, exist_ok=True)
        href = f"../../index.html#axis=project&value={pname}"
        (pdir / "index.html").write_text(
            f'<!doctype html><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0; url={esc(href)}">'
            f'<title>{esc(pname)}</title>'
            f'<p>Redirecting to <a href="{esc(href)}">{esc(pname)}</a>...</p>',
            encoding="utf-8",
        )
        stats["project_index"] += 1

    # ---------- Weekly retrospective standalone pages ----------
    weekly_rows = conn.execute(
        "SELECT key, date, prose FROM narrations WHERE scope='weekly' ORDER BY key DESC"
    ).fetchall()
    for wr in weekly_rows:
        iso_week, start, prose = wr["key"], wr["date"], wr["prose"]
        body_html = render_week_break(iso_week, prose, anchor_base="../")
        body = (
            f'<header class="site-head">'
            f'  <div class="crumb"><a href="../index.html">← back to journal</a></div>'
            f'  <h1>Week {esc(iso_week)}</h1>'
            f'  <div class="sub">starts {esc(start)}</div>'
            f'</header>{body_html}<footer>claudejournal</footer>'
        )
        (out_dir / "weekly" / f"{iso_week}.html").write_text(
            layout(f"Week {iso_week}", body, anchor_base="../"), encoding="utf-8"
        )
        stats["weekly"] += 1

    # ---------- Monthly retrospective standalone pages ----------
    monthly_rows = conn.execute(
        "SELECT key, date, prose FROM narrations WHERE scope='monthly' ORDER BY key DESC"
    ).fetchall()
    for mr in monthly_rows:
        ym, start, prose = mr["key"], mr["date"], mr["prose"]
        try:
            pretty = datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
        except ValueError:
            pretty = ym
        body_html = render_month_break(ym, prose, anchor_base="../")
        body = (
            f'<header class="site-head">'
            f'  <div class="crumb"><a href="../index.html">← back to journal</a></div>'
            f'  <h1>{esc(pretty)}</h1>'
            f'  <div class="sub">starts {esc(start)}</div>'
            f'</header>{body_html}<footer>claudejournal</footer>'
        )
        (out_dir / "monthly" / f"{ym}.html").write_text(
            layout(pretty, body, anchor_base="../"), encoding="utf-8"
        )
        stats["monthly"] += 1

    # ---------- Chat deep-link page (main chat is the floating bubble) ----------
    (out_dir / "chat.html").write_text(
        layout("Ask the journal", render_chat_page(), anchor_base="./"),
        encoding="utf-8",
    )

    # ---------- daily/*.html redirect stubs (back-compat for old [YYYY-MM-DD] links) ----------
    for date in dates:
        href = f"../index.html#{date}"
        html_page = (
            f'<!doctype html><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0; url={href}">'
            f'<title>{date}</title>'
            f'<p>Redirecting to <a href="{href}">{date}</a>...</p>'
        )
        (out_dir / "daily" / f"{date}.html").write_text(html_page, encoding="utf-8")
        stats["daily_redirect"] += 1

    conn.close()
    return stats
