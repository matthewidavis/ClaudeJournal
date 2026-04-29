"""Per-entity profile page data builder — Phase B, Tasks 10 & 11.

Data-driven (no LLM calls): for each qualifying entity, builds a profile
data dict that render.py passes to render_entity_profile_page().

Qualifying threshold (either condition):
  - Appears in 2+ distinct projects, OR
  - Appears on 5+ distinct dates

The critical correctness constraint (Phase A edge case fix):
  Learnings are drawn ONLY from session_briefs whose corresponding
  brief_entities row actually contains this entity. We never pull from
  a project's general learning pool — only briefs where the entity
  was recorded as present.

Task 12 (LLM-synthesized entity wiki) is NOT implemented here.
It is a separate optional upgrade deferred pending user approval.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict


# ---------------------------------------------------------------------------
# Qualifying threshold
# ---------------------------------------------------------------------------
_MIN_PROJECTS = 2     # entity must appear in this many+ distinct projects, OR
_MIN_DATES = 5        # entity must appear on this many+ distinct dates


def qualifying_entities(conn: sqlite3.Connection) -> list[dict]:
    """Return a list of entity rows that qualify for a profile page.

    Each row has: entity_id, entity_name, entity_type, canonical_name,
                  project_count, date_count.

    Sorted: project_count desc, date_count desc.
    """
    rows = conn.execute(
        """
        SELECT e.id AS entity_id,
               e.name AS entity_name,
               e.type AS entity_type,
               e.canonical_name,
               COUNT(DISTINCT sb.project_id) AS project_count,
               COUNT(DISTINCT be.date)        AS date_count
        FROM brief_entities be
        JOIN entities e ON e.id = be.entity_id
        JOIN session_briefs sb
          ON sb.session_id = be.session_id
         AND sb.date = be.date
        WHERE be.date != ''
        GROUP BY e.id
        HAVING project_count >= ? OR date_count >= ?
        ORDER BY project_count DESC, date_count DESC
        """,
        (_MIN_PROJECTS, _MIN_DATES),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Profile data builder
# ---------------------------------------------------------------------------

def build_entity_profile_data(
    conn: sqlite3.Connection,
    entity_id: str,
) -> dict | None:
    """Build the full profile data dict for one entity.

    Returns None if the entity has no qualifying data.

    Returned dict shape:
        {
          "entity_id": str,
          "entity_name": str,
          "entity_type": str | None,
          "canonical_name": str | None,
          "total_projects": int,
          "total_dates": int,
          "projects": [
            {
              "project_id": str,
              "project_name": str,
              "date_count": int,
              "first_seen": str,
              "last_seen": str,
              "learnings": [str, ...],   # from briefs that actually have this entity
              "arc_url": str,            # relative to out/ root: projects/<name>/index.html
            },
            ...   # sorted: date_count desc
          ],
          "all_learnings": [
            {
              "text": str,
              "date": str,
              "project_name": str,
              "project_id": str,
              "arc_url": str,
            },
            ...  # deduped, sorted by date desc
          ],
        }
    """
    import urllib.parse as _up

    # Fetch entity metadata
    e_row = conn.execute(
        "SELECT id, name, type, canonical_name FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if not e_row:
        return None

    ename = e_row["name"]
    etype = e_row["type"]
    cname = e_row["canonical_name"]

    # Fetch all (date, session_id, project_id, brief_json, project_name) rows
    # for briefs that actually contain this entity.
    rows = conn.execute(
        """
        SELECT be.date, be.session_id,
               sb.project_id, sb.brief_json,
               p.display_name AS project_name
        FROM brief_entities be
        JOIN session_briefs sb
          ON sb.session_id = be.session_id
         AND sb.date = be.date
        JOIN projects p ON p.id = sb.project_id
        WHERE be.entity_id = ?
          AND be.date != ''
        ORDER BY be.date DESC, p.display_name
        """,
        (entity_id,),
    ).fetchall()

    if not rows:
        return None

    # Check annotation suppression: skip dates that are fully suppressed
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

    # Build per-project data and collect all learnings
    project_data: dict[str, dict] = {}   # project_id -> accumulated data
    all_learning_rows: list[dict] = []   # flat, deduped across projects
    seen_learning_texts: set[str] = set()

    for r in rows:
        date = r["date"]
        if date in suppressed_dates:
            continue
        pid = r["project_id"]
        pname = r["project_name"]
        arc_url = f"projects/{_up.quote(pname, safe='')}/index.html"

        if pid not in project_data:
            project_data[pid] = {
                "project_id": pid,
                "project_name": pname,
                "dates": [],
                "learnings": [],
                "arc_url": arc_url,
            }
        entry = project_data[pid]
        if date not in entry["dates"]:
            entry["dates"].append(date)

        # Extract learnings from THIS brief (the brief that actually has the entity)
        try:
            brief = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            brief = {}
        for item in (brief.get("learned") or []):
            if not isinstance(item, str) or not item.strip():
                continue
            ltext = item.strip()
            if ltext not in entry["learnings"]:
                entry["learnings"].append(ltext)
            # For the global all_learnings list: add once, most-recent first
            if ltext not in seen_learning_texts:
                seen_learning_texts.add(ltext)
                all_learning_rows.append({
                    "text": ltext,
                    "date": date,
                    "project_id": pid,
                    "project_name": pname,
                    "arc_url": arc_url,
                })

    if not project_data:
        return None

    # Finalize project list
    projects_out: list[dict] = []
    for entry in project_data.values():
        dates_sorted = sorted(entry["dates"])
        projects_out.append({
            "project_id": entry["project_id"],
            "project_name": entry["project_name"],
            "date_count": len(dates_sorted),
            "first_seen": dates_sorted[0],
            "last_seen": dates_sorted[-1],
            "learnings": entry["learnings"][:4],   # cap at 4 in the project card
            "arc_url": entry["arc_url"],
        })
    projects_out.sort(key=lambda p: -p["date_count"])

    total_dates = sum(p["date_count"] for p in projects_out)

    # Sort all_learnings: most recent date first
    all_learning_rows.sort(key=lambda l: l["date"], reverse=True)

    return {
        "entity_id": entity_id,
        "entity_name": ename,
        "entity_type": etype,
        "canonical_name": cname,
        "total_projects": len(projects_out),
        "total_dates": total_dates,
        "projects": projects_out,
        "all_learnings": all_learning_rows,
    }
