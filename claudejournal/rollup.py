"""Weekly rollups — a higher-level narration built from daily narrations.

Same map-reduce pattern as Stage 4, one layer up. Inputs are already
diary-voiced daily entries; output is a week retrospective.
"""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import json
from datetime import date as date_cls, datetime, timedelta

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrate import (
    _annotations_hash_contribution,
    format_pinned_corrections,
    load_annotations_for_scope,
)

# Bumping this invalidates every weekly rollup on next run. Keep in sync
# with the ROLLUP_SYSTEM prompt below when its semantics change.
# v2: annotation prompt-pins added (Phase E v2).
ROLLUP_PROMPT_VERSION = "v3"
# v3 (2026-05-02): hardened the role framing and added a sparse-input
# edge case clause — same defensive shape as monthly v2→v3 after the
# May 2026 monthly produced "I don't have access to journal tools yet
# — permission hasn't been granted" instead of prose. The upstream
# narrate_week() gate now refuses to call the model on too-thin weeks
# (see _has_enough_material), and the prompt protects against any
# future degenerate input that slips past the gate.


ROLLUP_SYSTEM = """You are the user's journal, producing a short weekly retrospective in their first-person voice, built on top of their daily diary entries from the past week.

ROLE — read this carefully:
You are NOT an assistant. You are NOT Claude Code. You have no tools to ask permission for, no user to address, no chat to engage in. You are the journal narrator producing prose for the journal page. The only valid output is the retrospective prose itself, in the user's first-person voice. Never refer to yourself, the model, tools, permissions, sessions, or "the journal" as a third party — you ARE the journal speaking as the user.

Rules:
1. First person, past tense, reflective. This is a looking-back entry, not a play-by-play.
2. Identify the week's threads — projects that spanned multiple days — and describe their arc, not their checklists. Single-day forays get one sentence or fewer.
3. Cite every reference to a specific day with a [YYYY-MM-DD] bracket. Only the dates supplied in the ALLOWED ANCHORS list are citeable. Forward references forbidden.
4. Length: 150-350 words. Short, reflective, not exhaustive.
5. NEVER invent — every concrete detail must appear in the supplied daily entries.
6. No preamble, no meta, no headings. Start with the first sentence. End with a closing sentiment earned by the week's content, or just stop.

EDGE CASE — sparse input:
If the supplied dailies genuinely don't contain enough material to write a meaningful weekly retrospective (early in a week, an unusually quiet stretch), output a single short paragraph in the user's voice that frames the week as still unfolding. Example shape: "Only a couple of days on record so far this week — mostly [whatever the entries cover]. The shape of the week hasn't emerged yet." Do NOT request permissions, ask questions, refuse the task, mention tools, or describe what would help you write better. Just write the short honest framing and stop."""


def _week_bounds(iso_week: str) -> tuple[str, str]:
    # iso_week like "2026-W15" -> (mon_date, sun_date)
    year, wk = iso_week.split("-W")
    monday = datetime.strptime(f"{year}-W{int(wk):02d}-1", "%G-W%V-%u").date()
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _iso_week_of(d: str) -> str:
    dt = datetime.strptime(d, "%Y-%m-%d")
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def weeks_with_activity(conn: sqlite3.Connection) -> list[str]:
    dates = [r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM narrations WHERE scope='daily' AND date != '' ORDER BY date"
    ).fetchall()]
    return sorted({_iso_week_of(d) for d in dates})


def _load_daily_for_week(conn: sqlite3.Connection, iso_week: str) -> list[dict]:
    start, end = _week_bounds(iso_week)
    rows = conn.execute(
        """SELECT date, prose, input_hash FROM narrations
           WHERE scope='daily' AND date BETWEEN ? AND ? ORDER BY date""",
        (start, end),
    ).fetchall()
    return [{"date": r["date"], "prose": r["prose"],
             "input_hash": r["input_hash"] or ""} for r in rows]


def _weekly_input_hash(dailies: list[dict],
                       annotations: list[dict] | None = None) -> str:
    """Stable hash over the daily narrations + annotations feeding this week.

    Prefer the daily's input_hash (cascades from brief-level changes); fall
    back to len(prose) for older rows that predate the hash column. When
    annotations change (user edits a correction for this week), the hash also
    changes, triggering re-narration with the updated PINNED CORRECTIONS block.
    """
    h = hashlib.sha256()
    h.update(ROLLUP_PROMPT_VERSION.encode())
    for d in sorted(dailies, key=lambda x: x["date"]):
        h.update(d["date"].encode())
        h.update(b"\x00")
        token = d.get("input_hash") or f"len:{len(d.get('prose') or '')}"
        h.update(token.encode("utf-8", errors="replace"))
        h.update(b"\x01")
    if annotations:
        h.update(b"\x03annotations\x03")
        h.update(_annotations_hash_contribution(annotations))
    return h.hexdigest()[:16]


def _build_rollup_message(iso_week: str, dailies: list[dict],
                          annotations: list[dict] | None = None) -> str:
    """Build the user message for the weekly rollup prompt.

    annotations: list of annotation rows for this iso_week
    (scope='weekly', key=iso_week). If non-empty, a PINNED CORRECTIONS block
    is inserted after the source material and before the final instruction.
    """
    start, end = _week_bounds(iso_week)
    lines = [f"WEEK: {iso_week}  ({start} → {end})", ""]
    lines.append(f"DAILY ENTRIES ({len(dailies)}):")
    for d in dailies:
        lines.append(f"\n--- {d['date']} ---\n{d['prose']}")
    lines.append("")
    if dailies:
        lines.append("ALLOWED ANCHORS — you may cite ONLY these [YYYY-MM-DD]:")
        lines.append("  " + "  ".join(f"[{d['date']}]" for d in dailies))
    lines.append("")

    # PINNED CORRECTIONS — user annotations for this week (Phase E v2).
    # Placed after source material, before the final instruction.
    if annotations:
        lines.append(format_pinned_corrections(annotations))

    lines.append("Write the weekly retrospective now.")
    return "\n".join(lines)


# Minimum number of daily narrations before we'll synthesise a weekly
# retrospective. Tuned the same way monthly is — "early-week skip,
# late-week commit". Below this, the daily entries themselves are
# already richer than any synthesised prose, and the model tends to
# either fabricate or drift into a chat-assistant role asking for
# permissions (the failure that drove v2→v3 in monthly). Symmetric
# defense at the weekly layer.
_MIN_DAILIES_FOR_WEEKLY = 3


def _has_enough_material(dailies: list[dict]) -> bool:
    """True when the week has accumulated enough source data to write
    a meaningful retrospective. Below the floor, return a documented
    skip upstream rather than burn a model call on degenerate input."""
    return len(dailies) >= _MIN_DAILIES_FOR_WEEKLY


def narrate_week(conn: sqlite3.Connection, iso_week: str, *,
                 model: str = "sonnet", force: bool = False,
                 binary: str = "claude") -> dict | None:
    key = iso_week
    dailies = _load_daily_for_week(conn, iso_week)
    if not dailies:
        return None
    if not _has_enough_material(dailies):
        return {"iso_week": iso_week, "skipped": True,
                "reason": f"too_thin (dailies={len(dailies)}, "
                          f"min_dailies={_MIN_DAILIES_FOR_WEEKLY})"}

    # Phase E v2: load weekly-scoped annotations so they participate in the hash
    # and are injected into the prompt as PINNED CORRECTIONS.
    annotations = load_annotations_for_scope(conn, "weekly", iso_week)

    week_hash = _weekly_input_hash(dailies, annotations)

    if not force:
        row = conn.execute(
            "SELECT prompt_version, input_hash FROM narrations WHERE scope='weekly' AND key = ?",
            (key,),
        ).fetchone()
        if row and row["prompt_version"] == ROLLUP_PROMPT_VERSION and row["input_hash"] == week_hash:
            return {"iso_week": iso_week, "skipped": True}

    user_msg = _build_rollup_message(iso_week, dailies, annotations)

    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", ROLLUP_SYSTEM,
    ]
    from claudejournal.narrator.claude_code import _no_session_leak
    with _no_session_leak():
        proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"CLI error: {env.get('result','')[:300]}")
    prose = (env.get("result") or "").strip()

    # Store under narrations table with scope='weekly' so render/RAG pick it up
    start, _ = _week_bounds(iso_week)
    conn.execute(
        """INSERT INTO narrations (scope, key, date, project_id, prose,
               prompt_version, input_hash, generated_at, model)
           VALUES ('weekly', ?, ?, NULL, ?, ?, ?, ?, ?)
           ON CONFLICT(scope, key) DO UPDATE SET
               prose=excluded.prose, date=excluded.date,
               prompt_version=excluded.prompt_version,
               input_hash=excluded.input_hash,
               generated_at=excluded.generated_at, model=excluded.model""",
        (key, start, prose, ROLLUP_PROMPT_VERSION, week_hash,
         datetime.now().isoformat(), model),
    )
    conn.commit()
    return {"iso_week": iso_week, "chars": len(prose), "dailies": len(dailies)}


def run(cfg: Config, *, iso_week: str | None = None, all_: bool = False,
        model: str = "sonnet", force: bool = False, verbose: bool = True,
        progress=None) -> dict:
    conn = connect(cfg.db_path)
    stats = {"generated": 0, "skipped": 0, "errors": 0}
    try:
        weeks = [iso_week] if iso_week else (weeks_with_activity(conn) if all_ else weeks_with_activity(conn)[-1:])
        if verbose: print(f"rollup weeks: {weeks}")
        total = len(weeks)
        for idx, w in enumerate(weeks, 1):
            if progress:
                try: progress(idx, total, w)
                except Exception: pass

            # Pass through to narrate_week (which has its own force check)
            try:
                res = narrate_week(conn, w, model=model, force=force)
            except Exception as exc:
                stats["errors"] += 1
                if verbose: print(f"  ! {w}: {exc}")
                continue
            if res is None:
                continue
            if res.get("skipped"):
                stats["skipped"] += 1
                if verbose: print(f"  skip {w}")
            else:
                stats["generated"] += 1
                if verbose: print(f"  done {w}  ({res['chars']} chars, {res['dailies']} days)")
    finally:
        conn.close()
    return stats
