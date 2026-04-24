"""Render SQLite state into a feed-style warm-diary HTML site.

Layout:
  out/index.html                              — main feed (all days, newest first)
  out/projects/<name>/index.html              — same feed scoped to one project
  out/projects/<name>/<YYYY-MM-DD>.html       — single project-day deep link
  out/weekly/<ISO-week>.html                  — weekly retrospective standalone
  out/monthly/<YYYY-MM>.html                  — monthly retrospective standalone
  out/docs/<id>.html                          — curated document summary + excerpt
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
    render_arc_page,
    render_chat_page,
    render_day_entry,
    render_doc_feed_entry,
    render_document_page,
    render_feed,
    render_month_break,
    render_topic_page,
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
            "SELECT prose, generated_at FROM narrations WHERE scope='project_day' AND date=? AND project_id=?",
            (date, pid),
        ).fetchone()
    else:
        nrow = conn.execute(
            "SELECT prose, generated_at FROM narrations WHERE scope='daily' AND date=?",
            (date,),
        ).fetchone()
    narration = nrow["prose"] if nrow else ""
    narration_generated_at = nrow["generated_at"] if nrow else ""

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
        SELECT b.session_id, b.brief_json, b.generated_at, p.display_name AS project_name
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
            b["_generated_at"] = br["generated_at"] or ""
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
        "narration_generated_at": narration_generated_at,
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


def _docs_by_date(conn: sqlite3.Connection,
                  pid: str | None = None) -> dict[str, list[dict]]:
    """Group documents (with their summary) by added_date. When `pid` is
    supplied, restrict to docs attached to that project."""
    rows = conn.execute(
        """SELECT d.id, d.title, d.original_filename, d.ext, d.user_note,
                  d.project_ids, d.tags, d.added_date, d.extracted_text,
                  n.prose AS summary_json
           FROM documents d
           LEFT JOIN narrations n
             ON n.scope='document' AND n.key=d.id
           ORDER BY d.added_date DESC, d.added_at DESC"""
    ).fetchall()
    # Resolve project ids to display names once — same lookup a few
    # callers need; worth passing through a small map.
    proj_names = {
        r["id"]: r["display_name"]
        for r in conn.execute("SELECT id, display_name FROM projects").fetchall()
    }
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        try:
            pids = json.loads(r["project_ids"] or "[]")
        except json.JSONDecodeError:
            pids = []
        if pid is not None and pid not in pids:
            continue
        try:
            tags = json.loads(r["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
        except json.JSONDecodeError:
            summary = {}
        doc = {
            "id": r["id"],
            "title": r["title"],
            "original_filename": r["original_filename"],
            "ext": r["ext"],
            "user_note": r["user_note"] or "",
            "added_date": r["added_date"],
            "extracted_text": r["extracted_text"] or "",
            "_project_names": [proj_names.get(p, p) for p in pids if p],
            "_tags_list": tags,
            "_summary": summary,
        }
        grouped.setdefault(r["added_date"] or "", []).append(doc)
    return grouped


def _render_feed_pages(conn: sqlite3.Connection, dates: list[str], anchor_base: str,
                       pid: str | None = None,
                       tags_by_date: dict[str, list[str]] | None = None,
                       known_topics: list[tuple[str, str]] | None = None) -> list[str]:
    """Produce the feed entries + week/month breaks interleaved, newest first."""
    weekly = _weekly_rollups(conn)
    monthly = _monthly_rollups(conn)
    docs_by_date = _docs_by_date(conn, pid=pid)
    # Flat list of (title, id) pairs used to linkify narration prose
    # wherever the narrator mentioned a doc by its exact title.
    known_docs: list[tuple[str, str]] = [
        (doc["title"] or doc.get("original_filename") or doc["id"], doc["id"])
        for doc_list in docs_by_date.values()
        for doc in doc_list
    ]
    known_topics = known_topics or []
    tags_by_date = tags_by_date or {}
    out: list[str] = []
    last_week: str | None = None
    last_month: str | None = None
    for date in dates:
        week = _iso_week_of(date)
        month = _month_of(date)
        # Week break when week changes
        if week != last_week and last_week is not None:
            out.append(render_week_break(last_week, weekly.get(last_week, ""),
                                         anchor_base, known_docs=known_docs,
                                         known_topics=known_topics))
        # Month break AFTER the week break (month is the bigger boundary —
        # visually it sits below the week break, so appears after in DOM order
        # given newest-first iteration).
        if month != last_month and last_month is not None:
            out.append(render_month_break(last_month, monthly.get(last_month, ""),
                                          anchor_base, known_docs=known_docs,
                                          known_topics=known_topics))
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
            narration_generated_at=bundle.get("narration_generated_at", ""),
            docs_added=docs_by_date.get(date, []),
            known_docs=known_docs,
            known_topics=known_topics,
        ))
        # Doc entries for this date — emitted right after the day so they
        # cluster chronologically. The Library view chip keeps them visible
        # independently of Daily/Weekly/Monthly toggles.
        for doc in docs_by_date.get(date, []):
            out.append(render_doc_feed_entry(doc, doc.get("_summary") or {},
                                             anchor_base=anchor_base))
    # Also emit docs for dates that had no briefs (e.g., a quiet reading day
    # with no sessions) — those dates won't appear in the main `dates` list
    # but their docs still deserve a home in the feed.
    date_set = set(dates)
    for d, doc_list in docs_by_date.items():
        if d and d not in date_set:
            for doc in doc_list:
                out.append(render_doc_feed_entry(doc, doc.get("_summary") or {},
                                                 anchor_base=anchor_base))
    if last_week and last_week in weekly:
        out.append(render_week_break(last_week, weekly[last_week], anchor_base,
                                     known_docs=known_docs, known_topics=known_topics))
    if last_month and last_month in monthly:
        out.append(render_month_break(last_month, monthly[last_month], anchor_base,
                                      known_docs=known_docs, known_topics=known_topics))
    return out


def render_site(db_path: Path, out_dir: Path, claude_home: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "daily").mkdir(exist_ok=True)
    (out_dir / "projects").mkdir(exist_ok=True)
    (out_dir / "weekly").mkdir(exist_ok=True)
    (out_dir / "monthly").mkdir(exist_ok=True)
    (out_dir / "docs").mkdir(exist_ok=True)

    conn = connect(db_path)
    stats = {"index": 0, "project_index": 0, "project_day": 0, "weekly": 0,
             "monthly": 0, "daily_redirect": 0, "docs": 0}
    # Global list of (title, id) pairs — used by every renderer that calls
    # link_doc_titles on narration prose. Cheaper than per-call queries.
    known_docs_all: list[tuple[str, str]] = [
        (r["title"] or r["original_filename"] or r["id"], r["id"])
        for r in conn.execute(
            "SELECT id, title, original_filename FROM documents"
        ).fetchall()
    ]

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

    # Topic pages data for Overview mode.  Build slug map now so the JS gets
    # the correct hrefs. Only tags with a generated prose page are included.
    from claudejournal.topics import build_slug_map, tags_with_enough_coverage
    _all_qualifying_tags = tags_with_enough_coverage(conn)
    _slug_map_global = build_slug_map(_all_qualifying_tags)
    _rendered_topic_tags = [
        r["key"] for r in conn.execute(
            "SELECT key FROM narrations WHERE scope='topic' AND prose IS NOT NULL AND prose != ''"
        ).fetchall()
    ]
    topic_pages_list = _rendered_topic_tags  # tags with pages
    topic_pages_map = {t: _slug_map_global.get(t, t) for t in _rendered_topic_tags}

    # Arc pages data: display names of projects with a project_arc narration.
    # Used by the JS Overview mode gate — the project filter pool uses display names.
    arc_pages_list = [
        r["display_name"] for r in conn.execute(
            """SELECT p.display_name FROM narrations n
               JOIN projects p ON p.id = n.key
               WHERE n.scope = 'project_arc' AND n.prose IS NOT NULL AND n.prose != ''
               ORDER BY p.display_name"""
        ).fetchall()
        if r["display_name"]
    ]

    # known_topics: (tag_name, slug) pairs for linkification in narration prose.
    # Only include tags that have generated pages to avoid dead links.
    known_topics_all: list[tuple[str, str]] = [
        (tag, _slug_map_global.get(tag, tag)) for tag in _rendered_topic_tags
    ]

    entries = _render_feed_pages(conn, dates, anchor_base="./", pid=None,
                                 tags_by_date=tags_by_date,
                                 known_topics=known_topics_all)
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
        topic_pages=topic_pages_list,
        topic_pages_map=topic_pages_map,
        arc_pages=arc_pages_list,
    )
    (out_dir / "index.html").write_text(layout("Home", body, anchor_base="./"), encoding="utf-8")
    stats["index"] = 1

    # ---------- Per-project pages: arc narrative OR redirect stub ----------
    # Projects with a project_arc narration get a real arc page. Others keep
    # the lightweight redirect stub for back-compat with saved links.
    import re as _re
    _SAFE_PROJECT = _re.compile(r"^[\w\-. ]+$")

    # Build a map of project_id -> arc narration for quick lookup.
    _arc_rows = conn.execute(
        """SELECT n.key AS project_id, n.prose, n.generated_at,
                  p.display_name
           FROM narrations n
           JOIN projects p ON p.id = n.key
           WHERE n.scope = 'project_arc' AND n.prose IS NOT NULL AND n.prose != ''"""
    ).fetchall()
    _arc_by_pid = {r["project_id"]: r for r in _arc_rows}

    # Project metadata: first/last date, session count, top tags
    for r in pr:
        pname = r["display_name"]
        pid = r["id"]
        # Defensive: project names come from local Claude Code session paths
        # but we still treat them as untrusted before they hit the filesystem.
        # Reject anything that could escape out_dir/projects/ via traversal.
        if not pname or not _SAFE_PROJECT.match(pname) or ".." in pname:
            continue
        pdir = out_dir / "projects" / pname
        pdir.mkdir(parents=True, exist_ok=True)

        arc_row = _arc_by_pid.get(pid)
        if arc_row:
            # Build metadata for the arc page header
            date_bounds = conn.execute(
                "SELECT MIN(date) as first_date, MAX(date) as last_date "
                "FROM events WHERE project_id = ? AND date != ''",
                (pid,),
            ).fetchone()
            session_count = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM session_briefs WHERE project_id = ?",
                (pid,),
            ).fetchone()[0]
            # Top tags across all briefs for this project
            top_tags_raw: dict[str, int] = {}
            for br in conn.execute(
                "SELECT brief_json FROM session_briefs WHERE project_id = ?", (pid,)
            ).fetchall():
                try:
                    b = json.loads(br["brief_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                for t in (b.get("tags") or []):
                    if isinstance(t, str) and t.strip():
                        top_tags_raw[t.strip().lower()] = top_tags_raw.get(t.strip().lower(), 0) + 1
            top_tags = [t for t, _ in sorted(top_tags_raw.items(), key=lambda x: -x[1])][:8]

            page_html = render_arc_page(
                pname, arc_row["prose"], anchor_base="../../",
                first_date=(date_bounds["first_date"] or "") if date_bounds else "",
                last_date=(date_bounds["last_date"] or "") if date_bounds else "",
                session_count=session_count,
                top_tags=top_tags,
                known_docs=known_docs_all,
                topic_slugs=_slug_map_global,
                generated_at=arc_row["generated_at"] or "",
            )
            body = (
                f'<header class="site-head">'
                f'  <div class="crumb"><a href="../../index.html">← back to journal</a></div>'
                f'</header>{page_html}<footer>claudejournal</footer>'
            )
            (pdir / "index.html").write_text(
                layout(pname, body, anchor_base="../../"), encoding="utf-8"
            )
            stats["project_arc"] = stats.get("project_arc", 0) + 1
        else:
            # No arc yet — keep redirect stub as graceful degradation.
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
        body_html = render_week_break(iso_week, prose, anchor_base="../",
                                      known_docs=known_docs_all,
                                      known_topics=known_topics_all)
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
        body_html = render_month_break(ym, prose, anchor_base="../",
                                       known_docs=known_docs_all,
                                       known_topics=known_topics_all)
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

    # ---------- Per-document summary pages ----------
    # Join documents to their narration row (scope='document') so the page
    # has the summary JSON to render. Orphan docs (no summary yet) still
    # get a page so the link from the list isn't a 404 — they just show a
    # placeholder until the pipeline catches up.
    doc_rows = conn.execute(
        """SELECT d.id, d.title, d.original_filename, d.ext, d.user_note,
                  d.project_ids, d.tags, d.added_date, d.extracted_text,
                  n.prose AS summary_json
           FROM documents d
           LEFT JOIN narrations n
             ON n.scope='document' AND n.key = d.id
           ORDER BY d.added_date DESC, d.added_at DESC"""
    ).fetchall()
    # Resolve project ids → display names for the meta line. One query up
    # front is cheaper than one per doc.
    proj_name_map = {
        r["id"]: r["display_name"]
        for r in conn.execute("SELECT id, display_name FROM projects").fetchall()
    }
    for dr in doc_rows:
        try:
            pids = json.loads(dr["project_ids"] or "[]")
        except json.JSONDecodeError:
            pids = []
        try:
            tags = json.loads(dr["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            summary = json.loads(dr["summary_json"]) if dr["summary_json"] else {}
        except json.JSONDecodeError:
            summary = {}
        doc = {
            "id": dr["id"],
            "title": dr["title"],
            "original_filename": dr["original_filename"],
            "ext": dr["ext"],
            "user_note": dr["user_note"] or "",
            "added_date": dr["added_date"],
            "extracted_text": dr["extracted_text"] or "",
            "_project_names": [proj_name_map.get(p, p) for p in pids if p],
            "_tags_list": tags,
        }
        pretty_title = doc["title"] or doc["original_filename"] or doc["id"]
        body_html = render_document_page(doc, summary, anchor_base="../")
        # Standalone page: just a back crumb + the doc article. The
        # article carries its own title, so no redundant <h1>.
        body = (
            f'<header class="site-head">'
            f'  <div class="crumb"><a href="../index.html">← back to journal</a></div>'
            f'</header>{body_html}<footer>claudejournal</footer>'
        )
        (out_dir / "docs" / f"{dr['id']}.html").write_text(
            layout(pretty_title, body, anchor_base="../"), encoding="utf-8"
        )
        stats["docs"] += 1

    # ---------- Topic wiki pages (out/topics/<slug>.html) ----------
    (out_dir / "topics").mkdir(exist_ok=True)
    topic_rows = conn.execute(
        "SELECT key, prose, generated_at FROM narrations WHERE scope='topic' "
        "AND prose IS NOT NULL AND prose != '' ORDER BY key ASC"
    ).fetchall()
    # Reuse slug map already built for the filter data above.
    slug_map = _slug_map_global
    # Reverse map for rendering: slug -> tag (for filenames we iterate topic rows)
    stats["topics"] = 0
    for tr in topic_rows:
        tag = tr["key"]
        prose = tr["prose"]
        slug = slug_map.get(tag) or tag
        gen_at = tr["generated_at"] or ""

        # Collect dates and projects for this tag by scanning session_briefs
        dates_set: set[str] = set()
        projs_set: set[str] = set()
        for r in conn.execute(
            """SELECT b.date, b.brief_json, p.display_name AS pname
               FROM session_briefs b JOIN projects p ON p.id = b.project_id
               WHERE b.date IS NOT NULL AND b.date != ''"""
        ).fetchall():
            try:
                brief = json.loads(r["brief_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            tags_in_brief = [t.strip().lower() for t in (brief.get("tags") or [])
                             if isinstance(t, str)]
            if tag in tags_in_brief:
                dates_set.add(r["date"])
                projs_set.add(r["pname"])

        page_html = render_topic_page(
            tag, prose, anchor_base="../",
            dates=sorted(dates_set),
            projects=sorted(projs_set),
            known_docs=known_docs_all,
            topic_slugs=slug_map,
            slug=slug,
            generated_at=gen_at,
        )
        body = (
            f'<header class="site-head">'
            f'  <div class="crumb"><a href="../index.html">← back to journal</a></div>'
            f'</header>{page_html}<footer>claudejournal</footer>'
        )
        (out_dir / "topics" / f"{slug}.html").write_text(
            layout(tag.replace("-", " ").title(), body, anchor_base="../"),
            encoding="utf-8",
        )
        stats["topics"] += 1

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
