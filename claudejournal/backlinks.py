"""Backlinks query helpers.

The `links` table is rebuilt on every render_site() call and stores
source->target pairs extracted from narration prose.  This module
provides a thin query layer used by render.py when constructing topic,
arc, and document pages.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime


# Human-readable scope labels used in "Referenced from" sections.
_SCOPE_LABEL = {
    "daily": "Daily",
    "project_day": "Project day",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "topic": "Topic",
    "project_arc": "Project",
    "document": "Document",
    "entity_profile": "Entity",
}


def _format_daily_label(key: str) -> str:
    try:
        return datetime.strptime(key, "%Y-%m-%d").strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(key, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            return key


def _friendly_key(scope: str, key: str,
                  project_names: dict[str, str] | None = None,
                  document_titles: dict[str, str] | None = None) -> str:
    """Convert a raw key to a human-readable label for display.

    `project_names`: optional {project_id: display_name} map. When supplied,
      project_arc and project_day keys resolve to the display name instead
      of the raw project_id (which is a filesystem-flavoured slug).
    `document_titles`: optional {document_id: title} map for document keys.

    Examples
    --------
    scope='daily', key='2026-04-15'         -> 'April 15, 2026'
    scope='weekly', key='2026-W15'          -> 'Week 2026-W15'
    scope='monthly', key='2026-04'          -> 'April 2026'
    scope='topic', key='sqlite-vec'         -> 'Sqlite Vec'
    scope='project_arc', key='...'          -> 'ChromaKey' (from project_names)
    scope='project_day', key='...|<date>'   -> 'ChromaKey on April 15, 2026'
    """
    project_names = project_names or {}
    document_titles = document_titles or {}
    if scope == "daily":
        return _format_daily_label(key)
    if scope == "monthly":
        try:
            return datetime.strptime(key, "%Y-%m").strftime("%B %Y")
        except Exception:
            return key
    if scope == "weekly":
        return f"Week {key}"
    if scope == "project_arc":
        # Resolve project_id to its display name. Fall back to a tidied
        # version of the raw id if we have no mapping (it's a filesystem
        # slug, but title-cased it's at least readable).
        return project_names.get(key) or key.replace("-", " ").replace("_", " ").strip().title() or key
    if scope == "project_day":
        # key is 'project_id|YYYY-MM-DD'. Format as
        # '<display_name> on <date>' so the full source is identifiable.
        try:
            pid, date = key.split("|", 1)
        except ValueError:
            return key
        pname = project_names.get(pid) or pid.replace("-", " ").replace("_", " ").strip().title() or pid
        return f"{pname} on {_format_daily_label(date)}"
    if scope == "document":
        # Documents have a real title in the documents table; prefer it
        # over the raw id when available.
        return document_titles.get(key) or key.replace("-", " ").title()
    if scope == "topic":
        return key.replace("-", " ").title()
    if scope == "entity_profile":
        return key.replace("-", " ").title()
    return key


def _page_url(scope: str, key: str, anchor_base: str = "../") -> str:
    """Return the relative URL for a given scope+key pair."""
    ab = anchor_base.rstrip("/") + "/"
    if scope == "daily":
        return f"{ab}index.html#{key}"
    if scope == "project_day":
        # key is 'project_id|date'
        try:
            _pid, date = key.split("|", 1)
        except ValueError:
            return f"{ab}index.html"
        return f"{ab}index.html#{date}"
    if scope == "weekly":
        return f"{ab}weekly/{key}.html"
    if scope == "monthly":
        return f"{ab}monthly/{key}.html"
    if scope == "topic":
        return f"{ab}topics/{key}.html"
    if scope == "project_arc":
        # key is project_id; we don't have the display name here, so link
        # to the projects directory using the raw id.  render.py maps this
        # to the correct slug before calling get_backlinks().
        return f"{ab}projects/{key}/index.html"
    if scope == "document":
        return f"{ab}docs/{key}.html"
    if scope == "entity_profile":
        return f"{ab}entities/{key}.html"
    return f"{ab}index.html"


def _load_label_maps(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Build {project_id: display_name} and {document_id: title} lookup
    tables in one DB pass. Cheap; the journal's project + document
    counts are always small."""
    proj: dict[str, str] = {}
    docs: dict[str, str] = {}
    try:
        for r in conn.execute(
            "SELECT id, display_name FROM projects WHERE display_name IS NOT NULL"
        ).fetchall():
            proj[r["id"]] = r["display_name"]
    except sqlite3.OperationalError:
        pass  # projects table missing — extremely unlikely, but fall through gracefully
    try:
        for r in conn.execute(
            "SELECT id, title, original_filename FROM documents"
        ).fetchall():
            title = (r["title"] or "").strip() or (r["original_filename"] or "").strip() or r["id"]
            docs[r["id"]] = title
    except sqlite3.OperationalError:
        pass
    return proj, docs


def get_backlinks(conn: sqlite3.Connection, scope: str, key: str,
                  anchor_base: str = "../") -> list[dict]:
    """Return all pages that link TO (scope, key).

    Each item in the returned list has:
      - source_scope: str
      - source_key: str
      - link_type: str
      - label: str  -- friendly display label (project_arc / project_day
                       sources resolve to the project's display name;
                       document sources resolve to the document title)
      - url: str    -- relative URL to the source page
    """
    rows = conn.execute(
        "SELECT source_scope, source_key, link_type FROM links "
        "WHERE target_scope = ? AND target_key = ? "
        "ORDER BY source_scope, source_key",
        (scope, key),
    ).fetchall()
    if not rows:
        return []
    project_names, document_titles = _load_label_maps(conn)
    result: list[dict] = []
    for r in rows:
        s_scope = r["source_scope"]
        s_key = r["source_key"]
        result.append({
            "source_scope": s_scope,
            "source_key": s_key,
            "link_type": r["link_type"],
            "label": _friendly_key(s_scope, s_key, project_names, document_titles),
            "scope_label": _SCOPE_LABEL.get(s_scope, s_scope),
            "url": _page_url(s_scope, s_key, anchor_base),
        })
    return result


def get_backlinks_grouped(conn: sqlite3.Connection, scope: str, key: str,
                          anchor_base: str = "../") -> dict[str, list[dict]]:
    """Like get_backlinks() but grouped by scope label for rendering.

    Returns {scope_label: [items...]} ordered by scope priority.
    """
    items = get_backlinks(conn, scope, key, anchor_base=anchor_base)
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["scope_label"], []).append(item)
    # Sort groups by a natural priority order
    priority = ["Daily", "Weekly", "Monthly", "Project", "Project day", "Topic", "Document"]
    ordered: dict[str, list[dict]] = {}
    for label in priority:
        if label in groups:
            ordered[label] = groups[label]
    for label, items_list in groups.items():
        if label not in ordered:
            ordered[label] = items_list
    return ordered
