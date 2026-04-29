"""MCP server — exposes the journal as a reference source for any MCP
client (Claude Code, Claude Desktop, etc).

Tools (11, read-only, local DB only):
  journal_search(query, limit=5)        — RAG retrieval, returns excerpts + dates
  journal_recent(days=7)                — recent daily diary prose
  journal_topic(tag, limit=20)          — days tagged with `tag`; FTS5 fallback +
                                          suggestions when tag not found
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
  journal_connections(query, project="", limit=10)
                                         — tiered transfer-recall: find prior
                                          learnings from other projects that are
                                          relevant to the given query; three signal
                                          tiers (entity match, tag overlap, FTS5)
  journal_entity(name="", limit=10)     — entity profile: synthesis prose, project
                                          list with date counts, top learnings

Note: journal_search and journal_connections answer different questions.
  journal_search — raw BM25 retrieval, returns whatever chunks match.
  journal_connections — structured transfer-recall, ranks results by signal
    strength (entity > tag > full-text) and attributes the WHY for each hit.

The server runs over stdio (standard MCP transport). Attach to Claude
Code via:

  claude mcp add claudejournal -- python -m claudejournal mcp

The journal DB is read via the repo's normal config.json, so MCP shares
the same data source as the web UI and CLI.

Annotation writes are deliberately not exposed via MCP — that surface
should require explicit human action, not agent action."""
from __future__ import annotations

import difflib
import json
from datetime import datetime, timedelta

from claudejournal.config import load as load_config
from claudejournal.db import connect


def _suggest_matches(query: str, candidates: list[str], limit: int = 3) -> str:
    """Return a human-readable suggestion string when a query finds no results.

    Strategy (in priority order):
    1. difflib.get_close_matches at cutoff 0.6 on lowercased candidates.
    2. Substring containment fallback — any candidate that contains the query
       as a substring (case-insensitive), not already in step 1's results.
    Returns at most `limit` suggestions formatted as a hint string, or ''
    if nothing useful was found."""
    if not query or not candidates:
        return ""
    q_low = query.strip().lower()
    cand_low = [c.strip().lower() for c in candidates]

    # Step 1: difflib close matches (operates on lowercased strings)
    close = difflib.get_close_matches(q_low, cand_low, n=limit, cutoff=0.6)
    close_set = set(close)

    # Step 2: substring fallback — candidates containing the query as substring
    substr = [c for c in cand_low if q_low in c and c not in close_set]

    merged = close + substr
    if not merged:
        return ""

    # Map back to original-case candidates (preserve display casing)
    low_to_orig = {c.strip().lower(): c for c in candidates}
    suggestions = [low_to_orig.get(m, m) for m in merged[:limit]]
    return "  Did you mean: " + ", ".join(f"{s!r}" for s in suggestions) + "?"


def _get_project_display_names(conn) -> list[str]:
    """Return all project display_names for use as suggestion candidates."""
    rows = conn.execute(
        "SELECT display_name FROM projects WHERE display_name IS NOT NULL"
    ).fetchall()
    return [r["display_name"] for r in rows if r["display_name"]]


def _get_tag_candidates(conn, limit: int = 50) -> list[str]:
    """Return the top `limit` tags by frequency from session_briefs.brief_json."""
    rows = conn.execute(
        "SELECT brief_json FROM session_briefs WHERE brief_json IS NOT NULL"
    ).fetchall()
    tag_counts: dict[str, int] = {}
    for r in rows:
        try:
            b = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        for t in (b.get("tags") or []):
            tag = str(t).strip().lower()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    # Sort by frequency descending, return top N
    sorted_tags = sorted(tag_counts, key=lambda t: tag_counts[t], reverse=True)
    return sorted_tags[:limit]


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
    the 'tags' field of session briefs. When no exact tag match is found,
    falls back to an FTS5 search for the term and suggests close tag names."""
    from claudejournal.rag import retrieve
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
            # Gather candidates for suggestions + FTS5 fallback while conn open
            tag_candidates = _get_tag_candidates(conn)
            hint = _suggest_matches(tag, tag_candidates)

            # FTS5 fallback: search for the term across the corpus
            fts_hits = retrieve(conn, tag, k=5)
            if not fts_hits:
                suffix = f"\n{hint}" if hint else ""
                return f"No days tagged {tag!r}.{suffix}"
            # Return FTS5 results with explicit fallback header
            parts = [
                f"No exact tag match for {tag!r} — "
                f"found {len(fts_hits)} entr{'y' if len(fts_hits) == 1 else 'ies'} via search:"
            ]
            if hint:
                parts.append(hint)
            for i, h in enumerate(fts_hits, 1):
                parts.append(_fmt_hit(h, i))
            parts.append("(use journal_search for more)")
            return "\n\n".join(parts)

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
        project_names = _get_project_display_names(conn) if proj_l else []
    finally:
        conn.close()
    if proj_l:
        loops = [l for l in loops if proj_l in (l.get("project_name") or "").lower()]
    if not loops:
        scope = f" for project matching {project!r}" if proj_l else ""
        hint = _suggest_matches(project, project_names) if proj_l else ""
        suffix = f"\n{hint}" if hint else ""
        return f"No open loops{scope}.{suffix}"
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
        filtered = [e for e in ents if name_l in (e.get("name") or "").lower()]
        if not filtered:
            entity_names = [e["name"] for e in ents if e.get("name")]
            hint = _suggest_matches(name, entity_names)
            suffix = f"\n{hint}" if hint else ""
            return f"No tools recorded matching {name!r}.{suffix}"
        ents = filtered
    if not ents:
        return "No tools recorded."
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
        project_names = _get_project_display_names(conn)
    finally:
        conn.close()
    if not rows:
        hint = _suggest_matches(project, project_names)
        suffix = f"\n{hint}" if hint else ""
        return f"No project arc found matching {project!r}.{suffix}"
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
        if not grouped:
            # Collect valid target_keys for this scope to suggest close matches
            key_rows = conn.execute(
                "SELECT DISTINCT target_key FROM links WHERE target_scope = ?",
                (scope,),
            ).fetchall()
            candidate_keys = [r["target_key"] for r in key_rows if r["target_key"]]
    finally:
        conn.close()
    if not grouped:
        hint = _suggest_matches(key, candidate_keys)
        suffix = f"\n{hint}" if hint else ""
        return f"No backlinks to {scope}:{key}.{suffix}"
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


def journal_connections(query: str, project: str = "", limit: int = 10) -> str:
    """Find learnings and patterns from other projects that may be relevant
    to your current work.

    Works best with a tool or library name, a problem description, or a concept.
    Results are grouped by signal tier so you can see WHY each item was surfaced:

      Tier 1 — Found via shared entity: the query mentions a known tool or library
        that appears in other projects. Strong signal — entity-level overlap means
        the same tool was used in a different context.

      Tier 2 — Found via tag overlap: the query contains tags that match known
        topic tags in the corpus. Medium signal — topic-level overlap.

      Tier 3 — Found via full-text search: BM25 retrieval over journal chunks.
        Complements Tier 1/2 by catching concepts not yet entitized or tagged.

    Annotation-suppressed content is never returned — you will not see learnings
    that the journal owner has flagged as outdated or wrong.

    Note: this tool complements journal_search, it does not replace it.
    journal_search returns raw BM25 chunks. journal_connections returns ranked,
    attributed learnings with explicit transfer signal.

    `query`: free text — tool name, concern, code fragment, concept.
    `project`: if provided, exclude results from this project (pass your current
      project name so results are always from OTHER projects).
    `limit`: max results to return (default 10, cap 50).
    """
    from claudejournal.connections import transfer_recall
    cfg = load_config()
    limit = max(1, min(50, int(limit or 10)))
    query = (query or "").strip()
    if not query:
        return "Provide a query — a tool name, concept, or problem description."
    conn = connect(cfg.db_path)
    try:
        results = transfer_recall(conn, query, project_filter=project or None, limit=limit)
    finally:
        conn.close()

    if not results:
        return f"No cross-project connections found for: {query!r}"

    # Group by tier for prose-readable output
    tier_groups: dict[int, list[dict]] = {}
    for r in results:
        tier_groups.setdefault(r["tier"], []).append(r)

    tier_headers = {
        1: "entity",
        2: "tag overlap",
        3: "full-text search",
    }

    parts: list[str] = [
        f"Cross-project connections for {query!r}"
        + (f" (excluding {project!r})" if project else "")
        + f" — {len(results)} result{'s' if len(results) != 1 else ''}:"
    ]

    idx = 1
    for tier in sorted(tier_groups.keys()):
        items = tier_groups[tier]
        # Group items within this tier by their signal value
        signal_groups: dict[str, list[dict]] = {}
        for item in items:
            signal_groups.setdefault(item["signal"], []).append(item)

        for signal, signal_items in signal_groups.items():
            if tier == 1:
                header = f"Found via shared entity '{signal}'"
            elif tier == 2:
                header = f"Found via tag overlap on '{signal}'"
            else:
                header = f"Found via full-text search"
            parts.append(f"\n{header}:")

            for item in signal_items:
                proj_name = item["source_project"] or item["source_project_id"] or "?"
                date = item["date"]
                excerpt = item["excerpt"]
                hint = item.get("entity_profile_hint", "")
                line = f"  [{idx}] {proj_name} ({date}): {excerpt}"
                if hint:
                    line += f"\n       [see entity profile: {hint}]"
                parts.append(line)
                idx += 1

    return "\n".join(parts)


def journal_entity(name: str = "", limit: int = 10) -> str:
    """Return the entity profile for a tool, library, service, AI model, or person.

    Entity profiles are the same data that backs the entity profile pages in the
    web UI: synthesis prose (if narrated), project list with date counts, and top
    learnings attributed to this entity across the whole corpus.

    `name`: case-insensitive substring of entity name. If multiple match, all are
      returned (most-mentioned first). Empty string returns the top entities by
      day-count — useful for browsing available profiles.
    `limit`: top N learnings to include per entity (default 10, max 50).

    Annotation suppression is respected: dates marked 'resolved' or 'outdated'
    via the annotations table are excluded from learning and project counts.
    """
    from claudejournal.entities import get_all_entities_with_counts
    cfg = load_config()
    limit = max(1, min(50, int(limit or 10)))
    name_l = (name or "").strip().lower()
    conn = connect(cfg.db_path)
    try:
        all_ents = get_all_entities_with_counts(conn)

        if not name_l:
            # List top entities by day_count, like journal_arc("") lists arcs.
            if not all_ents:
                return "No entities recorded yet."
            top = all_ents[:20]
            parts = [f"Top entities by day-count ({len(top)} shown of {len(all_ents)} total):"]
            for e in top:
                nm = e.get("name", "?")
                tp = e.get("type") or "?"
                days = e.get("day_count", 0)
                parts.append(f"  {nm}  [{tp}]  {days} day{'' if days == 1 else 's'}")
            parts.append("\nUse journal_entity(name=<name>) to retrieve a full profile.")
            return "\n".join(parts)

        # Find entities matching the name substring
        matched = [e for e in all_ents if name_l in (e.get("name") or "").lower()]
        if not matched:
            entity_names = [e["name"] for e in all_ents if e.get("name")]
            hint = _suggest_matches(name, entity_names)
            suffix = f"\n{hint}" if hint else ""
            return f"No entity found matching {name!r}.{suffix}"

        # Annotation suppression: load suppressed dates once
        suppressed_dates: set[str] = set()
        try:
            sup_rows = conn.execute(
                """SELECT target_key FROM annotations
                   WHERE target_scope = 'daily'
                   AND annotation_type = 'correction'
                   AND scope_tag IN ('resolved', 'outdated')"""
            ).fetchall()
            suppressed_dates = {r["target_key"] for r in sup_rows}
        except Exception:
            pass

        output_parts: list[str] = []

        for ent in matched:
            entity_id = ent["id"]
            entity_name = ent["name"]
            entity_type = ent.get("type") or "unknown"
            canonical = ent.get("canonical_name")

            # 1) Synthesis prose from narrations (scope='entity_profile', key=entity_id)
            prose_row = conn.execute(
                """SELECT prose, generated_at FROM narrations
                   WHERE scope = 'entity_profile' AND key = ? AND prose IS NOT NULL""",
                (entity_id,),
            ).fetchone()

            # 2) Per-project data from brief_entities + session_briefs
            brief_rows = conn.execute(
                """
                SELECT be.date, sb.project_id, sb.brief_json,
                       p.display_name AS project_name
                FROM brief_entities be
                JOIN session_briefs sb
                  ON sb.session_id = be.session_id AND sb.date = be.date
                JOIN projects p ON p.id = sb.project_id
                WHERE be.entity_id = ? AND be.date != ''
                ORDER BY be.date DESC, p.display_name
                """,
                (entity_id,),
            ).fetchall()

            # Build project data + collect learnings
            project_data: dict[str, dict] = {}
            all_learnings: list[dict] = []
            seen_texts: set[str] = set()

            for r in brief_rows:
                date = r["date"]
                if date in suppressed_dates:
                    continue
                pid = r["project_id"]
                pname = r["project_name"]

                if pid not in project_data:
                    project_data[pid] = {
                        "project_name": pname,
                        "dates": [],
                    }
                if date not in project_data[pid]["dates"]:
                    project_data[pid]["dates"].append(date)

                try:
                    brief = json.loads(r["brief_json"])
                except (json.JSONDecodeError, TypeError):
                    brief = {}
                for item in (brief.get("learned") or []):
                    if not isinstance(item, str) or not item.strip():
                        continue
                    ltext = item.strip()
                    if ltext not in seen_texts:
                        seen_texts.add(ltext)
                        all_learnings.append({"text": ltext, "date": date, "project": pname})

            # Sort projects by date_count desc
            projects_out = sorted(
                project_data.values(),
                key=lambda p: len(p["dates"]),
                reverse=True,
            )
            total_days = sum(len(p["dates"]) for p in projects_out)

            # Sort learnings by date desc, cap at limit
            all_learnings.sort(key=lambda l: l["date"], reverse=True)
            top_learnings = all_learnings[:limit]

            # Format this entity's block
            header_line = f"Entity: {entity_name}  [{entity_type}]"
            if canonical and canonical.lower() != entity_name.lower():
                header_line += f"  (canonical: {canonical})"
            header_line += f"  — {total_days} day{'' if total_days == 1 else 's'} across {len(projects_out)} project{'' if len(projects_out) == 1 else 's'}"
            block: list[str] = [header_line]

            # Synthesis prose
            if prose_row and prose_row["prose"]:
                updated = (prose_row["generated_at"] or "")[:10]
                block.append(f"\nSynthesis (last updated {updated}):")
                block.append(prose_row["prose"].strip())
            else:
                block.append("\n(No synthesis prose yet — run the narration pass to generate.)")

            # Projects
            block.append("\nProjects:")
            for p in projects_out:
                dates = sorted(p["dates"])
                dc = len(dates)
                first, last = dates[0], dates[-1]
                block.append(
                    f"  {p['project_name']}  —  {dc} day{'' if dc == 1 else 's'}  "
                    f"({first} → {last})"
                )

            # Learnings
            if top_learnings:
                block.append(f"\nTop {len(top_learnings)} learnings (most recent first):")
                for i, lrn in enumerate(top_learnings, 1):
                    block.append(f"  [{i}] [{lrn['date']}] ({lrn['project']}) {lrn['text']}")
            else:
                block.append("\n(No learnings extracted for this entity.)")

            output_parts.append("\n".join(block))

    finally:
        conn.close()

    return "\n\n---\n\n".join(output_parts)


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
    mcp.tool()(journal_connections)
    mcp.tool()(journal_entity)

    mcp.run()
