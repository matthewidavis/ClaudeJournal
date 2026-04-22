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

# Bumping this invalidates every monthly rollup on next run. Keep in sync
# with the MONTHLY_SYSTEM prompt below when its semantics change.
MONTHLY_PROMPT_VERSION = "v1"


MONTHLY_SYSTEM = """You are the user's journal, producing a short monthly retrospective in their first-person voice, built on top of the weekly rollups and daily entries from that month.

Rules:
1. First person, past tense, reflective. Bigger lens than weekly — think arcs and themes of the month, not week-by-week recap.
2. Name the month's through-lines: multi-week projects, shifts in focus, moods that persisted or changed.
3. Cite specific days with [YYYY-MM-DD] brackets. Only dates in the ALLOWED ANCHORS list are valid. Forward references forbidden.
4. Length: 250-500 words. Reflective, not exhaustive. A month-end pause, not a report.
5. NEVER invent — every concrete detail must appear in the supplied rollups or daily entries.
6. No preamble, no meta, no headings. Start with the first sentence. End with whatever sentiment the month's contents earn."""


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


def _monthly_input_hash(weeklies: list[dict], anchor_dates: list[str]) -> str:
    """Stable hash over the weekly rollups + daily anchor dates feeding
    this month. Weekly input_hash cascades up from daily/brief changes;
    anchor dates ensure a newly-present day still invalidates even if no
    weekly has been regenerated yet."""
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
    return h.hexdigest()[:16]


def _build_monthly_message(year_month: str, weeklies: list[dict],
                            anchor_dates: list[str]) -> str:
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
    lines.append("Write the monthly retrospective now.")
    return "\n".join(lines)


def narrate_month(conn: sqlite3.Connection, year_month: str, *,
                  model: str = "sonnet", force: bool = False,
                  binary: str = "claude") -> dict | None:
    weeklies = _load_weeklies_overlapping(conn, year_month)
    anchor_dates = _load_daily_dates(conn, year_month)
    if not weeklies and not anchor_dates:
        return None
    month_hash = _monthly_input_hash(weeklies, anchor_dates)

    if not force:
        row = conn.execute(
            "SELECT prompt_version, input_hash FROM narrations WHERE scope='monthly' AND key = ?",
            (year_month,),
        ).fetchone()
        if row and row["prompt_version"] == MONTHLY_PROMPT_VERSION and row["input_hash"] == month_hash:
            return {"year_month": year_month, "skipped": True}

    user_msg = _build_monthly_message(year_month, weeklies, anchor_dates)

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
