"""MCP server — exposes the journal as a reference source for any MCP
client (Claude Code, Claude Desktop, etc).

Tools (read-only, local DB only):
  journal_search(query, limit=5)        — RAG retrieval, returns excerpts + dates
  journal_recent(days=7)                — recent daily diary prose
  journal_topic(tag, limit=20)          — days tagged with `tag`
  journal_learned(topic="", limit=40)   — 'learned' bullets from briefs
  journal_open_loops(project="", limit=30)
                                         — unresolved frictions still open
  journal_echoes(date="", limit=20)     — temporal recall: prior-year echoes,
                                          recurring friction, milestones
  journal_tools(name="", limit=40)      — third-party tools/libraries/services
                                          mentioned in briefs (extracted entities)
  journal_arc(project="")               — full multi-day project arc retrospective
  journal_backlinks(scope, key, limit=40)
                                         — pages that reference a given target

The server runs over stdio (standard MCP transport). Attach to Claude
Code via:

  claude mcp add claudejournal -- python -m claudejournal mcp

The journal DB is read via the repo's normal config.json, so MCP shares
the same data source as the web UI and CLI.

Annotation writes are deliberately not exposed via MCP — that surface
should require explicit human action, not agent action."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from claudejournal.config import load as load_config
from claudejournal.db import connect


def _fmt_hit(h, idx: int) -> str:
    head = h.title
    if h.date:
        head += f"  [{h.date}]"
    if h.project_name:
        head += f"  ({h.project_name})"
    body = h.body.strip()
    if len(body) > 1200:
        body = body[:1200] + "…"
    return f"[{idx}] {h.kind} · {head}\n{body}"


def journal_search(query: str, limit: int = 5) -> str:
    """Search the journal via BM25 + vector retrieval. Returns formatted
    excerpts a model can read and cite."""
    from claudejournal.rag import retrieve
    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        hits = retrieve(conn, query, k=max(1, min(25, int(limit or 5))))
    finally:
        conn.close()
    if not hits:
        return f"No matches in the journal for: {query!r}"
    parts = [f"Journal search for {query!r} — {len(hits)} hits:\n"]
    for i, h in enumerate(hits, 1):
        parts.append(_fmt_hit(h, i))
    return "\n\n".join(parts)


def journal_recent(days: int = 7) -> str:
    """Return the last N days of daily diary prose."""
    cfg = load_config()
    days = max(1, min(90, int(days or 7)))
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute(
            """SELECT date, prose FROM narrations
               WHERE scope='daily' AND date >= ? AND prose IS NOT NULL AND prose != ''
               ORDER BY date DESC""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return f"No daily entries in the last {days} days."
    parts = [f"Last {days} days of diary prose ({len(rows)} entries):"]
    for r in rows:
        parts.append(f"\n--- {r['date']} ---\n{r['prose']}")
    return "\n".join(parts)


def journal_topic(tag: str, limit: int = 20) -> str:
    """Return days tagged with `tag` (case-insensitive). Tags come from
    the 'tags' field of session briefs."""
    cfg = load_config()
    tag_l = (tag or "").strip().lower()
    if not tag_l:
        return "Provide a tag (e.g. 'rag', 'tts', 'claude-cli')."
    limit = max(1, min(100, int(limit or 20)))
    conn = connect(cfg.db_path)
    try:
        # 1) find dates with this tag from the briefs JSON
        brief_rows = conn.execute(
            """SELECT date, brief_json FROM session_briefs
               WHERE date IS NOT NULL AND date != ''"""
        ).fetchall()
        matching_dates: set[str] = set()
        for r in brief_rows:
            try:
                b = json.loads(r["brief_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            tags = [str(t).strip().lower() for t in (b.get("tags") or [])]
            if tag_l in tags:
                matching_dates.add(r["date"])
        if not matching_dates:
            return f"No days tagged {tag!r}."
        uniq = sorted(matching_dates, reverse=True)[:limit]

        # 2) bulk-fetch all daily prose for those dates in one query
        placeholders = ",".join("?" * len(uniq))
        prose_rows = conn.execute(
            f"SELECT date, prose FROM narrations "
            f"WHERE scope='daily' AND date IN ({placeholders})",
            uniq,
        ).fetchall()
        prose_by_date = {r["date"]: r["prose"] for r in prose_rows}
    finally:
        conn.close()
    parts = [f"Days tagged {tag!r} ({len(uniq)}):"]
    for d in uniq:
        prose = prose_by_date.get(d) or "(no daily narration)"
        parts.append(f"\n--- {d} ---\n{prose}")
    return "\n".join(parts)


def journal_learned(topic: str = "", limit: int = 40) -> str:
    """Return 'learned' bullets pulled from session briefs across the
    corpus, newest first. If `topic` is given, only bullets containing
    that substring (case-insensitive) are returned."""
    cfg = load_config()
    limit = max(1, min(200, int(limit or 40)))
    topic_l = (topic or "").strip().lower()
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute(
            """SELECT date, brief_json FROM session_briefs
               WHERE date IS NOT NULL AND date != ''
               ORDER BY date DESC"""
        ).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for r in rows:
        try:
            b = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        learned = b.get("learned") or []
        if not isinstance(learned, list):
            continue
        for item in learned:
            if not isinstance(item, str):
                continue
            if topic_l and topic_l not in item.lower():
                continue
            out.append(f"[{r['date']}] {item.strip()}")
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    if not out:
        return f"No 'learned' entries" + (f" matching {topic!r}." if topic_l else ".")
    header = f"'Learned' bullets" + (f" matching {topic!r}" if topic_l else "") + f" ({len(out)}):"
    return header + "\n" + "\n".join(out)


def journal_open_loops(project: str = "", limit: int = 30) -> str:
    """Return unresolved frictions ("open loops") from session briefs.

    A friction counts as an open loop when no later brief in the same
    project (or with overlapping tags) appears to have resolved it,
    AND no annotation marks it manually resolved. This is the same
    list shown on the Loops page.

    `project`: optional case-insensitive substring; only loops whose
      project display name contains this string are returned.
    `limit`: cap (default 30, max 200). Loops are returned oldest-first
      so the longest-standing items surface first.
    """
    from claudejournal.openloops import compute_open_loops
    cfg = load_config()
    limit = max(1, min(200, int(limit or 30)))
    proj_l = (project or "").strip().lower()
    conn = connect(cfg.db_path)
    try:
        loops = compute_open_loops(conn)
    finally:
        conn.close()
    if proj_l:
        loops = [l for l in loops if proj_l in (l.get("project_name") or "").lower()]
    if not loops:
        scope = f" for project matching {project!r}" if proj_l else ""
        return f"No open loops{scope}."
    # Oldest first so the longest-standing items surface first.
    loops.sort(key=lambda l: (l.get("date") or "", l.get("project_name") or ""))
    loops = loops[:limit]
    parts = [f"Open loops ({len(loops)}{', filtered' if proj_l else ''}):"]
    for l in loops:
        date = l.get("date", "?")
        proj = l.get("project_name", "?")
        age = l.get("age_days")
        age_str = f"  age={age}d" if age is not None else ""
        tags = l.get("tags") or []
        tag_str = f"  tags=[{', '.join(tags[:6])}]" if tags else ""
        friction = (l.get("friction") or "").strip()
        parts.append(f"\n[{date}] {proj}{age_str}{tag_str}\n  {friction}")
    return "\n".join(parts)


def journal_echoes(date: str = "", limit: int = 20) -> str:
    """Return temporal recall echoes: same MM-DD in prior years, recurring
    friction patterns, and project-anniversary milestones.

    `date`: ISO date (YYYY-MM-DD). If empty, today's echoes are returned.
    `limit`: cap on signals returned across all echo types.
    """
    from claudejournal.temporal import compute_all_echoes
    cfg = load_config()
    limit = max(1, min(100, int(limit or 20)))
    target = (date or "").strip() or datetime.now().date().isoformat()
    try:
        datetime.strptime(target, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date {date!r} — use YYYY-MM-DD."
    conn = connect(cfg.db_path)
    try:
        echoes_by_date = compute_all_echoes(conn, [target])
    finally:
        conn.close()
    e = echoes_by_date.get(target) or {}
    prior = e.get("prior_years") or []
    friction = e.get("recurring_friction") or []
    milestones = e.get("milestones") or []
    if not (prior or friction or milestones):
        return f"No echoes for {target}."
    parts = [f"Echoes for {target}:"]
    if prior:
        parts.append("\nPrior years on this day:")
        for p in prior[:limit]:
            yr = (p.get("date") or "")[:4]
            snip = (p.get("snippet") or "").strip()
            tail = f"  {snip[:200]}" if snip else ""
            parts.append(f"  [{yr}]{tail}")
    if friction:
        parts.append("\nRecurring friction patterns active today:")
        for f_ in friction[:limit]:
            tag = f_.get("tag", "?")
            n = f_.get("count", 0)
            ex = (f_.get("example_friction") or "").strip()
            ex_tail = f"  e.g. {ex[:160]}" if ex else ""
            parts.append(f"  '{tag}' — recurred on {n} dates{ex_tail}")
    if milestones:
        parts.append("\nProject milestones in window:")
        for m in milestones[:limit]:
            proj = m.get("project_name", "?")
            label = m.get("label") or f"{m.get('days', '?')} days"
            parts.append(f"  {proj} — {label}")
    return "\n".join(parts)


def journal_tools(name: str = "", limit: int = 40) -> str:
    """Return third-party tools / libraries / services / AI models that
    appear across the corpus, with day-counts.

    `name`: optional case-insensitive substring; only tools whose name
      contains this string are returned. Empty string returns the most-
      mentioned tools across the whole corpus.
    `limit`: cap (default 40, max 200).
    """
    from claudejournal.entities import get_all_entities_with_counts
    cfg = load_config()
    limit = max(1, min(200, int(limit or 40)))
    name_l = (name or "").strip().lower()
    conn = connect(cfg.db_path)
    try:
        ents = get_all_entities_with_counts(conn)
    finally:
        conn.close()
    if name_l:
        ents = [e for e in ents if name_l in (e.get("name") or "").lower()]
    if not ents:
        scope = f" matching {name!r}" if name_l else ""
        return f"No tools recorded{scope}."
    ents = ents[:limit]
    type_label = {
        "person": "person", "ai_model": "AI model",
        "library": "library", "service": "service",
    }
    parts = [f"Tools ({len(ents)}{', filtered' if name_l else ''}, sorted by day-count):"]
    for e in ents:
        nm = e.get("name", "?")
        tp = type_label.get(e.get("type"), e.get("type", "?"))
        days = e.get("day_count", 0)
        parts.append(f"  {nm}  [{tp}]  {days} day{'' if days == 1 else 's'}")
    return "\n".join(parts)


def journal_arc(project: str = "") -> str:
    """Return the full multi-day project arc retrospective for a project.

    Arcs are produced by the `arcs.py` synthesis pass — the same prose
    you see on each project's standalone page in the web UI. Far
    richer than journal_topic for project-scoped queries because it's
    explicitly written as a retrospective across the whole project's
    history rather than a list of tagged days.

    `project`: case-insensitive substring of project display name. If
      multiple match, the one with the most recent arc is returned.
      Empty string returns a list of available project arcs.
    """
    cfg = load_config()
    name_l = (project or "").strip().lower()
    conn = connect(cfg.db_path)
    try:
        if not name_l:
            rows = conn.execute(
                """SELECT n.key, n.date, n.generated_at, p.display_name
                   FROM narrations n
                   LEFT JOIN projects p ON p.id = n.key
                   WHERE n.scope = 'project_arc' AND n.prose IS NOT NULL
                   ORDER BY n.generated_at DESC"""
            ).fetchall()
            if not rows:
                return "No project arcs available yet."
            parts = [f"Available project arcs ({len(rows)}):"]
            for r in rows:
                pname = r["display_name"] or r["key"]
                parts.append(f"  {pname}  (last updated {r['generated_at'][:10]})")
            return "\n".join(parts)
        # Find matching arc — prefer most recent if multiple match.
        rows = conn.execute(
            """SELECT n.key, n.prose, n.date, n.generated_at, p.display_name
               FROM narrations n
               LEFT JOIN projects p ON p.id = n.key
               WHERE n.scope = 'project_arc' AND n.prose IS NOT NULL
                 AND lower(coalesce(p.display_name, n.key)) LIKE ?
               ORDER BY n.generated_at DESC""",
            (f"%{name_l}%",),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return f"No project arc found matching {project!r}."
    r = rows[0]
    pname = r["display_name"] or r["key"]
    extra = ""
    if len(rows) > 1:
        others = ", ".join((r2["display_name"] or r2["key"]) for r2 in rows[1:5])
        extra = f"\n\n(other matches: {others})"
    return (
        f"Project arc — {pname}\n"
        f"Last updated: {r['generated_at'][:10]}\n"
        f"\n{r['prose']}{extra}"
    )


def journal_backlinks(scope: str, key: str, limit: int = 40) -> str:
    """Return pages that reference a given target (the wiki "what links
    here" view).

    `scope`: target type — one of 'topic', 'document', 'project_arc',
      'daily', 'weekly', 'monthly'.
    `key`: target identifier — for topics, the slug; for project_arcs
      and documents, the id; for daily/weekly/monthly, the date or key.
    `limit`: cap on backlinks returned (default 40, max 200).
    """
    from claudejournal.backlinks import get_backlinks_grouped
    cfg = load_config()
    valid_scopes = {"topic", "document", "project_arc", "daily", "weekly", "monthly"}
    if (scope or "").strip().lower() not in valid_scopes:
        return (
            f"Invalid scope {scope!r}. Valid scopes: "
            + ", ".join(sorted(valid_scopes))
        )
    scope = scope.strip().lower()
    if not (key or "").strip():
        return "Provide a key (e.g. topic slug, document id, project_id, date)."
    limit = max(1, min(200, int(limit or 40)))
    conn = connect(cfg.db_path)
    try:
        grouped = get_backlinks_grouped(conn, scope, key.strip())
    finally:
        conn.close()
    if not grouped:
        return f"No backlinks to {scope}:{key}."
    parts = [f"Backlinks to {scope}:{key}:"]
    total = 0
    for src_scope, items in grouped.items():
        if total >= limit:
            break
        parts.append(f"\nFrom {src_scope} ({len(items)}):")
        for item in items[:max(1, limit - total)]:
            label = item.get("label") or item.get("key", "?")
            link_type = item.get("link_type", "")
            parts.append(f"  {label}" + (f"  [{link_type}]" if link_type else ""))
            total += 1
            if total >= limit:
                break
    return "\n".join(parts)


def run_stdio() -> None:
    """Run the MCP server over stdio. Entry point for `claudejournal mcp`."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit(
            "mcp package not installed. Install with: pip install mcp"
        )

    mcp = FastMCP("claudejournal")

    # Register tools — docstrings become the descriptions the client sees.
    mcp.tool()(journal_search)
    mcp.tool()(journal_recent)
    mcp.tool()(journal_topic)
    mcp.tool()(journal_learned)
    mcp.tool()(journal_open_loops)
    mcp.tool()(journal_echoes)
    mcp.tool()(journal_tools)
    mcp.tool()(journal_arc)
    mcp.tool()(journal_backlinks)

    mcp.run()
