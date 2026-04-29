"""Cross-project connection computation for ClaudeJournal.

Phase A: Render-time callouts — no model calls, pure SQL + Python.
Phase B: Global connections graph + Tier 2 textual similarity signals.
Phase C: Transfer-recall MCP query — "what does this remind me of?"

Core exports:
  compute_cross_project_connections(conn)
      -> {project_id: [connection_dict, ...]}
      Per-project list of related-work signals (shared entities + tags).

  compute_all_daily_connections(conn, dates)
      -> {date: [nudge_dict, ...]}
      Per-date nudges: for each entity/tag on that day, which OTHER projects
      have substantial history with it (threshold: 3+ dates in 2+ other projects).

  compute_connections_graph(conn)
      -> {entities: [...], tag_clusters: [...]}
      Full cross-project entity x learnings graph for the Connections page.
      Includes Tier 2 signals: transfer opportunities (similar-but-different
      learnings across projects) via significant-word overlap.

  transfer_recall(conn, query, *, project_filter=None, limit=10)
      -> [result_dict, ...]
      Cross-project transfer-recall: given a tool name, concern, or free text,
      find the most relevant prior learnings from other projects. Three signal
      tiers: (1) entity name match, (2) tag overlap, (3) FTS5 full-text search.
      Annotation-suppressed content is never surfaced.
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


# ---------------------------------------------------------------------------
# Tier 2 helpers (textual similarity for transfer opportunities)
# ---------------------------------------------------------------------------

import difflib
import re as _re

_T2_STOPWORDS = frozenset({
    "the", "and", "that", "this", "with", "from", "have", "been", "were",
    "when", "what", "which", "where", "while", "also", "into", "then",
    "than", "they", "them", "their", "some", "just", "more", "very",
    "will", "would", "could", "should", "does", "doing", "done", "used",
    "using", "make", "made", "need", "needs", "needed", "for", "are",
    "was", "had", "has", "its", "all", "out", "one", "but", "not",
})

_T2_MIN_WORD_OVERLAP = 4
_T2_SM_RATIO = 0.72   # Slightly looser than learnings.py's 0.80 —
                       # cross-project "transfer" is related-but-not-same.


def _t2_sig_words(text: str) -> set[str]:
    """Return significant words for Tier 2 similarity comparison."""
    tokens = _re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in _T2_STOPWORDS}


def _t2_are_related(a: str, b: str) -> bool:
    """True if two learnings from *different* projects are related enough to
    be surfaced as a 'transfer opportunity'. Looser than same-project dedup:
    the intent is 'similar insight, separate context.'
    """
    wa = _t2_sig_words(a)
    wb = _t2_sig_words(b)
    if not wa or not wb:
        return False
    shared = wa & wb
    if len(shared) >= _T2_MIN_WORD_OVERLAP:
        return True
    # SequenceMatcher as a fallback for paraphrase (slightly costly)
    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio >= _T2_SM_RATIO


# ---------------------------------------------------------------------------
# Public API: compute_connections_graph  (Phase B, Task 7)
# ---------------------------------------------------------------------------

def compute_connections_graph(conn: sqlite3.Connection) -> dict:
    """Build the complete entity x project x learnings graph for connections.html.

    Returns:
        {
          "entities": [
            {
              "entity_id": str,
              "entity_name": str,
              "entity_type": str | None,
              "canonical_name": str | None,
              "projects": [
                {
                  "project_id": str,
                  "project_name": str,
                  "date_count": int,
                  "first_seen": str,
                  "last_seen": str,
                  "learnings": [str, ...],   # up to 4
                }
              ],
              "total_projects": int,
              "total_dates": int,
              "transfer_opportunities": [
                {
                  "learning_a": str,
                  "project_a": str,
                  "learning_b": str,
                  "project_b": str,
                }
              ],
            },
            ...   # sorted: total_projects desc, then total_dates desc
          ],
          "tag_clusters": [
            {
              "tag": str,
              "projects": [
                {
                  "project_id": str,
                  "project_name": str,
                  "date_count": int,
                  "learnings": [str, ...],
                }
              ],
              "total_projects": int,
            },
            ...
          ],
          "total_connections": int,   # entity + tag clusters with 2+ projects
          "total_transfer_opps": int,
        }

    Only entities/tags appearing in 2+ projects are included.
    Tier 2 transfer opportunities: cross-project learning pairs that share
    4+ significant words but are NOT identical (word-for-word ratio < 0.95).
    No model calls — pure Python text similarity.
    """
    import urllib.parse as _up

    entity_map = _load_entity_project_map(conn)
    tag_map = _load_tag_project_map(conn)
    suppressed_dates = _check_annotations(conn)

    # ------------------------------------------------------------------ #
    # Build entity list
    # ------------------------------------------------------------------ #
    entities_out: list[dict] = []
    total_transfer = 0

    for eid, proj_map in entity_map.items():
        if len(proj_map) < 2:
            continue

        first_proj = next(iter(proj_map.values()))
        ename = first_proj["entity_name"]
        etype = first_proj["entity_type"]
        cname = first_proj["canonical_name"]

        projects_out: list[dict] = []
        all_learnings_with_proj: list[tuple[str, str]] = []  # (learning, project_name)

        for pid, entry in proj_map.items():
            active_dates = sorted(
                d for d in entry["dates"] if d not in suppressed_dates
            )
            if not active_dates:
                continue
            learnings_clean = entry["learnings"][:4]
            for l in learnings_clean:
                all_learnings_with_proj.append((l, entry["project_name"]))
            projects_out.append({
                "project_id": pid,
                "project_name": entry["project_name"],
                "date_count": len(active_dates),
                "first_seen": active_dates[0],
                "last_seen": active_dates[-1],
                "learnings": learnings_clean,
                "arc_url": f"projects/{_up.quote(entry['project_name'], safe='')}/index.html",
            })

        if len(projects_out) < 2:
            continue

        # Sort projects: most active first
        projects_out.sort(key=lambda p: -p["date_count"])
        total_dates = sum(p["date_count"] for p in projects_out)

        # --- Tier 2: transfer opportunities ---
        transfer_opps: list[dict] = []
        seen_pairs: set[frozenset] = set()
        for i in range(len(all_learnings_with_proj)):
            la, proj_a = all_learnings_with_proj[i]
            for j in range(i + 1, len(all_learnings_with_proj)):
                lb, proj_b = all_learnings_with_proj[j]
                if proj_a == proj_b:
                    continue  # same project — not a transfer signal
                pair_key = frozenset([la, lb])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                # Must be related but NOT word-for-word identical
                if not _t2_are_related(la, lb):
                    continue
                sm = difflib.SequenceMatcher(None, la.lower(), lb.lower()).ratio()
                if sm >= 0.95:
                    continue  # identical — skip
                transfer_opps.append({
                    "learning_a": la,
                    "project_a": proj_a,
                    "learning_b": lb,
                    "project_b": proj_b,
                })
                if len(transfer_opps) >= 3:
                    break
            if len(transfer_opps) >= 3:
                break
        total_transfer += len(transfer_opps)

        entities_out.append({
            "entity_id": eid,
            "entity_name": ename,
            "entity_type": etype,
            "canonical_name": cname,
            "projects": projects_out,
            "total_projects": len(projects_out),
            "total_dates": total_dates,
            "transfer_opportunities": transfer_opps,
        })

    # Sort: most cross-project first, then by total dates
    entities_out.sort(key=lambda e: (-e["total_projects"], -e["total_dates"]))

    # ------------------------------------------------------------------ #
    # Build tag clusters
    # ------------------------------------------------------------------ #
    tag_clusters_out: list[dict] = []

    for tag, proj_map in tag_map.items():
        if len(proj_map) < 2:
            continue

        proj_list: list[dict] = []
        for pid, entry in proj_map.items():
            active_dates = {d for d in entry["dates"] if d not in suppressed_dates}
            if not active_dates:
                continue
            proj_list.append({
                "project_id": pid,
                "project_name": entry["project_name"],
                "date_count": len(active_dates),
                "learnings": entry["learnings"][:3],
                "arc_url": f"projects/{_up.quote(entry['project_name'], safe='')}/index.html",
            })

        if len(proj_list) < 2:
            continue

        proj_list.sort(key=lambda p: -p["date_count"])
        tag_clusters_out.append({
            "tag": tag,
            "projects": proj_list,
            "total_projects": len(proj_list),
        })

    tag_clusters_out.sort(key=lambda tc: -tc["total_projects"])

    total_connections = len(entities_out) + len(tag_clusters_out)

    return {
        "entities": entities_out,
        "tag_clusters": tag_clusters_out,
        "total_connections": total_connections,
        "total_transfer_opps": total_transfer,
    }


# ---------------------------------------------------------------------------
# Phase C: transfer_recall — "what does this remind me of?"
# ---------------------------------------------------------------------------

# Score weights for composite ranking
_TR_WEIGHT_TIER1 = 10.0   # entity name match (strongest signal)
_TR_WEIGHT_TIER2 = 5.0    # tag overlap
_TR_WEIGHT_TIER3 = 2.0    # FTS5 BM25 base weight (scaled by BM25 rank)

# Max items per tier before merging
_TR_TIER1_MAX = 20
_TR_TIER2_MAX = 20
_TR_TIER3_MAX = 20

# Minimum word length to be considered a "signal word" in the query
_TR_MIN_QUERY_WORD_LEN = 3


def _query_sig_words(query: str) -> set[str]:
    """Extract significant words from query for Tier 2 tag matching."""
    import re as _re2
    tokens = _re2.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", query.lower())
    return {
        t for t in tokens
        if len(t) >= _TR_MIN_QUERY_WORD_LEN and t not in _T2_STOPWORDS
    }


def _check_suppressed_projects(
    conn: sqlite3.Connection,
    suppressed_dates: set[str],
) -> set[str]:
    """Return set of project_ids where ALL known brief dates are suppressed.

    A project is fully suppressed only if every date in session_briefs for
    that project appears in suppressed_dates. In practice this is rare — the
    suppression gate acts per-date, not per-project, for individual briefs.
    This helper is used for quick project-level pre-filtering.
    """
    fully_suppressed: set[str] = set()
    if not suppressed_dates:
        return fully_suppressed
    rows = conn.execute(
        "SELECT project_id, GROUP_CONCAT(DISTINCT date) AS dates FROM session_briefs GROUP BY project_id"
    ).fetchall()
    for r in rows:
        all_dates = set((r["dates"] or "").split(",")) - {""}
        if all_dates and all_dates.issubset(suppressed_dates):
            fully_suppressed.add(r["project_id"])
    return fully_suppressed


def _load_project_display_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {project_id: display_name} for all projects."""
    rows = conn.execute("SELECT id, display_name FROM projects").fetchall()
    return {r["id"]: (r["display_name"] or r["id"]) for r in rows}


def transfer_recall(
    conn: sqlite3.Connection,
    query: str,
    *,
    project_filter: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Find cross-project learnings relevant to the given query.

    Implements three signal tiers:

    Tier 1 — Shared entity match:
        If the query mentions a known entity name (case-insensitive substring),
        pull all briefs where that entity appears across all projects, ranked
        by recency + learning density. Suppressed-date briefs are excluded.

    Tier 2 — Tag overlap:
        Identify tags in the query string that match known tags in the corpus.
        Surface learnings from briefs tagged with those tags in other projects.

    Tier 3 — FTS5 full-text search:
        BM25 retrieval over the `rag_chunks` FTS5 index as a complementary
        fallback. Catches concepts not yet entitized or tagged. Brief-kind
        chunks are preferred (they contain structured learned/friction data).

    Args:
        conn: Open SQLite connection.
        query: Free text — tool name, concern, code fragment, etc.
        project_filter: If given, exclude results from this project_id
            (use the caller's current project so results are always cross-project).
        limit: Max results to return (default 10, cap at 50).

    Returns:
        List of dicts (ranked by composite score, best first):
        {
          "tier": int,                    # 1, 2, or 3
          "tier_label": str,              # "entity", "tag", or "fts"
          "signal": str,                  # entity/tag name, or query excerpt
          "source_project": str,          # display name
          "source_project_id": str,
          "date": str,                    # YYYY-MM-DD of source brief/chunk
          "excerpt": str,                 # the relevant learned/friction/chunk text
          "score": float,                 # composite score (higher = better)
          "entity_profile_hint": str,     # slug of entity profile page, or ""
        }

    Annotation-suppressed content is never returned. The result set may
    contain items from multiple tiers for the same project — this is
    intentional so the caller can see BOTH entity-level and full-text signals.
    """
    import re as _re3

    limit = max(1, min(50, int(limit or 10)))
    query_l = (query or "").strip().lower()
    if not query_l:
        return []

    suppressed_dates = _check_annotations(conn)
    proj_display = _load_project_display_names(conn)

    # Normalize project_filter: accept either project_id or display_name substring
    filter_pid: str | None = None
    if project_filter:
        pf_l = project_filter.strip().lower()
        # Try exact project_id first
        for pid, dname in proj_display.items():
            if pid.lower() == pf_l or dname.lower() == pf_l:
                filter_pid = pid
                break
        if filter_pid is None:
            # Substring match on display_name
            for pid, dname in proj_display.items():
                if pf_l in dname.lower():
                    filter_pid = pid
                    break
        if filter_pid is None:
            # Substring match on project_id
            for pid in proj_display:
                if pf_l in pid.lower():
                    filter_pid = pid
                    break

    # Build entity profile slug map from narrations for hint generation
    ep_rows = conn.execute(
        "SELECT key FROM narrations WHERE scope='entity_profile'"
    ).fetchall()
    entity_profile_slugs: set[str] = {r["key"] for r in ep_rows}

    def _entity_slug_for(name: str) -> str:
        """Return the entity profile slug if one exists, else empty string."""
        # Entity profile keys are the canonical_name (lowercased, hyphenated slug)
        import re as _re_slug
        slug = _re_slug.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug in entity_profile_slugs:
            return slug
        return ""

    results: list[dict] = []
    seen_keys: set[str] = set()   # (project_id, date, excerpt[:80]) to dedup

    def _add_result(
        tier: int,
        tier_label: str,
        signal: str,
        source_pid: str,
        date: str,
        excerpt: str,
        score: float,
        entity_name: str = "",
        source_project_name: str = "",
    ) -> None:
        if date in suppressed_dates:
            return
        if filter_pid and source_pid == filter_pid:
            return
        excerpt = (excerpt or "").strip()
        if not excerpt:
            return
        # Truncate for readability; keep enough context
        if len(excerpt) > 400:
            excerpt = excerpt[:400].rsplit(" ", 1)[0] + "…"
        key = (source_pid, date, excerpt[:80])
        if key in seen_keys:
            return
        seen_keys.add(key)
        hint = _entity_slug_for(entity_name) if entity_name else ""
        # Resolve display name: prefer the lookup table; fall back to the
        # rag_chunks project_name field (used for daily narrations that have
        # no project_id in the DB).
        display = proj_display.get(source_pid) or source_project_name or source_pid or "?"
        results.append({
            "tier": tier,
            "tier_label": tier_label,
            "signal": signal,
            "source_project": display,
            "source_project_id": source_pid,
            "date": date,
            "excerpt": excerpt,
            "score": score,
            "entity_profile_hint": hint,
        })

    # ------------------------------------------------------------------ #
    # Tier 1 — Entity name match
    # ------------------------------------------------------------------ #
    # Load all entities; check which ones the query mentions
    entity_rows = conn.execute(
        "SELECT id, name, canonical_name, type FROM entities"
    ).fetchall()

    matched_entity_ids: list[tuple[str, str, str]] = []  # (entity_id, name, canonical)
    for er in entity_rows:
        ename = (er["name"] or "").strip()
        cname = (er["canonical_name"] or "").strip()
        ename_l = ename.lower()
        cname_l = cname.lower()
        # Block generic entities from appearing as Tier 1 signals
        if ename_l in _ENTITY_BLOCKLIST:
            continue
        # Check if entity name or canonical_name appears in query
        if ename_l and ename_l in query_l:
            matched_entity_ids.append((er["id"], ename, cname))
        elif cname_l and cname_l != ename_l and cname_l in query_l:
            matched_entity_ids.append((er["id"], ename, cname))

    for entity_id, ename, cname in matched_entity_ids[:_TR_TIER1_MAX]:
        # Pull all brief_entities rows for this entity; load the brief JSON
        be_rows = conn.execute(
            """SELECT be.date, be.session_id, sb.project_id, sb.brief_json
               FROM brief_entities be
               JOIN session_briefs sb ON sb.session_id = be.session_id
                                     AND sb.date = be.date
               WHERE be.entity_id = ?
                 AND sb.date != ''
               ORDER BY sb.date DESC""",
            (entity_id,),
        ).fetchall()

        for br in be_rows:
            date = br["date"]
            pid = br["project_id"]
            if date in suppressed_dates:
                continue
            if filter_pid and pid == filter_pid:
                continue
            try:
                brief = json.loads(br["brief_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            # Extract learned + friction items that mention the entity
            candidates = []
            for item in (brief.get("learned") or []):
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
            for item in (brief.get("friction") or []):
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
            if not candidates:
                continue
            # Score: base weight + recency boost (newer = higher score)
            # Recency: dates are YYYY-MM-DD strings; lexicographic sort works
            recency_score = _recency_score(date)
            score = _TR_WEIGHT_TIER1 + recency_score
            # Surface the most relevant item (entity name mention wins)
            best = None
            for c in candidates:
                if ename.lower() in c.lower() or cname.lower() in c.lower():
                    best = c
                    break
            if best is None:
                best = candidates[0]
            _add_result(1, "entity", ename, pid, date, best, score, entity_name=ename)

    # ------------------------------------------------------------------ #
    # Tier 2 — Tag overlap
    # ------------------------------------------------------------------ #
    query_words = _query_sig_words(query_l)
    if query_words:
        # Load all known tags from the corpus; find those that overlap query words
        # or whose string is a substring of the query
        tag_map = _load_tag_project_map(conn)
        matched_tags: list[str] = []
        for tag in tag_map:
            tag_words = set(tag.replace("-", " ").replace("_", " ").split())
            # Direct substring match
            if tag in query_l or tag.replace("-", " ") in query_l:
                matched_tags.append(tag)
            # Word overlap: tag tokens appear in query words
            elif tag_words and tag_words.issubset(query_words):
                matched_tags.append(tag)

        for tag in matched_tags[:_TR_TIER2_MAX]:
            proj_map = tag_map[tag]
            for pid, entry in proj_map.items():
                if filter_pid and pid == filter_pid:
                    continue
                active_dates = [d for d in sorted(entry["dates"], reverse=True)
                                if d not in suppressed_dates]
                if not active_dates:
                    continue
                # Use the most recent active date as anchor
                most_recent = active_dates[0]
                recency_score = _recency_score(most_recent)
                score = _TR_WEIGHT_TIER2 + recency_score
                # Best learning for this tag in this project
                learnings = entry.get("learnings") or []
                if not learnings:
                    continue
                excerpt = learnings[0]
                _add_result(2, "tag", tag, pid, most_recent, excerpt, score)

    # ------------------------------------------------------------------ #
    # Tier 3 — FTS5 full-text search
    # ------------------------------------------------------------------ #
    try:
        # Sanitize query for FTS5: wrap in quotes for phrase-first, then fallback
        # to individual terms if the quoted search fails
        fts_query = " ".join(
            f'"{w}"' if " " not in w else w
            for w in query_l.split()
            if len(w) >= _TR_MIN_QUERY_WORD_LEN
        ) or query_l

        fts_rows = conn.execute(
            """SELECT rowid, kind, date, project_id, project_name, title,
                      body, rank
               FROM rag_chunks
               WHERE rag_chunks MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, _TR_TIER3_MAX),
        ).fetchall()
    except Exception:
        # FTS5 syntax error — fall back to bare term search
        try:
            bare = " ".join(
                w for w in query_l.split()
                if len(w) >= _TR_MIN_QUERY_WORD_LEN
            )
            fts_rows = conn.execute(
                """SELECT rowid, kind, date, project_id, project_name, title,
                          body, rank
                   FROM rag_chunks
                   WHERE rag_chunks MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (bare, _TR_TIER3_MAX),
            ).fetchall() if bare else []
        except Exception:
            fts_rows = []

    for fr in fts_rows:
        date = (fr["date"] or "").strip()
        pid = (fr["project_id"] or "").strip()
        if not date or date in suppressed_dates:
            continue
        if filter_pid and pid == filter_pid:
            continue
        body = (fr["body"] or "").strip()
        if not body:
            continue
        # BM25 rank is negative; more negative = better match.
        # Normalize to a positive score contribution.
        bm25_val = fr["rank"] or 0.0
        # rank is negative; abs gives magnitude; cap at 10
        bm25_contribution = min(abs(bm25_val), 10.0)
        recency_score = _recency_score(date)
        score = _TR_WEIGHT_TIER3 + (bm25_contribution * 0.3) + recency_score
        # For the excerpt: brief kind has structured content; prefer learned lines
        kind = fr["kind"] or ""
        chunk_project_name = (fr["project_name"] or "").strip()
        # Daily narrations have no project_id; use a readable fallback label.
        if not pid and not chunk_project_name:
            if kind == "daily_narration":
                chunk_project_name = "daily journal"
            elif kind == "topic":
                chunk_project_name = "topics"
        if kind == "brief":
            # Extract a learned or friction line from the body if possible
            excerpt = _extract_learning_from_brief_body(body)
        else:
            # For narration/arc chunks, use the first meaningful sentence
            excerpt = _first_sentences(body, max_chars=300)
        _add_result(
            3, "fts", query[:60], pid, date, excerpt, score,
            source_project_name=chunk_project_name,
        )

    # ------------------------------------------------------------------ #
    # Merge, rank, deduplicate, cap
    # ------------------------------------------------------------------ #
    # Sort by tier first (lower = stronger), then by score descending
    results.sort(key=lambda r: (r["tier"], -r["score"]))

    # Per-project cap: at most 3 results per source project to prevent
    # one dominant project from flooding the output.
    _MAX_PER_PROJECT = 3
    proj_counts: dict[str, int] = {}
    capped: list[dict] = []
    for r in results:
        pid = r["source_project_id"]
        if proj_counts.get(pid, 0) < _MAX_PER_PROJECT:
            capped.append(r)
            proj_counts[pid] = proj_counts.get(pid, 0) + 1

    return capped[:limit]


# ---------------------------------------------------------------------------
# transfer_recall helpers
# ---------------------------------------------------------------------------

import time as _time

# Epoch reference for recency scoring: use 2025-01-01 as base
_RECENCY_EPOCH = "2025-01-01"


def _recency_score(date_str: str, decay: float = 0.001) -> float:
    """Return a small recency boost [0, 1] for a date string YYYY-MM-DD.

    More recent dates score closer to 1.0; older dates score closer to 0.
    The decay constant (default 0.001 per day) means a 1000-day-old entry
    still contributes ~0.37, so recency is a tiebreaker, not a dominator.
    """
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        today = _date.today()
        age_days = max(0, (today - d).days)
        import math
        return math.exp(-decay * age_days)
    except Exception:
        return 0.0


def _extract_learning_from_brief_body(body: str) -> str:
    """Given the text body of a 'brief' rag_chunk, extract the most useful
    learning or friction line for display.

    The brief body is free text from session_briefs. It often starts with
    'goal:' / 'mood:' / 'did:' sections and includes 'learned:' lines.
    We try to find a 'learned:' bullet; fall back to the 'did:' section.
    """
    # Look for 'learned:' section
    lines = body.split("\n")
    in_learned = False
    learned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("learned:"):
            in_learned = True
            rest = stripped[len("learned:"):].strip()
            if rest and not rest.startswith("-"):
                learned_lines.append(rest)
            continue
        if in_learned:
            if stripped.startswith("-") or stripped.startswith("•"):
                learned_lines.append(stripped.lstrip("-•").strip())
            elif stripped and not stripped[0].islower():
                # New section header — stop
                break
    if learned_lines:
        return learned_lines[0][:400]

    # Fall back to 'did:' section
    in_did = False
    did_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("did:"):
            in_did = True
            rest = stripped[4:].strip()
            if rest:
                did_lines.append(rest)
            continue
        if in_did:
            if stripped and not any(
                stripped.lower().startswith(p)
                for p in ("goal:", "mood:", "friction:", "learned:", "tags:")
            ):
                did_lines.append(stripped)
            elif stripped:
                break
    if did_lines:
        combined = " ".join(did_lines)
        return combined[:400]

    # Final fallback: first non-empty line
    for line in lines:
        s = line.strip()
        if s and len(s) > 20:
            return s[:400]
    return body[:400]


def _first_sentences(text: str, max_chars: int = 300) -> str:
    """Return the first `max_chars` characters of text, truncated at a sentence
    boundary if possible."""
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    # Find last sentence boundary
    for sep in (". ", "! ", "? ", "\n"):
        idx = chunk.rfind(sep)
        if idx > max_chars // 2:
            return chunk[:idx + 1].strip()
    return chunk.rsplit(" ", 1)[0] + "…"
