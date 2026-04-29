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
  out/loops.html                              — open loops standing page (Phase B)
  out/learnings.html                          — learnings aggregation standing page (Phase B)
  out/echoes.html                             — temporal recall standing page (Phase D)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from claudejournal import interludes as interludemod
from claudejournal.backlinks import get_backlinks
from claudejournal.db import connect
from claudejournal.post_process import (
    extract_anchor_pairs,
    extract_doc_link_pairs,
    extract_topic_link_pairs,
)
from claudejournal.templates import (
    esc,
    layout,
    render_arc_page,
    render_chat_page,
    render_connections_page,
    render_day_entry,
    render_doc_feed_entry,
    render_document_page,
    render_echoes_page,
    render_entity_profile_page,
    render_feed,
    render_graph_page,
    render_learnings_page,
    render_loops_page,
    render_month_break,
    render_site_header,
    render_topic_page,
    render_week_break,
)
from claudejournal.connections import (
    compute_cross_project_connections as _compute_cross_project_connections,
    compute_all_daily_connections as _compute_all_daily_connections,
    compute_connections_graph as _compute_connections_graph,
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
                       known_topics: list[tuple[str, str]] | None = None,
                       open_loops_by_project: dict[str, int] | None = None,
                       open_loops_items_by_project: dict[str, list[dict]] | None = None,
                       entities_by_date: dict[str, list[str]] | None = None,
                       echoes_by_date: dict[str, dict] | None = None,
                       annotations_by_date: dict[str, list[dict]] | None = None,
                       daily_connections_by_date: dict[str, list[dict]] | None = None) -> list[str]:
    """Produce the feed entries + week/month breaks interleaved, newest first.

    open_loops_by_project: {project_id: count_of_open_loops_older_than_7d}
      Pre-computed in render_site() from compute_open_loops(). Used to render
      the open-loops banner on daily entries.
    entities_by_date: {date: [canonical_name, ...]} pre-built in render_site()
      from brief_entities. Each canonical_name becomes a token in the entry's
      data-entities attribute so the JS entity filter axis can match it.
    echoes_by_date: {date: echoes_dict} pre-built in render_site() from
      temporal.compute_all_echoes(). Passed to render_day_entry() so that days
      with temporal signals show the subtle echo banner. Dates absent from the
      dict have no echoes and render no banner.
    annotations_by_date: {date: [annotation_dict, ...]} pre-built in render_site()
      from the annotations table (scope='daily'). Each annotation may have a
      _contradiction flag set by the render-time contradiction guard (E6).
    daily_connections_by_date: {date: [nudge_dict, ...]} pre-built in render_site()
      from compute_all_daily_connections(). Passed to render_day_entry() so that
      days with cross-project entity/tag history show a connections chip. Dates
      absent from the dict have no cross-project signals and render no chip.
    """
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
    open_loops_by_project = open_loops_by_project or {}
    open_loops_items_by_project = open_loops_items_by_project or {}
    entities_by_date = entities_by_date or {}
    echoes_by_date = echoes_by_date or {}
    annotations_by_date = annotations_by_date or {}
    daily_connections_by_date = daily_connections_by_date or {}

    # Build a map of date -> project_ids active that day for banner lookup.
    # We use the events table since that's already available without extra queries.
    date_project_ids: dict[str, list[str]] = {}
    if open_loops_by_project:
        rows = conn.execute(
            "SELECT DISTINCT date, project_id FROM events WHERE date != '' ORDER BY date"
        ).fetchall()
        for r in rows:
            date_project_ids.setdefault(r["date"], []).append(r["project_id"])

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

        # Open loops banner: sum loops from all projects active this day
        # (only show when at least one loop is > 7 days old — the threshold
        # is already baked into open_loops_by_project which was built from
        # loops with age_days >= 7).
        day_pids = date_project_ids.get(date, [])
        day_open_loops = sum(open_loops_by_project.get(p, 0) for p in day_pids)
        # Collect the actual loop items for this day (deduplicated across
        # projects; a single loop only appears once even if its project_id
        # is hit multiple times). Sorted oldest-first so the most-stale
        # frictions surface first when the chip panel opens.
        day_loop_items: list[dict] = []
        seen_loop_ids: set[tuple[str, str, str]] = set()
        for p in day_pids:
            for loop in open_loops_items_by_project.get(p, []):
                k = (loop.get("date", ""), loop.get("project_id", ""), loop.get("friction", ""))
                if k in seen_loop_ids:
                    continue
                seen_loop_ids.add(k)
                day_loop_items.append(loop)
        day_loop_items.sort(key=lambda l: (l.get("date", ""), l.get("project_name", "")))

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
            open_loops_count=day_open_loops,
            open_loops_items=day_loop_items,
            entities=entities_by_date.get(date, []),
            echoes=echoes_by_date.get(date),
            annotations=annotations_by_date.get(date, []),
            daily_connections=daily_connections_by_date.get(date),
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


_LINKS_HASH_KEY = "links_input_hash"


def _compute_links_input_hash(conn: sqlite3.Connection,
                              known_docs: list[tuple[str, str]],
                              known_topics: list[tuple[str, str]]) -> str:
    """Stable digest covering everything _rebuild_links() depends on.

    Inputs:
      * every narration row's (scope, key, input_hash) — we use the
        existing per-narration content hash rather than re-hashing prose,
        so this is O(rows) lookups not O(prose-bytes)
      * the docs list (id + title pairs) since linkifier behaviour
        depends on it
      * the topics list (tag slug + display) for the same reason
      * brief_entities contents — entity→arc link type (Phase B) depends
        on which entities appear in which project's briefs; if brief_entities
        changes, entity_arc links must be rebuilt too.

    Returns a hex string. Equal hash → no link-graph change since last
    render → safe to skip the rebuild.
    """
    h = hashlib.sha256()
    rows = conn.execute(
        "SELECT scope, key, input_hash FROM narrations "
        "WHERE prose IS NOT NULL AND prose != '' "
        "ORDER BY scope, key"
    ).fetchall()
    for r in rows:
        h.update(b"\x01")
        h.update((r["scope"] or "").encode("utf-8"))
        h.update(b"\x02")
        h.update((r["key"] or "").encode("utf-8"))
        h.update(b"\x03")
        h.update((r["input_hash"] or "").encode("utf-8"))
    h.update(b"\x04docs\x04")
    for doc_id, title in sorted(known_docs):
        h.update((doc_id or "").encode("utf-8"))
        h.update(b"|")
        h.update((title or "").encode("utf-8"))
        h.update(b"\x05")
    h.update(b"\x06topics\x06")
    for slug, label in sorted(known_topics):
        h.update((slug or "").encode("utf-8"))
        h.update(b"|")
        h.update((label or "").encode("utf-8"))
        h.update(b"\x07")
    # Phase B: include brief_entities summary so entity_arc link changes
    # are detected. Use (entity_id, project_id, date) tuples — cheap.
    h.update(b"\x08entities\x08")
    try:
        be_rows = conn.execute(
            """SELECT be.entity_id, sb.project_id, be.date
               FROM brief_entities be
               JOIN session_briefs sb
                 ON sb.session_id = be.session_id AND sb.date = be.date
               WHERE be.date != ''
               ORDER BY be.entity_id, sb.project_id, be.date"""
        ).fetchall()
        for br in be_rows:
            h.update((br["entity_id"] or "").encode("utf-8"))
            h.update(b"|")
            h.update((br["project_id"] or "").encode("utf-8"))
            h.update(b"|")
            h.update((br["date"] or "").encode("utf-8"))
            h.update(b"\x09")
    except Exception:
        pass  # brief_entities may not exist on very old DBs
    return h.hexdigest()[:32]


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None
    except sqlite3.OperationalError:
        # meta table may not exist on older DBs — caller treats as cache miss
        return None


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    try:
        conn.execute(
            """INSERT INTO meta (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )
    except sqlite3.OperationalError:
        # meta table missing — silently skip; the rebuild itself still ran
        pass


def _rebuild_links(conn: sqlite3.Connection,
                   known_docs: list[tuple[str, str]],
                   known_topics: list[tuple[str, str]]) -> int:
    """Rebuild the materialized `links` table from all narration prose.

    Called once per render_site() invocation. Skips the rebuild when the
    cached input hash matches — narration content + the docs/topics
    lists are the only things that affect link extraction, so an unchanged
    hash means the existing table is still authoritative.

    On hash mismatch (or first run / table missing), the table is
    truncated and re-populated from a fresh extraction pass over every
    narration row. Returns the number of link rows currently in the table
    (whether freshly written or kept from the cached run).
    """
    fresh_hash = _compute_links_input_hash(conn, known_docs, known_topics)
    cached_hash = _read_meta(conn, _LINKS_HASH_KEY)

    if cached_hash == fresh_hash:
        # Inputs unchanged — keep the existing table. Cheap path: just
        # report the row count so callers/stats get an accurate number.
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            # links table missing — fall through to full rebuild
            pass

    conn.execute("DELETE FROM links")
    rows_written = 0
    # Fetch all narration rows that have prose
    narration_rows = conn.execute(
        "SELECT scope, key, prose FROM narrations WHERE prose IS NOT NULL AND prose != ''"
    ).fetchall()

    inserts: list[tuple[str, str, str, str, str]] = []

    for nr in narration_rows:
        scope: str = nr["scope"]
        key: str = nr["key"]
        prose: str = nr["prose"]

        # 1) Date anchors -> target_scope='daily'
        for date in extract_anchor_pairs(prose):
            inserts.append((scope, key, "daily", date, "date_anchor"))

        # 2) Doc title links -> target_scope='document'
        for doc_id in extract_doc_link_pairs(prose, known_docs):
            inserts.append((scope, key, "document", doc_id, "doc_title"))

        # 3) Topic title links -> target_scope='topic'
        for slug in extract_topic_link_pairs(prose, known_topics):
            inserts.append((scope, key, "topic", slug, "topic_title"))

    if inserts:
        conn.executemany(
            "INSERT OR IGNORE INTO links "
            "(source_scope, source_key, target_scope, target_key, link_type) "
            "VALUES (?, ?, ?, ?, ?)",
            inserts,
        )
        rows_written = len(inserts)

    _write_meta(conn, _LINKS_HASH_KEY, fresh_hash)
    conn.commit()
    return rows_written


def _write_graph_json(conn: sqlite3.Connection, out_dir: Path,
                      slug_map: dict[str, str] | None = None,
                      entity_slug_map: dict[str, str] | None = None) -> None:
    """Serialize the materialized `links` table as a D3-compatible nodes+edges
    JSON file written to out_dir/graph.json.

    Node format:  {id, scope, label, url}
    Edge format:  {source, target, type}

    Node ids use the compound '{scope}:{key}' string to ensure uniqueness
    across scopes (a topic slug might collide with a date key otherwise).
    """
    slug_map = slug_map or {}

    link_rows = conn.execute(
        "SELECT source_scope, source_key, target_scope, target_key, link_type FROM links"
    ).fetchall()

    nodes_map: dict[str, dict] = {}
    edges: list[dict] = []

    def _node_id(scope: str, key: str) -> str:
        return f"{scope}:{key}"

    _entity_slug_map = entity_slug_map or {}

    def _node_url(scope: str, key: str) -> str:
        """Return a root-relative URL suitable for the graph page (which lives
        at out/graph.html, one level below the site root)."""
        if scope == "daily":
            return f"./index.html#{key}"
        if scope == "project_day":
            parts = key.split("|", 1)
            date = parts[1] if len(parts) == 2 else key
            return f"./index.html#{date}"
        if scope == "weekly":
            return f"./weekly/{key}.html"
        if scope == "monthly":
            return f"./monthly/{key}.html"
        if scope == "topic":
            return f"./topics/{key}.html"
        if scope == "project_arc":
            return f"./projects/{key}/index.html"
        if scope == "document":
            return f"./docs/{key}.html"
        if scope == "entity_profile":
            # key is the entity slug
            return f"./entities/{key}.html"
        return "./index.html"

    def _node_label(scope: str, key: str) -> str:
        if scope == "daily":
            try:
                return datetime.strptime(key, "%Y-%m-%d").strftime("%b %-d, %Y")
            except (ValueError, TypeError):
                try:
                    return datetime.strptime(key, "%Y-%m-%d").strftime("%b %d, %Y")
                except Exception:
                    return key
        if scope == "weekly":
            return f"Week {key}"
        if scope == "monthly":
            try:
                return datetime.strptime(key, "%Y-%m").strftime("%B %Y")
            except Exception:
                return key
        if scope == "topic":
            return key.replace("-", " ").title()
        if scope == "project_arc":
            # Resolve project_id to display_name via DB
            row = conn.execute(
                "SELECT display_name FROM projects WHERE id = ?", (key,)
            ).fetchone()
            return row["display_name"] if row else key
        if scope == "document":
            row = conn.execute(
                "SELECT title, original_filename FROM documents WHERE id = ?", (key,)
            ).fetchone()
            if row:
                return row["title"] or row["original_filename"] or key
            return key
        if scope == "entity_profile":
            # key is the entity slug; resolve to entity name via entities table
            row = conn.execute(
                "SELECT name FROM entities WHERE canonical_name = ? OR name = ?", (key, key)
            ).fetchone()
            if row:
                return row["name"]
            return key.replace("-", " ").title()
        return key

    # Resolve project_id → display_name once so we don't re-query per node.
    project_display: dict[str, str] = {
        r["id"]: r["display_name"] for r in conn.execute(
            "SELECT id, display_name FROM projects"
        ).fetchall()
    }

    def _iso_week_for(date_str: str) -> str | None:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            iy, iw, _ = d.isocalendar()
            return f"{iy}-W{iw:02d}"
        except Exception:
            return None

    def _node_filters(scope: str, key: str) -> dict:
        """Filter metadata for client-side graph filtering. Each node carries
        whichever of {project, topic, year, month, week, entity} apply, so
        the client can match against the chip widget's axis values without
        re-parsing IDs.
        """
        f: dict[str, str | list[str]] = {}
        if scope == "daily":
            f["year"] = key[:4]
            f["month"] = key[:7]
            w = _iso_week_for(key)
            if w:
                f["week"] = w
        elif scope == "project_day":
            parts = key.split("|", 1)
            if len(parts) == 2:
                pid, date = parts
                f["project"] = project_display.get(pid, pid)
                f["year"] = date[:4]
                f["month"] = date[:7]
                w = _iso_week_for(date)
                if w:
                    f["week"] = w
        elif scope == "project_arc":
            f["project"] = project_display.get(key, key)
        elif scope == "topic":
            f["topic"] = key
        elif scope == "weekly":
            f["week"] = key
            # Best-effort year extraction from "YYYY-Www"
            if len(key) >= 4:
                f["year"] = key[:4]
        elif scope == "monthly":
            f["month"] = key
            f["year"] = key[:4]
        elif scope == "entity_profile":
            # entity_profile nodes carry the entity slug as key
            f["entity"] = key
        return f

    for lr in link_rows:
        for scope, key in [
            (lr["source_scope"], lr["source_key"]),
            (lr["target_scope"], lr["target_key"]),
        ]:
            nid = _node_id(scope, key)
            if nid not in nodes_map:
                node = {
                    "id": nid,
                    "scope": scope,
                    "label": _node_label(scope, key),
                    "url": _node_url(scope, key),
                }
                node.update(_node_filters(scope, key))
                nodes_map[nid] = node
        edges.append({
            "source": _node_id(lr["source_scope"], lr["source_key"]),
            "target": _node_id(lr["target_scope"], lr["target_key"]),
            "type": lr["link_type"],
        })

    graph = {"nodes": list(nodes_map.values()), "edges": edges}
    (out_dir / "graph.json").write_text(
        json.dumps(graph, separators=(",", ":")), encoding="utf-8"
    )


def _add_entity_arc_links(
    conn: sqlite3.Connection,
    qualifying_entities: list[dict],
    entity_slug_map: dict[str, str],
) -> int:
    """Add entity_profile → project_arc link rows to the materialized links table.

    For each qualifying entity, emits one link per project the entity appears in:
      source: (entity_profile, entity_slug) → target: (project_arc, project_id)
      link_type: 'entity_arc'

    This makes entities first-class nodes in the link graph alongside topics,
    docs, and arcs. The entity_slug_map provides canonical_name → slug mapping.

    Returns the number of new rows inserted.
    """
    if not qualifying_entities or not entity_slug_map:
        return 0

    # Delete existing entity_arc rows first (idempotent rebuild).
    conn.execute("DELETE FROM links WHERE link_type = 'entity_arc'")

    inserts: list[tuple[str, str, str, str, str]] = []

    for ent in qualifying_entities:
        eid = ent["entity_id"]
        cname = ent.get("canonical_name") or ent.get("entity_name", "")
        slug = entity_slug_map.get(cname)
        if not slug:
            continue
        # Find all project_ids this entity appears in
        proj_rows = conn.execute(
            """SELECT DISTINCT sb.project_id
               FROM brief_entities be
               JOIN session_briefs sb
                 ON sb.session_id = be.session_id AND sb.date = be.date
               WHERE be.entity_id = ? AND be.date != ''""",
            (eid,),
        ).fetchall()
        for pr in proj_rows:
            inserts.append((
                "entity_profile", slug,
                "project_arc", pr["project_id"],
                "entity_arc",
            ))

    if inserts:
        conn.executemany(
            "INSERT OR IGNORE INTO links "
            "(source_scope, source_key, target_scope, target_key, link_type) "
            "VALUES (?, ?, ?, ?, ?)",
            inserts,
        )
        conn.commit()

    return len(inserts)


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

    # Entities: pulled from brief_entities/entities tables, sorted by day-count
    # descending.  entity_opts is the flat JS filter pool; entities_by_date
    # maps each date to a list of entity dicts {key, label, type} for both
    # the data-entities attr (uses 'key' = canonical_name) and the inspect chip.
    from claudejournal.entities import get_all_entities_with_counts as _get_entity_counts
    from claudejournal.topics import _safe_slug as _entity_safe_slug
    from claudejournal.entity_pages import qualifying_entities as _qualifying_entities_fn
    _raw_entity_list = _get_entity_counts(conn)
    # Build the qualifying entity set for slug URL generation.
    # These are entities that will get profile pages — only those get a url.
    _qualifying_entity_rows = _qualifying_entities_fn(conn)
    _qualifying_cnames: set[str] = {
        row.get("canonical_name") or row.get("entity_name", "")
        for row in _qualifying_entity_rows
    }
    # Pre-compute entity_slug_map here (canonical_name -> slug) so arc pages
    # can link entity names in "Related work" sections to profile pages.
    # This is purely a computation; no files are written at this point.
    _entity_slug_map_global: dict[str, str] = {
        cname: _entity_safe_slug(cname)
        for cname in _qualifying_cnames
        if cname
    }
    entity_opts = []
    for e in _raw_entity_list:
        cname = e["canonical_name"]
        opt: dict = {"key": cname, "label": e["name"], "type": e["type"]}
        if cname in _qualifying_cnames:
            opt["url"] = f"entities/{_entity_safe_slug(cname)}.html"
        entity_opts.append(opt)
    # Build {date: [{key, label, type, url?}, ...]} for entry annotation and inspect chip.
    # One query fetches all (date, entity) pairs; we fan out to dicts using the
    # entity_opts lookup map (keyed by canonical_name).
    entities_by_date: dict[str, list[dict]] = {}
    if entity_opts:
        _ent_opt_map = {e["key"]: e for e in entity_opts}
        _entity_date_rows = conn.execute(
            """SELECT DISTINCT be.date, e.canonical_name, e.name, e.type
               FROM brief_entities be
               JOIN entities e ON e.id = be.entity_id
               WHERE be.date != ''
               ORDER BY be.date, e.type, e.name"""
        ).fetchall()
        for _r in _entity_date_rows:
            _opt = _ent_opt_map.get(_r["canonical_name"], {})
            _ent_dict: dict = {
                "key": _r["canonical_name"],
                "label": _r["name"],
                "type": _r["type"],
            }
            if "url" in _opt:
                _ent_dict["url"] = _opt["url"]
            entities_by_date.setdefault(_r["date"], []).append(_ent_dict)

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

    # ---------- Rebuild materialized link graph ----------
    # Run early so backlinks are available when rendering topic/arc/doc pages.
    # Narration prose is written by the narrate stage BEFORE render runs, so
    # the DB already has the current prose when we arrive here.  The table is
    # truncated + rebuilt, making this idempotent.
    link_count = _rebuild_links(conn, known_docs_all, known_topics_all)
    stats["links"] = link_count

    # ---------- Pre-compute open loops for feed banners ----------
    # Build per-project: a count (used by the open-loops chip badge) AND
    # the actual loop dicts (used by the chip panel to list this day's
    # frictions inline). Pure text ops; both maps are tiny.
    from claudejournal.openloops import compute_open_loops as _compute_open_loops
    _all_open_loops = _compute_open_loops(conn)
    _open_loops_by_project: dict[str, int] = {}
    _open_loops_items_by_project: dict[str, list[dict]] = {}
    for _loop in _all_open_loops:
        if _loop["age_days"] >= 7:
            pid = _loop["project_id"]
            _open_loops_by_project[pid] = _open_loops_by_project.get(pid, 0) + 1
            _open_loops_items_by_project.setdefault(pid, []).append(_loop)

    # ---------- Pre-compute temporal echoes for feed banners ----------
    # compute_all_echoes() does a single pass over all brief/narration data;
    # the result dict only contains dates that have at least one echo signal.
    # Per-day lookup is a simple dict.get() — zero overhead for days with
    # no echoes, which is the common case for recent dates.
    from claudejournal.temporal import compute_all_echoes as _compute_all_echoes
    _echoes_by_date = _compute_all_echoes(conn, dates)
    stats["echoes"] = len(_echoes_by_date)

    # ---------- Pre-compute cross-project connections (Phase A) ----------
    # Two passes, both O(n) over the corpus:
    #   _connections_by_project: {project_id: [connection_dict, ...]}
    #     — passed to render_arc_page() so arc pages show "Related work" section.
    #   _daily_connections_by_date: {date: [nudge_dict, ...]}
    #     — passed to render_day_entry() so daily entries show connections chip.
    # Both computations share the same internal entity/tag maps built once per
    # render; capping at 3 nudges per day keeps the per-entry rendering O(1).
    _connections_by_project = _compute_cross_project_connections(conn)
    _daily_connections_by_date = _compute_all_daily_connections(conn, dates)
    stats["connections"] = sum(
        len(v) for v in _connections_by_project.values()
    )
    stats["connections_dates"] = len(_daily_connections_by_date)

    # ---------- Pre-compute annotations for feed entries (Phase E) ----------
    # Load all 'daily' scope annotations once; group by target_key (date).
    # Apply the render-time contradiction guard: for 'correction' annotations,
    # flag _contradiction=True when the annotation's significant words are
    # absent from the current narration prose (advisory warning only).
    _annotations_by_date: dict[str, list[dict]] = {}
    try:
        _ann_rows = conn.execute(
            """SELECT id, target_scope, target_key, annotation_type, text,
                      created_at, updated_at, pin_priority, scope_tag
               FROM annotations
               WHERE target_scope = 'daily'
               ORDER BY pin_priority DESC, id ASC"""
        ).fetchall()
        for _r in _ann_rows:
            _ann = dict(_r)
            _ann["_contradiction"] = False
            _annotations_by_date.setdefault(_r["target_key"], []).append(_ann)
    except Exception:
        pass  # annotations table may not exist yet on old DBs

    # Contradiction guard: for correction-type annotations, check keyword overlap
    # with the narration prose for the same date. If the significant words in the
    # correction do not appear in the prose, flag _contradiction=True.
    # This is advisory (not blocking) — implemented simply with word-set overlap.
    def _sig_words(text: str) -> set[str]:
        import re as _re
        _STOP = frozenset({
            "the", "and", "that", "this", "with", "from", "have", "been", "were",
            "when", "what", "which", "where", "while", "also", "into", "then",
            "than", "they", "them", "their", "some", "just", "more", "very",
            "will", "would", "could", "should", "does", "doing", "done", "used",
            "using", "make", "made", "need", "needs", "needed", "for", "are",
            "was", "had", "has", "its", "all", "out", "one", "but", "not",
        })
        tokens = _re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", text.lower())
        return {t for t in tokens if len(t) >= 4 and t not in _STOP}

    for _date, _ann_list in _annotations_by_date.items():
        # Load narration prose for this date
        _narr_row = conn.execute(
            "SELECT prose FROM narrations WHERE scope='daily' AND key=?", (_date,)
        ).fetchone()
        _narr_prose = (_narr_row["prose"] if _narr_row else "") or ""
        _prose_words = _sig_words(_narr_prose)
        for _ann in _ann_list:
            if _ann.get("annotation_type") == "correction" and _narr_prose:
                _ann_words = _sig_words(_ann.get("text", ""))
                if _ann_words:
                    # If fewer than half the annotation's significant words appear
                    # in the prose, flag as potentially contradicting.
                    _overlap_frac = len(_ann_words & _prose_words) / len(_ann_words)
                    _ann["_contradiction"] = _overlap_frac < 0.5
    stats["annotations"] = sum(len(v) for v in _annotations_by_date.values())

    # ---------- Phase E v2: annotations for topic/arc/weekly/monthly scopes ----------
    # Load annotations for all non-daily scopes, apply the same contradiction guard,
    # and store in per-scope dicts for injection into the respective page renderers.
    _annotations_by_topic: dict[str, list[dict]] = {}
    _annotations_by_arc: dict[str, list[dict]] = {}
    _annotations_by_week: dict[str, list[dict]] = {}
    _annotations_by_month: dict[str, list[dict]] = {}
    try:
        _scope_ann_rows = conn.execute(
            """SELECT id, target_scope, target_key, annotation_type, text,
                      created_at, updated_at, pin_priority, scope_tag
               FROM annotations
               WHERE target_scope IN ('topic', 'project_arc', 'weekly', 'monthly')
               ORDER BY pin_priority DESC, id ASC"""
        ).fetchall()
        _scope_buckets = {
            "topic": _annotations_by_topic,
            "project_arc": _annotations_by_arc,
            "weekly": _annotations_by_week,
            "monthly": _annotations_by_month,
        }
        for _r in _scope_ann_rows:
            _ann = dict(_r)
            _ann["_contradiction"] = False
            _scope_buckets[_r["target_scope"]].setdefault(_r["target_key"], []).append(_ann)
    except Exception:
        pass  # annotations table may not exist yet on old DBs

    # Contradiction guard for non-daily scopes: same word-overlap heuristic.
    # scope -> (narration_scope, dict_to_check)
    _scope_guard_map = [
        ("topic", "topic", _annotations_by_topic),
        ("project_arc", "project_arc", _annotations_by_arc),
        ("weekly", "weekly", _annotations_by_week),
        ("monthly", "monthly", _annotations_by_month),
    ]
    for _scope_label, _narr_scope, _ann_dict in _scope_guard_map:
        for _key, _ann_list in _ann_dict.items():
            _narr_row = conn.execute(
                "SELECT prose FROM narrations WHERE scope=? AND key=?",
                (_narr_scope, _key),
            ).fetchone()
            _narr_prose = (_narr_row["prose"] if _narr_row else "") or ""
            _prose_words = _sig_words(_narr_prose)
            for _ann in _ann_list:
                if _ann.get("annotation_type") == "correction" and _narr_prose:
                    _ann_words = _sig_words(_ann.get("text", ""))
                    if _ann_words:
                        _overlap_frac = len(_ann_words & _prose_words) / len(_ann_words)
                        _ann["_contradiction"] = _overlap_frac < 0.5
    stats["annotations"] += sum(
        sum(len(v) for v in d.values())
        for d in (_annotations_by_topic, _annotations_by_arc,
                  _annotations_by_week, _annotations_by_month)
    )

    # ---------- Render-time fallback: ensure every events-day has prose ----------
    # The pipeline's interlude stage normally fills gaps before render runs,
    # but rollovers (a fresh day with activity but no pipeline run yet) can
    # leave a date with events, no narration, AND no interlude — which
    # surfaces the generic "A short day…" placeholder in the feed.
    #
    # We close that hole here: any date with events that has neither a
    # daily narration nor an interlude triggers an interlude generation
    # synchronously before _render_feed_pages emits the page. The interlude
    # module itself decides what form to use (haiku, limerick, etc.) and
    # persists the row, so subsequent renders skip it. If narrate later
    # produces a real narration, render_day_entry naturally prefers it
    # over the interlude — the interlude is non-blocking by design.
    #
    # Cost: at most one haiku call per affected day per render. In a
    # healthy system that's 0–1 calls; the placeholder is genuinely
    # unreachable in normal operation. A failure in interlude generation
    # is non-fatal — render falls through to the placeholder text the
    # same as before.
    try:
        from claudejournal.config import load as _load_cfg
        _cfg = _load_cfg()
        if getattr(_cfg, "interludes_enabled", True):
            _gap_dates = [
                _r["date"] for _r in conn.execute(
                    """SELECT DISTINCT e.date FROM events e
                       LEFT JOIN narrations n
                         ON n.scope='daily' AND n.date = e.date
                       LEFT JOIN interludes i ON i.date = e.date
                       WHERE e.date != ''
                         AND n.prose IS NULL
                         AND i.prose IS NULL
                       ORDER BY e.date DESC"""
                ).fetchall()
            ]
            if _gap_dates:
                _interlude_made = 0
                for _gd in _gap_dates:
                    try:
                        _s = interludemod.run(
                            _cfg, date=_gd, force=False, verbose=False,
                        )
                        _interlude_made += _s.get("generated", 0)
                    except Exception:
                        # Generation failure for one date doesn't block render.
                        pass
                stats["render_time_interludes"] = _interlude_made
    except Exception:
        # Config load or any other unexpected failure: silently fall through.
        # The placeholder text in render_day_entry is the same fallback the
        # system used before this hook existed.
        pass

    entries = _render_feed_pages(conn, dates, anchor_base="./", pid=None,
                                 tags_by_date=tags_by_date,
                                 known_topics=known_topics_all,
                                 open_loops_by_project=_open_loops_by_project,
                                 open_loops_items_by_project=_open_loops_items_by_project,
                                 entities_by_date=entities_by_date,
                                 echoes_by_date=_echoes_by_date,
                                 annotations_by_date=_annotations_by_date,
                                 daily_connections_by_date=_daily_connections_by_date)
    # Shared filter-data bundle — the main feed AND every standalone
    # deep-link page (weekly/monthly/topic/arc/doc) uses the same chip
    # bar, so they all pass the same filter data into render_site_header.
    # Each call just swaps the site_title/subtitle to contextualize.
    filter_data = dict(
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
        entities=entity_opts,
    )
    body = render_feed(
        entries,
        site_title="ClaudeJournal",
        subtitle="a diary of what you built and what you learned",
        **filter_data,
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

            arc_backlinks = get_backlinks(conn, "project_arc", pid, anchor_base="../../")
            page_html = render_arc_page(
                pname, arc_row["prose"], anchor_base="../../",
                first_date=(date_bounds["first_date"] or "") if date_bounds else "",
                last_date=(date_bounds["last_date"] or "") if date_bounds else "",
                session_count=session_count,
                top_tags=top_tags,
                known_docs=known_docs_all,
                topic_slugs=_slug_map_global,
                generated_at=arc_row["generated_at"] or "",
                backlinks=arc_backlinks,
                annotations=_annotations_by_arc.get(pid, []),
                connections=_connections_by_project.get(pid, []),
                entity_slug_map=_entity_slug_map_global,
            )
            header = render_site_header(
                site_title="ClaudeJournal",
                subtitle=f"Project · {pname}",
                **filter_data,
            )
            body = header + page_html + "<footer>claudejournal</footer>"
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
                                      known_topics=known_topics_all,
                                      annotations=_annotations_by_week.get(iso_week, []))
        # Full site header — chip bar is the navigation, no back-crumb.
        header = render_site_header(
            site_title="ClaudeJournal",
            subtitle=f"Week {iso_week} · starts {start}",
            **filter_data,
        )
        body = header + body_html + "<footer>claudejournal</footer>"
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
                                       known_topics=known_topics_all,
                                       annotations=_annotations_by_month.get(ym, []))
        header = render_site_header(
            site_title="ClaudeJournal",
            subtitle=f"{pretty} · starts {start}",
            **filter_data,
        )
        body = header + body_html + "<footer>claudejournal</footer>"
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
        doc_backlinks = get_backlinks(conn, "document", dr["id"], anchor_base="../")
        body_html = render_document_page(doc, summary, anchor_base="../",
                                         backlinks=doc_backlinks)
        header = render_site_header(
            site_title="ClaudeJournal",
            subtitle=f"Document · {pretty_title}",
            **filter_data,
        )
        body = header + body_html + "<footer>claudejournal</footer>"
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

        topic_backlinks = get_backlinks(conn, "topic", slug, anchor_base="../")
        page_html = render_topic_page(
            tag, prose, anchor_base="../",
            dates=sorted(dates_set),
            projects=sorted(projs_set),
            known_docs=known_docs_all,
            topic_slugs=slug_map,
            slug=slug,
            generated_at=gen_at,
            backlinks=topic_backlinks,
            annotations=_annotations_by_topic.get(tag, []),
        )
        pretty_tag = tag.replace("-", " ").title()
        header = render_site_header(
            site_title="ClaudeJournal",
            subtitle=f"Topic · {pretty_tag}",
            **filter_data,
        )
        body = header + page_html + "<footer>claudejournal</footer>"
        (out_dir / "topics" / f"{slug}.html").write_text(
            layout(pretty_tag, body, anchor_base="../"),
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

    # graph.json + graph.html are written AFTER entity pages so entity_profile
    # nodes are included. See the "Static graph.json + graph.html" block below
    # (after the entity pages section).

    # ---------- Open loops standing page (out/loops.html) ----------
    # Reuse the pre-computed list from the feed banners pass above.
    open_loops = _all_open_loops
    loops_body = render_loops_page(open_loops, anchor_base="./")
    loops_header = render_site_header(
        site_title="ClaudeJournal",
        subtitle=f"Open Loops · {len(open_loops)} unresolved",
        **filter_data,
    )
    (out_dir / "loops.html").write_text(
        layout("Open Loops", loops_header + loops_body + "<footer>claudejournal</footer>",
               anchor_base="./"),
        encoding="utf-8",
    )
    stats["loops"] = len(open_loops)

    # ---------- Learnings standing page (out/learnings.html) ----------
    from claudejournal.learnings import aggregate_learnings
    learnings_list = aggregate_learnings(conn)
    learnings_body = render_learnings_page(learnings_list, anchor_base="./",
                                           known_topics=known_topics_all)
    learnings_header = render_site_header(
        site_title="ClaudeJournal",
        subtitle=f"Learnings · {len(learnings_list)} insights",
        **filter_data,
    )
    (out_dir / "learnings.html").write_text(
        layout("Learnings", learnings_header + learnings_body + "<footer>claudejournal</footer>",
               anchor_base="./"),
        encoding="utf-8",
    )
    stats["learnings"] = len(learnings_list)

    # ---------- Temporal recall standing page (out/echoes.html) ----------
    # Reuse the pre-computed echoes_by_date dict from the feed banners pass.
    echoes_body = render_echoes_page(_echoes_by_date, anchor_base="./",
                                     known_topics=known_topics_all)
    echoes_header = render_site_header(
        site_title="ClaudeJournal",
        subtitle=f"Echoes · {stats['echoes']} dates with patterns",
        **filter_data,
    )
    (out_dir / "echoes.html").write_text(
        layout("Echoes", echoes_header + echoes_body + "<footer>claudejournal</footer>",
               anchor_base="./"),
        encoding="utf-8",
    )

    # ---------- Connections page (out/connections.html) ----------
    # Reuse the pre-computed cross-project data from Phase A pass above,
    # but compute the full graph (with Tier 2 transfer opportunities).
    # This is separate from _connections_by_project — the graph includes
    # all entities/tags, not just per-project slices.
    _connections_graph = _compute_connections_graph(conn)
    connections_body = render_connections_page(_connections_graph, anchor_base="./")
    connections_header = render_site_header(
        site_title="ClaudeJournal",
        subtitle=(
            f"Connections · {_connections_graph['total_connections']} cross-project clusters"
        ),
        **filter_data,
    )
    (out_dir / "connections.html").write_text(
        layout("Connections",
               connections_header + connections_body + "<footer>claudejournal</footer>",
               anchor_base="./"),
        encoding="utf-8",
    )
    stats["connections_page"] = 1
    stats["connections_graph_entities"] = len(_connections_graph.get("entities") or [])
    stats["connections_graph_tags"] = len(_connections_graph.get("tag_clusters") or [])
    stats["connections_transfer_opps"] = _connections_graph.get("total_transfer_opps", 0)

    # ---------- Entity profile pages (out/entities/<slug>.html) ----------
    from claudejournal.entity_pages import build_entity_profile_data
    (out_dir / "entities").mkdir(exist_ok=True)
    # Reuse pre-computed qualifying list and slug map from entity_opts section above.
    _qualifying = _qualifying_entity_rows
    entity_slug_map: dict[str, str] = _entity_slug_map_global
    stats["entity_pages"] = 0
    for _ent_row in _qualifying:
        _ent_data = build_entity_profile_data(conn, _ent_row["entity_id"])
        if not _ent_data:
            continue
        _cname = _ent_data.get("canonical_name") or _ent_data.get("entity_name", "")
        _slug = entity_slug_map.get(_cname) or _entity_safe_slug(_cname)
        _profile_html = render_entity_profile_page(_ent_data, anchor_base="../")
        _profile_header = render_site_header(
            site_title="ClaudeJournal",
            subtitle=f"Entity · {_ent_data['entity_name']}",
            **filter_data,
        )
        (out_dir / "entities" / f"{_slug}.html").write_text(
            layout(
                _ent_data["entity_name"],
                _profile_header + _profile_html + "<footer>claudejournal</footer>",
                anchor_base="../",
            ),
            encoding="utf-8",
        )
        stats["entity_pages"] += 1

    # ---------- Extend links table with entity→arc cross-references ----------
    # Now that entity profile pages exist, add entity_profile link rows to
    # the links table so they show up in graph.html.
    _add_entity_arc_links(conn, _qualifying, entity_slug_map)
    stats["entity_arc_links"] = conn.execute(
        "SELECT COUNT(*) FROM links WHERE link_type = 'entity_arc'"
    ).fetchone()[0] or 0

    # ---------- Static graph.json + graph.html for the D3 link-graph view ----------
    # Written here (AFTER entity pages) so entity_profile nodes are included.
    _write_graph_json(conn, out_dir, slug_map=_slug_map_global,
                      entity_slug_map=entity_slug_map)
    _node_count = conn.execute(
        "SELECT COUNT(DISTINCT source_scope || ':' || source_key) + "
        "COUNT(DISTINCT target_scope || ':' || target_key) FROM links"
    ).fetchone()[0] or 0
    _edge_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] or 0
    graph_body = render_graph_page(node_count=_node_count, edge_count=_edge_count)
    graph_header = render_site_header(
        site_title="ClaudeJournal",
        subtitle="Link Graph",
        **filter_data,
    )
    (out_dir / "graph.html").write_text(
        layout("Link Graph", graph_header + graph_body + "<footer>claudejournal</footer>",
               anchor_base="./"),
        encoding="utf-8",
    )
    stats["graph"] = 1

    conn.close()
    return stats
