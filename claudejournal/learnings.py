"""Learnings aggregation — Phase B, task B4.

Collects all non-empty `learned` items from session_briefs, deduplicates by
fuzzy textual similarity, clusters by tag, and returns a curated lessons corpus.

Deduplication:
    Two learnings are considered identical when their significant-word sets
    overlap >= 4 words (conservative) OR when difflib.SequenceMatcher ratio
    > 0.80. The higher-count duplicate is kept as the canonical form.

Clustering:
    Each deduplicated learning is associated with the union of tags from all
    the briefs where it (or its duplicate) appeared. The *primary* tag is the
    most-frequent single tag across those briefs; learnings without any tag
    fall into the "untagged" group.

Output: list of dicts sorted by times_seen desc, then first_seen asc.
    {
        "text":       str   — canonical learning text,
        "first_seen": str   — YYYY-MM-DD of earliest appearance,
        "last_seen":  str   — YYYY-MM-DD of most recent appearance,
        "times_seen": int   — number of unique briefs this appeared in,
        "dates":      list  — sorted list of YYYY-MM-DD dates,
        "projects":   list  — sorted list of project display names,
        "tags":       list  — sorted tags (most frequent first),
    }
"""
from __future__ import annotations

import difflib
import re
import sqlite3


# ---------------------------------------------------------------------------
# Text helpers
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

_MIN_WORD_OVERLAP = 4      # significant words shared → likely duplicate
_SM_RATIO_THRESH  = 0.80   # SequenceMatcher ratio → likely duplicate


def _significant_words(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in _STOPWORDS}


def _are_duplicates(a: str, b: str) -> bool:
    """True if two learning strings are likely expressing the same insight."""
    # Quick word-overlap check (cheap)
    if len(_significant_words(a) & _significant_words(b)) >= _MIN_WORD_OVERLAP:
        return True
    # SequenceMatcher ratio (slightly slower but catches paraphrase)
    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio >= _SM_RATIO_THRESH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def aggregate_learnings(conn: sqlite3.Connection) -> list[dict]:
    """Aggregate and deduplicate learned items from all session_briefs.

    Returns a list of dicts — see module docstring for shape.
    """
    import json
    from collections import Counter

    # Build project_id -> display_name map
    proj_names: dict[str, str] = {
        r["id"]: (r["display_name"] or r["id"])
        for r in conn.execute("SELECT id, display_name FROM projects").fetchall()
    }

    # Collect raw learning items: (text, date, project_id, tags)
    raw: list[tuple[str, str, str, list[str]]] = []

    rows = conn.execute(
        """SELECT date, project_id, brief_json
           FROM session_briefs
           WHERE date IS NOT NULL AND date != '' AND brief_json IS NOT NULL
           ORDER BY date ASC"""
    ).fetchall()

    for r in rows:
        try:
            bj = json.loads(r["brief_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        learned = [l for l in (bj.get("learned") or []) if isinstance(l, str) and l.strip()]
        tags = [t.strip().lower() for t in (bj.get("tags") or []) if isinstance(t, str) and t.strip()]
        for item in learned:
            raw.append((item.strip(), r["date"], r["project_id"], tags))

    if not raw:
        return []

    # Cluster into groups of duplicates.
    # We use a simple O(n^2) union-find approach; n is typically hundreds.
    n = len(raw)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if _are_duplicates(raw[i][0], raw[j][0]):
                union(i, j)

    # Collect groups
    from collections import defaultdict
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    results: list[dict] = []
    for root, indices in groups.items():
        texts   = [raw[i][0] for i in indices]
        dates   = sorted({raw[i][1] for i in indices})
        pids    = [raw[i][2] for i in indices]
        all_tags: list[str] = []
        for i in indices:
            all_tags.extend(raw[i][3])

        # Canonical text: longest (usually most descriptive)
        canonical = max(texts, key=len)

        # Tag frequency → sorted most-common first
        tag_counter: Counter = Counter(all_tags)
        sorted_tags = [t for t, _ in tag_counter.most_common()]

        project_names_sorted = sorted({proj_names.get(p, p) for p in pids if p})

        results.append({
            "text":       canonical,
            "first_seen": dates[0],
            "last_seen":  dates[-1],
            "times_seen": len(indices),
            "dates":      dates,
            "projects":   project_names_sorted,
            "tags":       sorted_tags,
        })

    # Sort: most-reinforced first, then oldest first
    results.sort(key=lambda x: (-x["times_seen"], x["first_seen"]))
    return results
