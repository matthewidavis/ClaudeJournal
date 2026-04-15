"""Deterministic lexical mood signals per session — trusted fact layer.

The LLM-inferred mood in each brief is advisory. These lexical signals
are computed from counted events and must not lie. When the two disagree,
the narrator is told to let that disagreement become texture in the prose.
"""
from __future__ import annotations

import sqlite3


def lexical_signals(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN kind='user_prompt' THEN 1 ELSE 0 END) AS prompts,
            SUM(CASE WHEN kind='correction' THEN 1 ELSE 0 END) AS corrections,
            SUM(CASE WHEN kind='appreciation' THEN 1 ELSE 0 END) AS appreciations,
            SUM(CASE WHEN kind='error' THEN 1 ELSE 0 END) AS errors,
            SUM(CASE WHEN kind='file_edit' THEN 1 ELSE 0 END) AS edits,
            COUNT(*) AS total
        FROM events WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()

    prompts = row["prompts"] or 0
    corrections = row["corrections"] or 0
    appreciations = row["appreciations"] or 0
    errors = row["errors"] or 0
    edits = row["edits"] or 0

    friction = corrections + errors
    momentum = appreciations + edits / max(prompts, 1) * 0.5

    if appreciations == 0 and corrections == 0 and errors == 0:
        label = "neutral-quiet"
    elif friction == 0 and appreciations >= 2:
        label = "smooth"
    elif friction >= 3 and appreciations == 0:
        label = "friction-heavy"
    elif friction >= 2 and appreciations >= 2:
        label = "mixed"
    elif corrections >= 3:
        label = "correction-heavy"
    elif errors >= corrections * 2 and errors >= 3:
        label = "error-heavy"
    elif appreciations >= 3 and friction <= 1:
        label = "warm"
    else:
        label = "working"

    return {
        "prompts": prompts,
        "corrections": corrections,
        "appreciations": appreciations,
        "errors": errors,
        "edits": edits,
        "label": label,
    }
