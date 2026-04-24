"""RAG over the journal — SQLite FTS5 index + retrieval.

Indexed granularities (all at once — retrieval ranks across them):
  - daily narrations      (coarse: "what happened this week")
  - project_day narrations(medium: "what's the ZigZag story")
  - session briefs        (fine: goal/did/learned/friction/wins flattened)
  - project memory/*.md   (project ground truth)

Each chunk has stable metadata (date, project_id) so answers can cite
[YYYY-MM-DD] brackets that resolve to real entries.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks USING fts5(
    kind UNINDEXED,
    date UNINDEXED,
    project_id UNINDEXED,
    project_name UNINDEXED,
    title,
    body,
    tokenize = 'porter unicode61'
);
"""

# Characters FTS5 treats as syntax; strip them from user input.
_FTS_SYNTAX_RX = re.compile(r'[\"\'()*:^+\-]')

# Small stopword list — FTS5's porter tokenizer handles stemming but doesn't
# drop stopwords, and user questions are often half stopwords ("when did I").
_STOPWORDS = frozenset("""
a an and any are as at be been being but by can could did do does doing done
for from had has have having he her him his how i if in into is it its itself
just me my myself no not of off on once only or other our ours out over own
so some still such than that the their them then there these they this those
through to too under until up was we were what when where which while who whom
why will with would you your yours yourself
""".split())

_WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


@dataclass
class Hit:
    kind: str
    date: str
    project_id: str
    project_name: str
    title: str
    body: str
    score: float


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(FTS_SCHEMA)


def _flatten_brief(brief: dict) -> str:
    parts = [
        f"goal: {brief.get('goal','')}",
        f"mood: {brief.get('mood','')}",
    ]
    for key in ("did", "learned", "friction", "wins"):
        vals = brief.get(key) or []
        if vals:
            parts.append(f"{key}: " + " | ".join(vals))
    return "\n".join(parts)


def reindex(conn: sqlite3.Connection, claude_home: Path, verbose: bool = False) -> dict:
    _ensure_schema(conn)
    conn.execute("DELETE FROM rag_chunks")
    stats = {"daily": 0, "project_day": 0, "briefs": 0, "memory": 0}

    # Daily narrations
    for r in conn.execute(
        "SELECT date, prose FROM narrations WHERE scope='daily' AND date != ''"
    ).fetchall():
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("daily_narration", r["date"], "", "",
             f"Daily · {r['date']}", r["prose"]),
        )
        stats["daily"] += 1

    # Project-day narrations
    for r in conn.execute(
        """SELECT n.date, n.project_id, n.prose, p.display_name AS pname
           FROM narrations n JOIN projects p ON p.id = n.project_id
           WHERE scope='project_day' AND n.date != ''"""
    ).fetchall():
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("project_day_narration", r["date"], r["project_id"], r["pname"],
             f"{r['pname']} · {r['date']}", r["prose"]),
        )
        stats["project_day"] += 1

    # Session briefs
    for r in conn.execute(
        """SELECT b.session_id, b.date, b.project_id, b.brief_json, p.display_name AS pname
           FROM session_briefs b JOIN projects p ON p.id = b.project_id"""
    ).fetchall():
        try:
            brief = json.loads(r["brief_json"])
        except json.JSONDecodeError:
            continue
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("brief", r["date"], r["project_id"], r["pname"],
             f"brief · {r['pname']} · {r['date']}", _flatten_brief(brief)),
        )
        stats["briefs"] += 1

    # Curated documents — summary + head of extracted text. The summary
    # is stored as JSON under narrations.scope='document'; we flatten the
    # fields the same way briefs are flattened so retrieval hits them.
    stats["documents"] = 0
    for r in conn.execute(
        """SELECT d.id, d.title, d.added_date, d.project_ids, d.tags,
                  d.user_note, d.extracted_text,
                  n.prose AS summary_json
           FROM documents d
           LEFT JOIN narrations n
             ON n.scope='document' AND n.key=d.id"""
    ).fetchall():
        body_parts: list[str] = []
        title = r["title"] or r["id"]
        note = r["user_note"] or ""
        if note:
            body_parts.append(f"note: {note}")
        try:
            summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
        except json.JSONDecodeError:
            summary = {}
        if summary.get("hook"):
            body_parts.append(f"hook: {summary['hook']}")
        if summary.get("takeaway"):
            body_parts.append(f"takeaway: {summary['takeaway']}")
        if summary.get("key_points"):
            body_parts.append("key_points:\n" + "\n".join(
                f"- {p}" for p in summary["key_points"] if isinstance(p, str)
            ))
        # First 2 KB of extracted text gives FTS something to grep beyond
        # the summary — cheap recall win without bloating the index.
        excerpt = (r["extracted_text"] or "")[:2000]
        if excerpt:
            body_parts.append("excerpt:\n" + excerpt)
        try:
            pids = json.loads(r["project_ids"] or "[]")
        except json.JSONDecodeError:
            pids = []
        primary_pid = pids[0] if pids else ""
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("document", r["added_date"] or "", primary_pid, "",
             f"doc · {title}", "\n\n".join(body_parts)),
        )
        stats["documents"] += 1

    # Project arc narrations — retrospective prose per project.
    stats["arcs"] = 0
    for r in conn.execute(
        """SELECT n.key AS project_id, n.prose, p.display_name AS pname
           FROM narrations n
           LEFT JOIN projects p ON p.id = n.key
           WHERE n.scope = 'project_arc' AND n.prose IS NOT NULL AND n.prose != ''"""
    ).fetchall():
        pname = r["pname"] or r["project_id"]
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("project_arc", "", r["project_id"], pname,
             f"Arc: {pname}", r["prose"]),
        )
        stats["arcs"] += 1

    # Topic narrations — wiki-style synthesis pages. Prose is human-readable.
    stats["topics"] = 0
    for r in conn.execute(
        "SELECT key, prose FROM narrations WHERE scope='topic' "
        "AND prose IS NOT NULL AND prose != ''"
    ).fetchall():
        conn.execute(
            "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("topic", "", "", "", f"Topic: {r['key']}", r["prose"]),
        )
        stats["topics"] += 1

    # Project memory files
    for r in conn.execute(
        "SELECT id, display_name FROM projects"
    ).fetchall():
        mem_dir = claude_home / "projects" / r["id"] / "memory"
        if not mem_dir.exists():
            continue
        for md in mem_dir.glob("*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            conn.execute(
                "INSERT INTO rag_chunks (kind, date, project_id, project_name, title, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("memory", "", r["id"], r["display_name"],
                 f"memory · {r['display_name']} · {md.name}", text),
            )
            stats["memory"] += 1

    conn.commit()
    if verbose:
        print(f"indexed: {stats}")
    return stats


def _sanitize_query(q: str) -> str:
    """Turn a natural-language question into an FTS5 MATCH expression.

    FTS5 defaults to AND across space-separated tokens. User questions are
    half stopwords, so AND over every word usually returns nothing. We strip
    stopwords + syntax chars, then join the rest with OR so any term matches.
    """
    q = _FTS_SYNTAX_RX.sub(" ", q)
    tokens = [t.lower() for t in _WORD_RX.findall(q)]
    kept = [t for t in tokens if t not in _STOPWORDS and len(t) >= 2]
    if not kept:
        return ""
    # Deduplicate while preserving order
    seen, unique = set(), []
    for t in kept:
        if t not in seen:
            seen.add(t); unique.append(t)
    return " OR ".join(unique)


def retrieve(conn: sqlite3.Connection, query: str, *, k: int = 8) -> list[Hit]:
    _ensure_schema(conn)
    q = _sanitize_query(query)
    if not q:
        return []
    rows = conn.execute(
        """
        SELECT kind, date, project_id, project_name, title, body,
               bm25(rag_chunks) AS score
        FROM rag_chunks
        WHERE rag_chunks MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (q, k),
    ).fetchall()
    return [Hit(**dict(r)) for r in rows]
