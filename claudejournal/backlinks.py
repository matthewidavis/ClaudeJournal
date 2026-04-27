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
}


def _friendly_key(scope: str, key: str) -> str:
    """Convert a raw key to a human-readable label for display.

    Examples
    --------
    scope='daily', key='2026-04-15'  -> 'April 15, 2026'
    scope='weekly', key='2026-W15'   -> 'Week 2026-W15'
    scope='monthly', key='2026-04'   -> 'April 2026'
    scope='topic', key='sqlite'      -> 'Sqlite'
    scope='project_arc', key='...'  -> the project id (caller formats)
    """
    if scope == "daily":
        try:
            return datetime.strptime(key, "%Y-%m-%d").strftime("%B %-d, %Y")
        except (ValueError, AttributeError):
            try:
                return datetime.strptime(key, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0", " ")
            except Exception:
                return key
    if scope == "monthly":
        try:
            return datetime.strptime(key, "%Y-%m").strftime("%B %Y")
        except Exception:
            return key
    if scope == "weekly":
        return f"Week {key}"
    if scope in ("topic", "project_arc", "document"):
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
    return f"{ab}index.html"


def get_backlinks(conn: sqlite3.Connection, scope: str, key: str,
                  anchor_base: str = "../") -> list[dict]:
    """Return all pages that link TO (scope, key).

    Each item in the returned list has:
      - source_scope: str
      - source_key: str
      - link_type: str
      - label: str  -- friendly display label
      - url: str    -- relative URL to the source page
    """
    rows = conn.execute(
        "SELECT source_scope, source_key, link_type FROM links "
        "WHERE target_scope = ? AND target_key = ? "
        "ORDER BY source_scope, source_key",
        (scope, key),
    ).fetchall()
    result: list[dict] = []
    for r in rows:
        s_scope = r["source_scope"]
        s_key = r["source_key"]
        result.append({
            "source_scope": s_scope,
            "source_key": s_key,
            "link_type": r["link_type"],
            "label": _friendly_key(s_scope, s_key),
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
