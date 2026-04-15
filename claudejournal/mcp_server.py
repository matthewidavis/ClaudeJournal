"""MCP server — exposes the journal as a reference source for any MCP
client (Claude Code, Claude Desktop, etc).

Tools (read-only, local DB only):
  journal_search(query, limit=5)     — RAG retrieval, returns excerpts + dates
  journal_recent(days=7)             — recent daily diary prose
  journal_topic(tag, limit=20)       — days tagged with `tag`
  journal_learned(topic="", limit=40) — 'learned' bullets from briefs,
                                         optionally filtered by substring

The server runs over stdio (standard MCP transport). Attach to Claude
Code via:

  claude mcp add claudejournal -- python -m claudejournal mcp

The journal DB is read via the repo's normal config.json, so MCP shares
the same data source as the web UI and CLI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from claudejournal.config import load as load_config
from claudejournal.db import connect


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
    the 'tags' field of session briefs."""
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
            return f"No days tagged {tag!r}."
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

    mcp.run()
