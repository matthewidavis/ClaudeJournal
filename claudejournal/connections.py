"""Cross-project connection computation for ClaudeJournal.

Phase A: Render-time callouts — no model calls, pure SQL + Python.

Core exports:
  compute_cross_project_connections(conn)
      -> {project_id: [connection_dict, ...]}
      Per-project list of related-work signals (shared entities + tags).

  compute_all_daily_connections(conn, dates)
      -> {date: [nudge_dict, ...]}
      Per-date nudges: for each entity/tag on that day, which OTHER projects
      have substantial history with it (threshold: 3+ dates in 2+ other projects).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict


# ---------------------------------------------------------------------------
# Blocklist: entities that are too generic to be useful connection signals.
# "Claude Code" and "GitHub" appear in almost every project — surfacing
# them as connections is noise, not signal.  PTZOptics is extremely
# domain-specific and IS useful signal, so it stays in.
# ---------------------------------------------------------------------------
_ENTITY_BLOCKLIST: frozenset[str] = frozenset({
    "claude", "claude code", "github", "git",
    "python", "javascript", "typescript", "html", "css",
    "node", "node.js", "npm", "pip",
    "vscode", "vs code", "visual studio code",
    "windows", "macos", "linux",
})

# Tags that are too generic to be useful (common workflow tags, not topics).
_TAG_BLOCKLIST: frozenset[str] = frozenset({
    "debugging", "code-review", "research", "qa-testing",
    "refactoring", "testing", "documentation",
    "bug-fix", "bug", "setup", "configuration",
})

# Threshold: an entity/tag must appear on this many dates in *another* project
# to count as "substantial history" for daily nudges.
_DAILY_NUDGE_THRESHOLD = 3

# Max nudges per day in the render — cap prevents noise on busy days.
MAX_DAILY_NUDGES = 3

# Suppress connections where the entity appears in this many+ projects AND
# this many+ dates across the corpus (too ubiquitous to be informative).
_ENTITY_UBIQUITY_PROJECT_THRESHOLD = 15
_ENTITY_UBIQUITY_DATE_THRESHOLD = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_entity_project_map(conn: sqlite3.Connection) -> dict:
    """Return entity_id -> {project_id -> {dates, learnings, entity_name, entity_type}}.

    One pass over the join; everything downstream uses this map.
    Filters out blocklisted and overly ubiquitous entities.
    """
    rows = conn.execute(
        """
        SELECT e.id AS eid, e.name AS ename, e.type AS etype,
               e.canonical_name,
               sb.project_id, sb.date, sb.brief_json,
               p.display_name AS project_name
        FROM brief_entities be
        JOIN entities e ON e.id = be.entity_id
        JOIN session_briefs sb ON sb.session_id = be.session_id
                               AND sb.date = be.date
        JOIN projects p ON p.id = sb.project_id
        WHERE sb.date != ''
        ORDER BY e.id, sb.project_id, sb.date
        """
    ).fetchall()

    # entity_id -> project_id -> {dates, learnings, name, type, project_name}
    emap: dict[str, dict[str, dict]] = {}
    for r in rows:
        eid = r["eid"]
        ename_lower = (r["ename"] or "").lower()
        if ename_lower in _ENTITY_BLOCKLIST:
            continue
        pid = r["project_id"]
        if eid not in emap:
            emap[eid] = {}
        if pid not in emap[eid]:
            emap[eid][pid] = {
                "dates": set(),
                "learnings": [],
                "entity_name": r["ename"],
                "entity_type": r["etype"],
                "canonical_name": r["canonical_name"],
                "project_name": r["project_name"],
            }
        entry = emap[eid][pid]
        entry["dates"].add(r["date"])
        # Extract learnings from this brief
        try:
            brief = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            brief = {}
        for item in (brief.get("learned") or []):
            if isinstance(item, str) and item.strip():
                ltext = item.strip()
                if ltext not in entry["learnings"]:
                    entry["learnings"].append(ltext)

    # Remove ubiquitous entities (too many projects or too many dates total)
    to_remove = []
    for eid, proj_map in emap.items():
        total_projects = len(proj_map)
        total_dates = sum(len(v["dates"]) for v in proj_map.values())
        if (total_projects >= _ENTITY_UBIQUITY_PROJECT_THRESHOLD or
                total_dates >= _ENTITY_UBIQUITY_DATE_THRESHOLD):
            to_remove.append(eid)
    for eid in to_remove:
        del emap[eid]

    return emap


def _load_tag_project_map(conn: sqlite3.Connection) -> dict:
    """Return tag -> {project_id -> {dates, learnings, project_name}}.

    Scans session_briefs JSON for tags; skips blocklisted tags.
    """
    rows = conn.execute(
        """
        SELECT sb.project_id, sb.date, sb.brief_json, p.display_name AS project_name
        FROM session_briefs sb
        JOIN projects p ON p.id = sb.project_id
        WHERE sb.date != ''
        ORDER BY sb.project_id, sb.date
        """
    ).fetchall()

    tmap: dict[str, dict[str, dict]] = {}
    for r in rows:
        try:
            brief = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        pid = r["project_id"]
        date = r["date"]
        learnings = [
            item.strip()
            for item in (brief.get("learned") or [])
            if isinstance(item, str) and item.strip()
        ]
        for tag in (brief.get("tags") or []):
            if not isinstance(tag, str):
                continue
            tag = tag.strip().lower()
            if not tag or tag in _TAG_BLOCKLIST:
                continue
            if tag not in tmap:
                tmap[tag] = {}
            if pid not in tmap[tag]:
                tmap[tag][pid] = {
                    "dates": set(),
                    "learnings": [],
                    "project_name": r["project_name"],
                }
            entry = tmap[tag][pid]
            entry["dates"].add(date)
            for ltext in learnings:
                if ltext not in entry["learnings"]:
                    entry["learnings"].append(ltext)

    return tmap


def _check_annotations(conn: sqlite3.Connection) -> set[str]:
    """Return set of dates whose 'daily' scope annotations have a
    scope_tag of 'resolved' or 'outdated'. Connections referencing
    learnings from these dates should be suppressed.
    """
    suppressed: set[str] = set()
    try:
        rows = conn.execute(
            """SELECT target_key, scope_tag FROM annotations
               WHERE target_scope = 'daily'
               AND annotation_type = 'correction'
               AND scope_tag IN ('resolved', 'outdated')"""
        ).fetchall()
        for r in rows:
            suppressed.add(r["target_key"])
    except Exception:
        pass
    return suppressed


# ---------------------------------------------------------------------------
# Public API: compute_cross_project_connections
# ---------------------------------------------------------------------------

def compute_cross_project_connections(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Compute cross-project connections for every project.

    Returns:
        {project_id: [
            {
              "signal_type": "entity" | "tag",
              "name": str,                  # entity name or tag string
              "entity_type": str | None,    # only for signal_type="entity"
              "canonical_name": str | None, # only for signal_type="entity"
              "other_projects": [
                {
                  "project_id": str,
                  "project_name": str,
                  "date_count": int,
                  "top_learnings": [str, ...],  # up to 2
                  "arc_url": str,               # relative: projects/<name>/index.html
                }
              ]
            },
            ...
        ]}

    Only entities/tags that appear in 2+ projects are included.
    """
    suppressed_dates = _check_annotations(conn)
    entity_map = _load_entity_project_map(conn)
    tag_map = _load_tag_project_map(conn)

    # Build per-project connection list
    result: dict[str, list[dict]] = defaultdict(list)

    # --- Entity-based connections ---
    for eid, proj_map in entity_map.items():
        if len(proj_map) < 2:
            continue
        project_ids = list(proj_map.keys())
        # Entity name / type from the first project entry
        first = next(iter(proj_map.values()))
        ename = first["entity_name"]
        etype = first["entity_type"]
        cname = first["canonical_name"]

        for pid in project_ids:
            # other_projects = all projects that share this entity, except pid itself
            others = []
            for other_pid, other_entry in proj_map.items():
                if other_pid == pid:
                    continue
                # Filter learnings from suppressed dates
                clean_learnings = [
                    l for l in other_entry["learnings"]
                    # We can't easily map individual learnings to dates here,
                    # so only suppress the entire project's learnings if every
                    # date for this project+entity is suppressed.
                    # In practice, suppression is rare and project-level is safe.
                ]
                # Check if all dates are suppressed
                active_dates = {d for d in other_entry["dates"] if d not in suppressed_dates}
                if not active_dates:
                    continue  # all dates suppressed — skip this other_project
                # Limit learnings to 2 for display
                top_learnings = clean_learnings[:2]
                import urllib.parse
                pname_enc = urllib.parse.quote(other_entry["project_name"], safe="")
                others.append({
                    "project_id": other_pid,
                    "project_name": other_entry["project_name"],
                    "date_count": len(active_dates),
                    "top_learnings": top_learnings,
                    "arc_url": f"../../projects/{pname_enc}/index.html",
                })
            if not others:
                continue
            # Sort other projects by date_count descending
            others.sort(key=lambda x: -x["date_count"])
            result[pid].append({
                "signal_type": "entity",
                "name": ename,
                "entity_type": etype,
                "canonical_name": cname,
                "other_projects": others,
            })

    # --- Tag-based connections ---
    for tag, proj_map in tag_map.items():
        if len(proj_map) < 2:
            continue
        project_ids = list(proj_map.keys())

        for pid in project_ids:
            others = []
            for other_pid, other_entry in proj_map.items():
                if other_pid == pid:
                    continue
                active_dates = {d for d in other_entry["dates"] if d not in suppressed_dates}
                if not active_dates:
                    continue
                top_learnings = other_entry["learnings"][:2]
                import urllib.parse
                pname_enc = urllib.parse.quote(other_entry["project_name"], safe="")
                others.append({
                    "project_id": other_pid,
                    "project_name": other_entry["project_name"],
                    "date_count": len(active_dates),
                    "top_learnings": top_learnings,
                    "arc_url": f"../../projects/{pname_enc}/index.html",
                })
            if not others:
                continue
            others.sort(key=lambda x: -x["date_count"])
            result[pid].append({
                "signal_type": "tag",
                "name": tag,
                "entity_type": None,
                "canonical_name": None,
                "other_projects": others,
            })

    # Sort each project's connections: entities first (stronger signal), then tags;
    # within each group, by max date_count of any other_project descending.
    for pid in result:
        result[pid].sort(
            key=lambda c: (
                0 if c["signal_type"] == "entity" else 1,
                -max((op["date_count"] for op in c["other_projects"]), default=0),
            )
        )

    return dict(result)


# ---------------------------------------------------------------------------
# Public API: compute_all_daily_connections
# ---------------------------------------------------------------------------

def compute_all_daily_connections(
    conn: sqlite3.Connection,
    dates: list[str],
) -> dict[str, list[dict]]:
    """For each date in `dates`, find cross-project connection nudges.

    A nudge fires when:
      - An entity/tag present on that date also appears in at least one OTHER
        project on 3+ distinct dates (the "substantial history" threshold).
      - The entity/tag is not blocklisted or ubiquitous.

    Returns:
        {date: [
            {
              "signal_type": "entity" | "tag",
              "name": str,
              "entity_type": str | None,
              "other_project": str,          # display name of strongest other project
              "other_project_id": str,
              "other_project_date_count": int,
              "top_learning": str,           # best learning from that project
              "arc_url": str,               # relative URL to arc page
              "total_other_projects": int,  # how many other projects share this
            },
            ...  # capped at MAX_DAILY_NUDGES (3)
        ]}

    Dates with no connections are absent from the returned dict.
    """
    if not dates:
        return {}

    suppressed_dates = _check_annotations(conn)
    entity_map = _load_entity_project_map(conn)
    tag_map = _load_tag_project_map(conn)

    # Build date -> project_ids active that day
    date_set = set(dates)
    date_projects: dict[str, set[str]] = defaultdict(set)
    rows = conn.execute(
        """SELECT DISTINCT date, project_id FROM session_briefs WHERE date != ''"""
    ).fetchall()
    for r in rows:
        if r["date"] in date_set:
            date_projects[r["date"]].add(r["project_id"])

    # Build date -> entities (entity_id -> entity_info) from brief_entities
    date_entities: dict[str, dict[str, dict]] = defaultdict(dict)
    erows = conn.execute(
        """SELECT be.date, be.entity_id, e.name, e.type, e.canonical_name
           FROM brief_entities be
           JOIN entities e ON e.id = be.entity_id
           WHERE be.date != ''"""
    ).fetchall()
    for r in erows:
        if r["date"] in date_set:
            ename_lower = (r["name"] or "").lower()
            if ename_lower not in _ENTITY_BLOCKLIST:
                date_entities[r["date"]][r["entity_id"]] = {
                    "name": r["name"],
                    "type": r["type"],
                    "canonical_name": r["canonical_name"],
                }

    # Build date -> tags from session_briefs
    date_tags: dict[str, set[str]] = defaultdict(set)
    brows = conn.execute(
        """SELECT date, brief_json FROM session_briefs WHERE date != ''"""
    ).fetchall()
    for r in brows:
        if r["date"] not in date_set:
            continue
        try:
            brief = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        for tag in (brief.get("tags") or []):
            if not isinstance(tag, str):
                continue
            tag = tag.strip().lower()
            if tag and tag not in _TAG_BLOCKLIST:
                date_tags[r["date"]].add(tag)

    result: dict[str, list[dict]] = {}

    for date in dates:
        if date in suppressed_dates:
            continue
        day_pids = date_projects.get(date, set())
        nudges: list[dict] = []
        seen_signals: set[str] = set()  # avoid duplicate signal names

        # --- Entity nudges ---
        for eid, einfo in date_entities.get(date, {}).items():
            if eid not in entity_map:
                continue
            ename = einfo["name"]
            if ename in seen_signals:
                continue
            proj_map = entity_map[eid]
            # Find other projects (not active today) with substantial history
            other_candidates = []
            for other_pid, other_entry in proj_map.items():
                if other_pid in day_pids:
                    continue  # same project — not a cross-project signal
                active_dates = {d for d in other_entry["dates"] if d not in suppressed_dates}
                if len(active_dates) < _DAILY_NUDGE_THRESHOLD:
                    continue
                top_learning = other_entry["learnings"][0] if other_entry["learnings"] else ""
                other_candidates.append({
                    "project_id": other_pid,
                    "project_name": other_entry["project_name"],
                    "date_count": len(active_dates),
                    "top_learning": top_learning,
                })
            if not other_candidates:
                continue
            other_candidates.sort(key=lambda x: -x["date_count"])
            best = other_candidates[0]
            import urllib.parse
            pname_enc = urllib.parse.quote(best["project_name"], safe="")
            seen_signals.add(ename)
            nudges.append({
                "signal_type": "entity",
                "name": ename,
                "entity_type": einfo["type"],
                "other_project": best["project_name"],
                "other_project_id": best["project_id"],
                "other_project_date_count": best["date_count"],
                "top_learning": best["top_learning"],
                "arc_url": f"projects/{pname_enc}/index.html",
                "total_other_projects": len(other_candidates),
            })

        # --- Tag nudges ---
        for tag in date_tags.get(date, set()):
            if tag in seen_signals:
                continue
            if tag not in tag_map:
                continue
            proj_map = tag_map[tag]
            other_candidates = []
            for other_pid, other_entry in proj_map.items():
                if other_pid in day_pids:
                    continue
                active_dates = {d for d in other_entry["dates"] if d not in suppressed_dates}
                if len(active_dates) < _DAILY_NUDGE_THRESHOLD:
                    continue
                top_learning = other_entry["learnings"][0] if other_entry["learnings"] else ""
                other_candidates.append({
                    "project_id": other_pid,
                    "project_name": other_entry["project_name"],
                    "date_count": len(active_dates),
                    "top_learning": top_learning,
                })
            if not other_candidates:
                continue
            other_candidates.sort(key=lambda x: -x["date_count"])
            best = other_candidates[0]
            import urllib.parse
            pname_enc = urllib.parse.quote(best["project_name"], safe="")
            seen_signals.add(tag)
            nudges.append({
                "signal_type": "tag",
                "name": tag,
                "entity_type": None,
                "other_project": best["project_name"],
                "other_project_id": best["project_id"],
                "other_project_date_count": best["date_count"],
                "top_learning": best["top_learning"],
                "arc_url": f"projects/{pname_enc}/index.html",
                "total_other_projects": len(other_candidates),
            })

        if not nudges:
            continue

        # Rank: entities before tags, then by date_count of best other project
        nudges.sort(key=lambda n: (
            0 if n["signal_type"] == "entity" else 1,
            -n["other_project_date_count"],
        ))

        # Cap at MAX_DAILY_NUDGES
        result[date] = nudges[:MAX_DAILY_NUDGES]

    return result
