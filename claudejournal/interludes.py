"""Creative interludes for days with no real narration.

Strict rules (from design memory — deferred_interludes.md):
  - Clearly fiction. Visually framed as such by the template, never confusable with real entries.
  - Never references the user's work, projects, code, or anything personal.
  - Inputs: date (day-of-week, month) + a randomly picked form + optional user-supplied
    abstract themes. Nothing else. No session data, no project names, no memory files.
  - Post-generation safety check: reject any output containing a known project name or
    obvious work artifact. Retry once with a different form, then fall through to the
    muted placeholder if it still leaks.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from claudejournal.config import Config
from claudejournal.db import connect


FORMS = [
    "haiku",
    "limerick",
    "couplet",
    "one_observation",
    "micro_proverb",
    "ascii_doodle",
    "absurd_weather",
    "unusual_word",
    "dream_fragment",
    "future_self_question",
]

# Light weighting — haiku is classic, ascii is fun, others rotate
FORM_WEIGHTS = {
    "haiku": 3, "limerick": 2, "couplet": 2, "one_observation": 2,
    "micro_proverb": 2, "ascii_doodle": 2, "absurd_weather": 1,
    "unusual_word": 2, "dream_fragment": 1, "future_self_question": 1,
}


# Large pool of flavor words injected as a soft "angle" hint per date —
# gives the model a different starting point even when the form repeats.
_FLAVOR_POOL = [
    # seasons/weather tints
    "brittle", "luminous", "damp", "gilded", "muted", "feverish", "soft-edged",
    "translucent", "rust-colored", "salt-scrubbed", "windblown", "sunbaked",
    # times/moods
    "dawn-tinged", "dusk-leaning", "mid-afternoon", "after-midnight",
    "expectant", "weary", "hopeful", "unrushed", "confused", "serene",
    # colors / textures
    "amber", "slate", "indigo", "ochre", "cream", "moss", "ash", "copper",
    "velvet", "burlap", "cedar", "linen",
    # invented place/culture prefixes
    "the village of Velk", "the shepherds of Belk", "the cartographers of Thrue",
    "old Merrow", "the ferry town of Loam", "the low hills of Ash",
    # abstract subjects
    "a small kindness", "an unopened letter", "a forgotten threshold",
    "the third Tuesday", "a garden no one planted", "a door that only locks inward",
    "a bell that only rings on accident",
]


def _flavor_for(date: str, form: str) -> str:
    h = hashlib.sha256((date + "::" + form + "::flavor").encode()).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    return rng.choice(_FLAVOR_POOL)


SYSTEM_PROMPT = """You produce tiny fictional interludes for a personal journal — whimsical filler for days with no real narration. You are told a DATE and a FORM. Produce ONLY that form.

ABSOLUTE RULES:
1. NEVER reference code, software, programming, engineering, tools, debugging, cameras, AI, APIs, files, folders, repositories, projects, or any work-related concept.
2. NEVER pretend to summarize or remember anything about the user's day. You know nothing about them.
3. NEVER use first-person past tense that implies lived experience ("I fixed...", "I figured out..."). Use the voice appropriate to the form only.
4. Themes allowed: seasons, weather, small nameless moments, abstract feelings, invented creatures, fictional places, words, numbers, the simple act of being a Tuesday.
5. If user-supplied themes are provided, you may use them as light flavor — never as concrete work memories.

FORM SPECS:
- haiku: exactly 3 lines, evocative, natural or abstract imagery
- limerick: 5 lines AABBA, light absurdity, often featuring a fictional character or place
- couplet: 2 rhyming lines about a mundane observation
- one_observation: 1–2 sentences, small noticing or truth
- micro_proverb: 1 short fictional proverb attributed to an invented culture (e.g. 'the shepherds of Velk say...')
- ascii_doodle: 3–6 lines of simple ASCII art, followed by a one-line caption
- absurd_weather: 1–2 sentence weather report from a fictional place
- unusual_word: a rare or invented word followed by its 1–2 sentence definition
- dream_fragment: 3–5 sentences of surreal, non-personal imagery
- future_self_question: one open-ended question about a small thing, addressed to the reader's future self

Output ONLY the content. No preamble, no labels, no markdown headings, no explanation."""


_FORBIDDEN_RX = re.compile(
    r"\b(code|coding|bug|debug|build|deploy|repo|repository|commit|git|"
    r"pipeline|tokenizer|claude|api|llm|ai|file|files|folder|"
    r"session|project|script|function|software|programmer|developer|"
    r"terminal|cli|script|refactor|narration|journal)\b",
    re.IGNORECASE,
)


def _pick_form_for_date(date: str, seed_salt: str = "") -> str:
    """Deterministic form pick — same date always gets same form."""
    h = hashlib.sha256((date + seed_salt).encode()).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    choices = []
    for form, weight in FORM_WEIGHTS.items():
        choices.extend([form] * weight)
    return rng.choice(choices)


def _build_prompt(date: str, form: str, seeds: list[str],
                  flavor: str, prior_same_form: list[str]) -> str:
    dt = datetime.strptime(date, "%Y-%m-%d")
    dayname = dt.strftime("%A")
    monthname = dt.strftime("%B")
    day = dt.day

    lines = [
        f"DATE: {date}",
        f"DAY: {dayname} in {monthname}, day {day}",
        f"FORM: {form}",
        f"FLAVOR HINT (a gentle angle — don't name it literally): {flavor}",
    ]
    if seeds:
        lines.append(f"OPTIONAL USER THEMES (flavor only, never literal): {', '.join(seeds)}")

    if prior_same_form:
        lines.append("")
        lines.append(f"PREVIOUS INTERLUDES YOU'VE WRITTEN IN THIS FORM ({form}):")
        for i, p in enumerate(prior_same_form[:5], 1):
            snippet = p.strip().replace("\n", " / ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            lines.append(f"  {i}. {snippet}")
        lines.append("")
        lines.append("Your new one MUST differ clearly: different imagery, different setting, different voice. "
                     "Do NOT echo phrases, place-names, characters, or motifs from the list above.")

    lines.append("")
    lines.append("Produce the interlude. Output ONLY the content.")
    return "\n".join(lines)


def _fingerprint(prose: str) -> str:
    """Normalized hash of the first chunk — catches near-duplicates."""
    normalized = re.sub(r"[^a-z0-9 ]+", "", prose.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)[:120]
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _existing_fingerprints(conn: sqlite3.Connection) -> set[str]:
    return {_fingerprint(r["prose"]) for r in conn.execute(
        "SELECT prose FROM interludes").fetchall()}


def _prior_same_form(conn: sqlite3.Connection, form: str, limit: int = 5) -> list[str]:
    rows = conn.execute(
        """SELECT prose FROM interludes WHERE form = ?
           ORDER BY generated_at DESC LIMIT ?""",
        (form, limit),
    ).fetchall()
    return [r["prose"] for r in rows]


def _is_safe(text: str, project_names: list[str]) -> bool:
    if not text.strip():
        return False
    if _FORBIDDEN_RX.search(text):
        return False
    tl = text.lower()
    for pname in project_names:
        if pname and len(pname) >= 3 and pname.lower() in tl:
            return False
    return True


def _call_claude(system: str, user: str, model: str = "haiku",
                 binary: str = "claude", timeout: int = 60) -> str:
    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", system,
    ]
    from claudejournal.narrator.claude_code import _no_session_leak
    with _no_session_leak():
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:200]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"CLI error: {env.get('result', '')[:200]}")
    return (env.get("result") or "").strip()


def empty_narration_dates(conn: sqlite3.Connection) -> list[str]:
    """Days that have activity but no daily narration at all.

    Returned newest-first so today's placeholder is generated before older
    backlog. Narrate runs oldest-first, so by the time the interlude loop
    reaches older dates, narrate has typically already filled them in —
    minimizing the collision window where both produce content for the
    same day.
    """
    return [r["date"] for r in conn.execute(
        """SELECT DISTINCT e.date FROM events e
           LEFT JOIN narrations n
             ON n.scope='daily' AND n.date = e.date
           WHERE e.date != '' AND n.prose IS NULL
           ORDER BY e.date DESC"""
    ).fetchall()]


def _already_has(conn: sqlite3.Connection, date: str) -> bool:
    row = conn.execute("SELECT 1 FROM interludes WHERE date = ?", (date,)).fetchone()
    return bool(row)


def run(cfg: Config, *, date: str | None = None, all_: bool = True,
        force: bool = False, model: str = "haiku",
        verbose: bool = True, progress=None) -> dict:
    stats = {"generated": 0, "skipped": 0, "rejected": 0, "errors": 0}
    if not cfg.interludes_enabled:
        if verbose: print("interludes disabled (config.interludes_enabled=false)")
        return stats

    conn = connect(cfg.db_path)
    try:
        project_names = [r["display_name"] for r in conn.execute(
            "SELECT display_name FROM projects").fetchall()]
        if date:
            dates = [date]
        else:
            dates = empty_narration_dates(conn)
        if not dates:
            return stats

        fingerprints = _existing_fingerprints(conn)
        total = len(dates)

        for idx, d in enumerate(dates, 1):
            if progress:
                try: progress(idx, total, d)
                except Exception: pass

            if not force and _already_has(conn, d):
                stats["skipped"] += 1
                continue

            form = _pick_form_for_date(d)
            flavor = _flavor_for(d, form)
            accepted = False
            prose = ""
            for attempt in range(3):
                priors = _prior_same_form(conn, form, limit=5)
                prompt = _build_prompt(d, form, cfg.interlude_seeds, flavor, priors)
                try:
                    prose = _call_claude(SYSTEM_PROMPT, prompt, model=model)
                except Exception as exc:
                    stats["errors"] += 1
                    if verbose: print(f"  ! {d}: {exc}")
                    break

                if not _is_safe(prose, project_names):
                    stats["rejected"] += 1
                    if verbose: print(f"  ! {d}: form {form} leaked work content; nudging")
                    form = _pick_form_for_date(d, seed_salt=f"safety{attempt}")
                    flavor = _flavor_for(d, form + str(attempt))
                    continue

                fp = _fingerprint(prose)
                if fp in fingerprints:
                    stats["rejected"] += 1
                    if verbose: print(f"  ! {d}: duplicate of prior interlude; re-rolling")
                    flavor = _flavor_for(d, form + f"variant{attempt}")
                    continue

                fingerprints.add(fp)
                accepted = True
                break

            if not accepted:
                continue

            conn.execute(
                """INSERT INTO interludes (date, form, prose, generated_at, model)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       form=excluded.form, prose=excluded.prose,
                       generated_at=excluded.generated_at, model=excluded.model""",
                (d, form, prose, datetime.now(timezone.utc).isoformat(), model),
            )
            conn.commit()
            stats["generated"] += 1
            if verbose:
                print(f"  [{idx}/{total}] {d}  {form:20s}  flavor={flavor!r:30s}  {len(prose)}c")
    finally:
        conn.close()
    return stats


def get_for_date(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT date, form, prose FROM interludes WHERE date = ?", (date,)
    ).fetchone()
    if not row:
        return None
    return dict(row)
