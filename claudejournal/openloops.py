"""Open loops extraction — Phase B, task B1.

Mines the `friction` field from all session_briefs and checks whether a
later brief (same project_id, or tag overlap) has a `wins` or `did` entry
with significant textual overlap. If no match is found the friction item is
classified as "open".

Resolution heuristic (v1, automatic):
  A friction is considered resolved when *any* later brief (on a strictly
  later date) that shares either the same project_id OR at least one common
  tag has a `wins` or `did` item whose significant-word set overlaps >= 3
  words with the friction text. "Significant" = not a stopword, length >= 4.

The resolution threshold is kept conservative (3 shared words) to minimize
false positives. Manual resolution via annotations arrives in Phase E (task E7).
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date_type
from datetime import datetime


# ---------------------------------------------------------------------------
# Stopword set — common short words excluded from overlap scoring
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "the", "and", "that", "this", "with", "from", "have", "been", "were",
    "when", "what", "which", "where", "while", "also", "into", "then",
    "than", "they", "them", "their", "some", "just", "more", "very",
    "will", "would", "could", "should", "does", "doing", "done", "used",
    "using", "make", "made", "made", "need", "needs", "needed", "cant",
    "dont", "didnt", "wasnt", "isnt", "arent", "wont", "but", "not",
    "for", "are", "was", "had", "has", "its", "all", "out", "one",
    "two", "three", "four", "five", "over", "about", "after", "before",
    "during", "between", "through", "because", "however", "therefore",
    "each", "other", "your", "our", "per", "via", "any", "may",
})


def _significant_words(text: str) -> set[str]:
    """Return the set of lowercase significant words (len >= 4, not stopword)."""
    import re
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in _STOPWORDS}


def _overlap(text_a: str, text_b: str) -> int:
    """Count shared significant words between two strings."""
    return len(_significant_words(text_a) & _significant_words(text_b))


def _age_days(brief_date: str) -> int:
    """Days between the brief date and today."""
    try:
        d = datetime.strptime(brief_date, "%Y-%m-%d").date()
        return (_date_type.today() - d).days
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Resolution check constants
# ---------------------------------------------------------------------------
_MIN_OVERLAP_WORDS = 3   # >= this many shared words → considered resolved


def _load_resolved_annotations(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Load manual resolution annotations (Phase E7) from the annotations table.

    Returns {date: [resolution_text, ...]} for all annotations that carry
    scope_tag='resolved', target_scope='daily', annotation_type='correction'.
    If the annotations table does not yet exist (old DB), returns an empty dict.

    Each entry in the list is the annotation text, which by convention is
    "Friction resolved: <friction text>" — the _overlap heuristic in
    compute_open_loops() matches it against the friction text.
    """
    try:
        rows = conn.execute(
            """SELECT target_key, text
               FROM annotations
               WHERE target_scope = 'daily'
                 AND annotation_type = 'correction'
                 AND scope_tag = 'resolved'"""
        ).fetchall()
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["target_key"], []).append(r["text"])
        return result
    except Exception:
        return {}  # annotations table not present on old DBs


def compute_open_loops(conn: sqlite3.Connection) -> list[dict]:
    """Return a list of open friction items from all session_briefs.

    Each returned dict has:
        date         (str)  — YYYY-MM-DD the friction was first recorded
        project_id   (str)  — project_id from session_briefs
        project_name (str)  — display_name from projects (or project_id fallback)
        friction     (str)  — the friction item text
        tags         (list) — tags from the brief
        age_days     (int)  — days since the brief date
        still_open   (bool) — always True (we only return open items)

    Items are sorted: oldest first within each project.

    Phase E7: also checks for manual resolution annotations (scope_tag='resolved')
    created via the "Mark resolved" UI button on loops.html. A friction with a
    matching resolution annotation is filtered out regardless of the automatic
    heuristic result.
    """
    import json

    # Build a lookup of project_id -> display_name
    proj_names: dict[str, str] = {
        r["id"]: (r["display_name"] or r["id"])
        for r in conn.execute("SELECT id, display_name FROM projects").fetchall()
    }

    # Load all briefs with non-empty friction, ordered by date ascending.
    # We load everything into memory — the dataset is tiny (hundreds of rows).
    rows = conn.execute(
        """SELECT session_id, date, project_id, brief_json
           FROM session_briefs
           WHERE date IS NOT NULL AND date != '' AND brief_json IS NOT NULL
           ORDER BY date ASC"""
    ).fetchall()

    # Parse and index; skip rows with no friction.
    briefs: list[dict] = []
    for r in rows:
        try:
            bj = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        friction_items = [f for f in (bj.get("friction") or []) if isinstance(f, str) and f.strip()]
        if not friction_items:
            continue
        tags = [t.strip().lower() for t in (bj.get("tags") or []) if isinstance(t, str) and t.strip()]
        briefs.append({
            "session_id": r["session_id"],
            "date": r["date"],
            "project_id": r["project_id"],
            "friction_items": friction_items,
            "tags": tags,
            "wins": [w for w in (bj.get("wins") or []) if isinstance(w, str)],
            "did":  [d for d in (bj.get("did")  or []) if isinstance(d, str)],
        })

    # Also load ALL briefs (including those without friction) to use as the
    # resolver pool — later briefs' wins/did fields can resolve earlier friction.
    all_briefs: list[dict] = []
    for r in conn.execute(
        """SELECT date, project_id, brief_json
           FROM session_briefs
           WHERE date IS NOT NULL AND date != '' AND brief_json IS NOT NULL
           ORDER BY date ASC"""
    ).fetchall():
        try:
            bj = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        tags = [t.strip().lower() for t in (bj.get("tags") or []) if isinstance(t, str) and t.strip()]
        all_briefs.append({
            "date": r["date"],
            "project_id": r["project_id"],
            "tags": tags,
            "wins": [w for w in (bj.get("wins") or []) if isinstance(w, str)],
            "did":  [d for d in (bj.get("did")  or []) if isinstance(d, str)],
        })

    # Phase E7: load manual resolution annotations indexed by date.
    # A friction with a matching annotation is treated as resolved, bypassing
    # the automatic heuristic entirely.
    resolved_annotations: dict[str, list[str]] = _load_resolved_annotations(conn)

    open_loops: list[dict] = []

    for brief in briefs:
        src_date = brief["date"]
        src_pid = brief["project_id"]
        src_tags = set(brief["tags"])

        for friction_text in brief["friction_items"]:
            resolved = False

            # Phase E7: check manual resolution annotations first.
            # Convention: annotation text is "Friction resolved: <friction text>".
            # We match by word overlap (>= _MIN_OVERLAP_WORDS) against the
            # annotation text, same heuristic as the automatic resolver.
            for ann_text in resolved_annotations.get(src_date, []):
                if _overlap(friction_text, ann_text) >= _MIN_OVERLAP_WORDS:
                    resolved = True
                    break

            if not resolved:
                # Automatic heuristic: check all *later* briefs for resolution
                for later in all_briefs:
                    if later["date"] <= src_date:
                        continue  # must be strictly later

                    # Must share project OR at least one tag
                    same_project = (later["project_id"] == src_pid)
                    tag_overlap = bool(src_tags & set(later["tags"]))
                    if not (same_project or tag_overlap):
                        continue

                    # Check wins and did items for textual overlap
                    for resolution_text in (later["wins"] + later["did"]):
                        if _overlap(friction_text, resolution_text) >= _MIN_OVERLAP_WORDS:
                            resolved = True
                            break
                    if resolved:
                        break

            if not resolved:
                open_loops.append({
                    "date": src_date,
                    "project_id": src_pid,
                    "project_name": proj_names.get(src_pid, src_pid),
                    "friction": friction_text,
                    "tags": brief["tags"],
                    "age_days": _age_days(src_date),
                    "still_open": True,
                })

    return open_loops
