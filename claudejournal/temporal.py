"""Temporal echoes computation — Phase D, task D1.

Surface three kinds of memory signal for any given date:

  (a) prior_years     — same month-day in earlier years (any narration or brief activity)
  (b) recurring_friction — friction tags that appear on 3+ separate dates across the journal
  (c) milestones      — projects whose round-number anniversary (30/90/180/365 days) falls
                        on or near the target date (within 2 days either side)

All three are render-only; no new DB tables, no new LLM calls.

Performance note (plan Risk #5): `compute_all_echoes(conn)` pre-computes echoes for
every active date in one pass and returns a dict keyed by date string.  Call this once
per render cycle and cache the result; do NOT call `compute_echoes()` per-day inside
the render loop.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date as _date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> _date | None:
    """Parse YYYY-MM-DD string to date; return None on failure."""
    if not s or len(s) < 10:
        return None
    try:
        return _date.fromisoformat(s[:10])
    except ValueError:
        return None


def _snippet(prose: str, max_chars: int = 80) -> str:
    """First sentence or first max_chars characters of prose, whichever is shorter."""
    if not prose:
        return ""
    # Try to extract the first sentence
    for sep in (". ", ".\n", "! ", "? "):
        idx = prose.find(sep)
        if 0 < idx < max_chars:
            return prose[: idx + 1].strip()
    # Fall back to first max_chars chars
    text = prose[:max_chars].strip()
    if len(prose) > max_chars:
        text += "…"
    return text


# ---------------------------------------------------------------------------
# Stopword set (reused from openloops; kept local to avoid circular import)
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "the", "and", "that", "this", "with", "from", "have", "been", "were",
    "when", "what", "which", "where", "while", "also", "into", "then",
    "than", "they", "them", "their", "some", "just", "more", "very",
    "will", "would", "could", "should", "does", "doing", "done", "used",
    "using", "make", "made", "need", "needs", "needed", "cant",
    "dont", "didnt", "wasnt", "isnt", "arent", "wont", "but", "not",
    "for", "are", "was", "had", "has", "its", "all", "out", "one",
    "two", "three", "four", "five", "over", "about", "after", "before",
    "during", "between", "through", "because", "however", "therefore",
    "each", "other", "your", "our", "per", "via", "any", "may",
})


def _sig_words(text: str) -> set[str]:
    """Lowercase significant words (len >= 4, not a stopword)."""
    import re
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in _STOPWORDS}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_echoes(conn: sqlite3.Connection, target_date: str) -> dict:
    """Compute temporal echoes for a single date.

    Returns::

        {
            "prior_years":          [{date, snippet, year_diff}],
            "recurring_friction":   [{tag, count, dates, example_friction}],
            "milestones":           [{project, project_name, days, label, first_seen}],
        }

    All three lists may be empty.  Callers SHOULD check `any(...)` across the
    three lists before rendering a banner — an all-empty result means no echoes.

    For bulk rendering use `compute_all_echoes()` instead of calling this per-day.
    """
    td = _parse_date(target_date)
    if td is None:
        return {"prior_years": [], "recurring_friction": [], "milestones": []}

    month_day = f"{td.month:02d}-{td.day:02d}"  # "04-27"

    # --- (a) Prior years — same MM-DD in earlier years -------------------------
    prior_years: list[dict] = []
    rows = conn.execute(
        """SELECT date, prose
           FROM narrations
           WHERE scope = 'daily'
             AND substr(date, 6, 5) = ?
             AND date < ?
           ORDER BY date DESC""",
        (month_day, target_date),
    ).fetchall()
    for r in rows:
        d = _parse_date(r["date"])
        if d is None:
            continue
        year_diff = td.year - d.year
        prior_years.append({
            "date": r["date"],
            "snippet": _snippet(r["prose"] or ""),
            "year_diff": year_diff,
        })

    # Fall back to session_briefs activity if no narration existed for a prior-year date
    if not prior_years:
        brief_rows = conn.execute(
            """SELECT DISTINCT date
               FROM session_briefs
               WHERE substr(date, 6, 5) = ?
                 AND date < ?
               ORDER BY date DESC""",
            (month_day, target_date),
        ).fetchall()
        for r in brief_rows:
            d = _parse_date(r["date"])
            if d is None:
                continue
            year_diff = td.year - d.year
            prior_years.append({
                "date": r["date"],
                "snippet": "",
                "year_diff": year_diff,
            })

    # --- (b) Recurring friction — tags with friction on 3+ distinct dates ------
    # We need to know today's tags to filter to patterns that are relevant.
    today_tags: set[str] = set()
    today_brief_rows = conn.execute(
        """SELECT brief_json FROM session_briefs
           WHERE date = ? AND brief_json IS NOT NULL""",
        (target_date,),
    ).fetchall()
    for r in today_brief_rows:
        try:
            bj = json.loads(r["brief_json"])
            for t in (bj.get("tags") or []):
                if isinstance(t, str) and t.strip():
                    today_tags.add(t.strip().lower())
        except (json.JSONDecodeError, TypeError):
            pass

    recurring_friction: list[dict] = []
    if today_tags:
        # Build tag -> list of (date, friction_text) across all history
        tag_frictions: dict[str, list[tuple[str, str]]] = defaultdict(list)
        all_brief_rows = conn.execute(
            """SELECT date, brief_json
               FROM session_briefs
               WHERE brief_json IS NOT NULL AND date IS NOT NULL AND date != ''
               ORDER BY date ASC"""
        ).fetchall()
        for r in all_brief_rows:
            try:
                bj = json.loads(r["brief_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            friction_items = [f for f in (bj.get("friction") or []) if isinstance(f, str) and f.strip()]
            if not friction_items:
                continue
            tags = [t.strip().lower() for t in (bj.get("tags") or []) if isinstance(t, str) and t.strip()]
            for tag in tags:
                if tag in today_tags:
                    for fi in friction_items:
                        tag_frictions[tag].append((r["date"], fi))

        for tag, occurrences in tag_frictions.items():
            # Deduplicate by date for the count
            unique_dates = sorted({occ[0] for occ in occurrences})
            if len(unique_dates) >= 3:
                # Use the most recent friction example
                latest = occurrences[-1]
                recurring_friction.append({
                    "tag": tag,
                    "count": len(unique_dates),
                    "dates": unique_dates,
                    "example_friction": latest[1],
                })
        # Sort by count descending, then tag name
        recurring_friction.sort(key=lambda x: (-x["count"], x["tag"]))

    # --- (c) Milestones — round-number anniversaries of project first_seen -----
    milestones: list[dict] = []
    _MILESTONE_DAYS = [30, 60, 90, 180, 365, 730]
    _WINDOW = 2  # days either side counts as "proximity"

    project_rows = conn.execute(
        "SELECT id, display_name, first_seen FROM projects WHERE first_seen IS NOT NULL AND first_seen != ''"
    ).fetchall()
    for pr in project_rows:
        fs = _parse_date(pr["first_seen"])
        if fs is None:
            continue
        if fs >= td:
            continue  # not yet past first_seen
        delta = (td - fs).days
        for milestone in _MILESTONE_DAYS:
            diff = abs(delta - milestone)
            if diff <= _WINDOW:
                years = milestone // 365
                months = (milestone % 365) // 30
                if years >= 1:
                    label = f"{years} year{'s' if years != 1 else ''} old"
                elif months >= 1:
                    label = f"{months} month{'s' if months != 1 else ''} old"
                else:
                    label = f"{milestone} days old"
                milestones.append({
                    "project": pr["id"],
                    "project_name": pr["display_name"] or pr["id"],
                    "days": delta,
                    "label": label,
                    "first_seen": pr["first_seen"],
                })
                break  # only the closest milestone per project per date

    return {
        "prior_years": prior_years,
        "recurring_friction": recurring_friction,
        "milestones": milestones,
    }


def compute_all_echoes(conn: sqlite3.Connection,
                       dates: list[str] | None = None) -> dict[str, dict]:
    """Pre-compute echoes for every date in `dates` (or all active dates).

    Returns {date_str: echoes_dict} where echoes_dict is the same shape as
    `compute_echoes()` returns.  Only dates that have at least one echo signal
    appear in the result dict — dates with no echoes are omitted, so callers
    can use a simple `if date in all_echoes_map` guard.

    This is the recommended call site inside render_site() to avoid N per-day
    queries.
    """
    if dates is None:
        rows = conn.execute(
            """SELECT DISTINCT date FROM session_briefs
               WHERE date IS NOT NULL AND date != ''
               ORDER BY date"""
        ).fetchall()
        dates = [r["date"] for r in rows]

    # Pre-load all narration prose keyed by (month_day, date) for fast lookup
    narr_by_date: dict[str, str] = {}
    for r in conn.execute(
        "SELECT date, prose FROM narrations WHERE scope='daily' AND date IS NOT NULL"
    ).fetchall():
        narr_by_date[r["date"]] = r["prose"] or ""

    # Pre-load all brief dates (for activity fallback) and their tags+friction
    brief_data: list[dict] = []  # [{date, tags, friction_items}]
    for r in conn.execute(
        """SELECT date, brief_json FROM session_briefs
           WHERE date IS NOT NULL AND date != '' AND brief_json IS NOT NULL
           ORDER BY date ASC"""
    ).fetchall():
        try:
            bj = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        tags = [t.strip().lower() for t in (bj.get("tags") or []) if isinstance(t, str) and t.strip()]
        friction_items = [f for f in (bj.get("friction") or []) if isinstance(f, str) and f.strip()]
        brief_data.append({
            "date": r["date"],
            "tags": tags,
            "friction_items": friction_items,
        })

    # Index briefs by date for today-tag lookups
    briefs_by_date: dict[str, list[dict]] = defaultdict(list)
    for bd in brief_data:
        briefs_by_date[bd["date"]].append(bd)

    # Build tag -> [(date, friction_text)] index for recurring friction
    tag_friction_index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for bd in brief_data:
        for tag in bd["tags"]:
            for fi in bd["friction_items"]:
                tag_friction_index[tag].append((bd["date"], fi))

    # Pre-load project first_seen for milestones
    project_rows = conn.execute(
        "SELECT id, display_name, first_seen FROM projects WHERE first_seen IS NOT NULL AND first_seen != ''"
    ).fetchall()
    projects = [
        {"id": r["id"], "name": r["display_name"] or r["id"], "first_seen": r["first_seen"]}
        for r in project_rows
    ]

    _MILESTONE_DAYS = [30, 60, 90, 180, 365, 730]
    _WINDOW = 2

    result: dict[str, dict] = {}

    for target_date in dates:
        td = _parse_date(target_date)
        if td is None:
            continue
        month_day = f"{td.month:02d}-{td.day:02d}"

        # (a) Prior years
        prior_years: list[dict] = []
        for d_str, prose in narr_by_date.items():
            d = _parse_date(d_str)
            if d is None:
                continue
            if f"{d.month:02d}-{d.day:02d}" != month_day:
                continue
            if d_str >= target_date:
                continue
            year_diff = td.year - d.year
            prior_years.append({
                "date": d_str,
                "snippet": _snippet(prose),
                "year_diff": year_diff,
            })
        if not prior_years:
            # Fallback: brief activity
            brief_dates_seen: set[str] = set()
            for bd in brief_data:
                d = _parse_date(bd["date"])
                if d is None:
                    continue
                if f"{d.month:02d}-{d.day:02d}" != month_day:
                    continue
                if bd["date"] >= target_date:
                    continue
                if bd["date"] in brief_dates_seen:
                    continue
                brief_dates_seen.add(bd["date"])
                year_diff = td.year - d.year
                prior_years.append({
                    "date": bd["date"],
                    "snippet": "",
                    "year_diff": year_diff,
                })
        prior_years.sort(key=lambda x: x["date"], reverse=True)

        # (b) Recurring friction
        today_tags: set[str] = set()
        for bd in briefs_by_date.get(target_date, []):
            today_tags.update(bd["tags"])

        recurring_friction: list[dict] = []
        if today_tags:
            for tag in today_tags:
                occurrences = tag_friction_index.get(tag, [])
                if not occurrences:
                    continue
                unique_dates = sorted({occ[0] for occ in occurrences})
                if len(unique_dates) >= 3:
                    latest = occurrences[-1]
                    recurring_friction.append({
                        "tag": tag,
                        "count": len(unique_dates),
                        "dates": unique_dates,
                        "example_friction": latest[1],
                    })
            recurring_friction.sort(key=lambda x: (-x["count"], x["tag"]))

        # (c) Milestones
        milestones: list[dict] = []
        for pr in projects:
            fs = _parse_date(pr["first_seen"])
            if fs is None or fs >= td:
                continue
            delta = (td - fs).days
            for milestone in _MILESTONE_DAYS:
                diff = abs(delta - milestone)
                if diff <= _WINDOW:
                    years = milestone // 365
                    months = (milestone % 365) // 30
                    if years >= 1:
                        label = f"{years} year{'s' if years != 1 else ''} old"
                    elif months >= 1:
                        label = f"{months} month{'s' if months != 1 else ''} old"
                    else:
                        label = f"{milestone} days old"
                    milestones.append({
                        "project": pr["id"],
                        "project_name": pr["name"],
                        "days": delta,
                        "label": label,
                        "first_seen": pr["first_seen"],
                    })
                    break

        if prior_years or recurring_friction or milestones:
            result[target_date] = {
                "prior_years": prior_years,
                "recurring_friction": recurring_friction,
                "milestones": milestones,
            }

    return result
