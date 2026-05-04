"""Monthly rollups — one layer above weekly. Fed weekly rollups (already
condensed) plus any daily entries for that month so the narrator sees
both grain sizes. Output is stored as scope='monthly', key='YYYY-MM'.

Same contract as rollup.py: [YYYY-MM-DD] anchors cite real days, no
forward references, no invention."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime

from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrate import (
    _annotations_hash_contribution,
    format_pinned_corrections,
    load_annotations_for_scope,
)

# Bumping this invalidates every monthly rollup on next run. Keep in sync
# with the MONTHLY_SYSTEM prompt below when its semantics change.
# v2: annotation prompt-pins added (Phase E v2).
MONTHLY_PROMPT_VERSION = "v3"
# v3 (2026-05-02): hardened the role framing and added explicit
# sparse-input edge-case guidance after a real failure where a May
# rollup with 1 anchor and 0 weeklies produced "I don't have access
# to journal tools yet — permission hasn't been granted" rather than
# prose. The upstream narrate_month() gate now refuses to even call
# the model on too-thin inputs (see _has_enough_material()), but the
# prompt hardening protects against future edge cases the gate misses.


MONTHLY_SYSTEM = """You are the user's journal, producing a short monthly retrospective in their first-person voice, built on top of the weekly rollups and daily entries from that month.

ROLE — read this carefully:
You are NOT an assistant. You are NOT Claude Code. You have no tools to ask permission for, no user to address, no chat to engage in. You are the journal narrator producing prose for the journal page. The only valid output is the retrospective prose itself, in the user's first-person voice. Never refer to yourself, the model, tools, permissions, sessions, or "the journal" as a third party — you ARE the journal speaking as the user.

Rules:
1. First person, past tense, reflective. Bigger lens than weekly — think arcs and themes of the month, not week-by-week recap.
2. Name the month's through-lines: multi-week projects, shifts in focus, moods that persisted or changed.
3. Cite specific days with [YYYY-MM-DD] brackets. Only dates in the ALLOWED ANCHORS list are valid. Forward references forbidden.
4. Length: 250-500 words. Reflective, not exhaustive. A month-end pause, not a report.
5. NEVER invent — every concrete detail must appear in the supplied rollups or daily entries.
6. No preamble, no meta, no headings. Start with the first sentence. End with whatever sentiment the month's contents earn.

EDGE CASE — sparse input:
If the supplied rollups + anchors genuinely don't contain enough material to write a meaningful retrospective (early in a month, an unusually quiet stretch), output a single short paragraph in the user's voice that frames the month as still unfolding. Example shape: "The month is still young — only a couple of days on record so far, mostly [whatever the anchor dates' activity was]. The shape of it hasn't emerged yet." Do NOT request permissions, ask questions, refuse the task, mention tools, or describe what would help you write better. Just write the short honest framing and stop."""


def _ym_of(date_str: str) -> str:
    return date_str[:7]  # "YYYY-MM"


def _month_bounds(year_month: str) -> tuple[str, str]:
    """YYYY-MM -> (first_day, last_day) as ISO strings."""
    y, m = year_month.split("-")
    start = datetime(int(y), int(m), 1).date()
    if int(m) == 12:
        end = datetime(int(y) + 1, 1, 1).date()
    else:
        end = datetime(int(y), int(m) + 1, 1).date()
    from datetime import timedelta
    return start.isoformat(), (end - timedelta(days=1)).isoformat()


def months_with_activity(conn: sqlite3.Connection) -> list[str]:
    dates = [r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM narrations WHERE scope='daily' AND date != '' ORDER BY date"
    ).fetchall()]
    return sorted({_ym_of(d) for d in dates if d})


def _load_weeklies_overlapping(conn: sqlite3.Connection, year_month: str) -> list[dict]:
    """Weekly rollups whose anchor date falls inside the month."""
    start, end = _month_bounds(year_month)
    rows = conn.execute(
        """SELECT key, date, prose, input_hash FROM narrations
           WHERE scope='weekly' AND date BETWEEN ? AND ? ORDER BY date""",
        (start, end),
    ).fetchall()
    return [{"iso_week": r["key"], "date": r["date"], "prose": r["prose"],
             "input_hash": r["input_hash"] or ""} for r in rows]


def _load_daily_dates(conn: sqlite3.Connection, year_month: str) -> list[str]:
    """Just the dates — used as ALLOWED ANCHORS. We don't dump all daily
    prose; the weekly rollups already carry the distilled content."""
    start, end = _month_bounds(year_month)
    rows = conn.execute(
        """SELECT date FROM narrations
           WHERE scope='daily' AND date BETWEEN ? AND ? ORDER BY date""",
        (start, end),
    ).fetchall()
    return [r["date"] for r in rows]


def _monthly_input_hash(weeklies: list[dict], anchor_dates: list[str],
                        annotations: list[dict] | None = None) -> str:
    """Stable hash over weekly rollups + daily anchor dates + annotations.

    Weekly input_hash cascades up from daily/brief changes; anchor dates ensure
    a newly-present day still invalidates even if no weekly has been regenerated
    yet. When annotations change (user edits a correction for this month), the
    hash also changes, triggering re-narration with the updated PINNED CORRECTIONS.
    """
    h = hashlib.sha256()
    h.update(MONTHLY_PROMPT_VERSION.encode())
    for w in sorted(weeklies, key=lambda x: x["iso_week"]):
        h.update(w["iso_week"].encode())
        h.update(b"\x00")
        token = w.get("input_hash") or f"len:{len(w.get('prose') or '')}"
        h.update(token.encode("utf-8", errors="replace"))
        h.update(b"\x01")
    h.update(b"\x02")
    for d in sorted(anchor_dates):
        h.update(d.encode())
        h.update(b"\x03")
    if annotations:
        h.update(b"\x03annotations\x03")
        h.update(_annotations_hash_contribution(annotations))
    return h.hexdigest()[:16]


def _build_monthly_message(year_month: str, weeklies: list[dict],
                            anchor_dates: list[str],
                            annotations: list[dict] | None = None) -> str:
    """Build the user message for the monthly rollup prompt.

    annotations: list of annotation rows for this year_month
    (scope='monthly', key=year_month). If non-empty, a PINNED CORRECTIONS block
    is inserted after the source material and before the final instruction.
    """
    start, end = _month_bounds(year_month)
    lines = [f"MONTH: {year_month}  ({start} → {end})", ""]
    lines.append(f"WEEKLY ROLLUPS ({len(weeklies)}):")
    for w in weeklies:
        lines.append(f"\n--- {w['iso_week']} (week of {w['date']}) ---\n{w['prose']}")
    lines.append("")
    if anchor_dates:
        lines.append("ALLOWED ANCHORS — you may cite ONLY these [YYYY-MM-DD]:")
        # Wrap anchors across lines to keep the prompt readable
        chunks = [anchor_dates[i:i+7] for i in range(0, len(anchor_dates), 7)]
        for chunk in chunks:
            lines.append("  " + "  ".join(f"[{d}]" for d in chunk))
    lines.append("")

    # PINNED CORRECTIONS — user annotations for this month (Phase E v2).
    # Placed after source material, before the final instruction.
    if annotations:
        lines.append(format_pinned_corrections(annotations))

    lines.append("Write the monthly retrospective now.")
    return "\n".join(lines)


# Minimum source-material thresholds before we'll synthesise a monthly
# retrospective. Below these, the model has nothing real to weave a
# month-shape from and tends to either fabricate or wander off-script
# (see v2→v3 prompt-version bump comment for the failure that drove
# this gate). Tuned for "early-month skip, late-month commit": a fresh
# May 2nd has 1 anchor, 0 weeklies → skip. A month with even one full
# weekly rollup is enough to write a real retrospective even without
# all daily anchors backfilled.
_MIN_ANCHORS_FOR_MONTHLY = 5
_MIN_WEEKLIES_FOR_MONTHLY = 1


def _has_enough_material(weeklies: list[dict], anchor_dates: list[str]) -> bool:
    """True when the month has accumulated enough source data to write
    a meaningful retrospective. Either a weekly rollup OR enough daily
    anchors qualifies; together they're a clear yes. Fewer than the
    minimums = skip generation upstream rather than burn a model call
    on input the prompt can't honestly produce prose from."""
    if len(weeklies) >= _MIN_WEEKLIES_FOR_MONTHLY:
        return True
    if len(anchor_dates) >= _MIN_ANCHORS_FOR_MONTHLY:
        return True
    return False


def narrate_month(conn: sqlite3.Connection, year_month: str, *,
                  model: str = "sonnet", force: bool = False,
                  binary: str = "claude") -> dict | None:
    weeklies = _load_weeklies_overlapping(conn, year_month)
    anchor_dates = _load_daily_dates(conn, year_month)
    if not weeklies and not anchor_dates:
        return None
    if not _has_enough_material(weeklies, anchor_dates):
        # Not yet enough source. Return a documented skip rather than
        # call the model on degenerate input.
        return {"year_month": year_month, "skipped": True,
                "reason": f"too_thin (weeklies={len(weeklies)}, "
                          f"anchors={len(anchor_dates)}, "
                          f"min_weeklies={_MIN_WEEKLIES_FOR_MONTHLY}, "
                          f"min_anchors={_MIN_ANCHORS_FOR_MONTHLY})"}

    # Phase E v2: load monthly-scoped annotations so they participate in the hash
    # and are injected into the prompt as PINNED CORRECTIONS.
    annotations = load_annotations_for_scope(conn, "monthly", year_month)

    month_hash = _monthly_input_hash(weeklies, anchor_dates, annotations)

    if not force:
        row = conn.execute(
            "SELECT prompt_version, input_hash FROM narrations WHERE scope='monthly' AND key = ?",
            (year_month,),
        ).fetchone()
        if row and row["prompt_version"] == MONTHLY_PROMPT_VERSION and row["input_hash"] == month_hash:
            return {"year_month": year_month, "skipped": True}

    user_msg = _build_monthly_message(year_month, weeklies, anchor_dates, annotations)

    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", MONTHLY_SYSTEM,
    ]
    from claudejournal.narrator.claude_code import _no_session_leak
    with _no_session_leak():
        proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"CLI error: {env.get('result','')[:300]}")
    prose = (env.get("result") or "").strip()

    start, _ = _month_bounds(year_month)
    conn.execute(
        """INSERT INTO narrations (scope, key, date, project_id, prose,
               prompt_version, input_hash, generated_at, model)
           VALUES ('monthly', ?, ?, NULL, ?, ?, ?, ?, ?)
           ON CONFLICT(scope, key) DO UPDATE SET
               prose=excluded.prose, date=excluded.date,
               prompt_version=excluded.prompt_version,
               input_hash=excluded.input_hash,
               generated_at=excluded.generated_at, model=excluded.model""",
        (year_month, start, prose, MONTHLY_PROMPT_VERSION, month_hash,
         datetime.now().isoformat(), model),
    )
    conn.commit()
    return {"year_month": year_month, "chars": len(prose),
            "weeklies": len(weeklies), "days": len(anchor_dates)}


def run(cfg: Config, *, year_month: str | None = None, all_: bool = False,
        model: str = "sonnet", force: bool = False, verbose: bool = True,
        progress=None) -> dict:
    conn = connect(cfg.db_path)
    stats = {"generated": 0, "skipped": 0, "errors": 0}
    try:
        months = [year_month] if year_month else (
            months_with_activity(conn) if all_ else months_with_activity(conn)[-1:]
        )
        if verbose: print(f"monthly rollups: {months}")
        total = len(months)
        for idx, m in enumerate(months, 1):
            if progress:
                try: progress(idx, total, m)
                except Exception: pass
            try:
                res = narrate_month(conn, m, model=model, force=force)
            except Exception as exc:
                stats["errors"] += 1
                if verbose: print(f"  ! {m}: {exc}")
                continue
            if res is None:
                continue
            if res.get("skipped"):
                stats["skipped"] += 1
                if verbose: print(f"  skip {m}")
            else:
                stats["generated"] += 1
                if verbose: print(f"  done {m}  ({res['chars']} chars, "
                                 f"{res['weeklies']} weeks, {res['days']} days)")
    finally:
        conn.close()
    return stats
