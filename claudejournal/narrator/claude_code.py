"""Claude Code CLI-backed narrator (default). Uses `claude -p --bare`."""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from pathlib import Path

from claudejournal.narrator.base import (
    BriefInput, BriefResult, NarrationInput, NarrationResult, Narrator, NarratorError,
)


def _project_folder_name(cwd: Path) -> str:
    """Mirror Claude Code's mangling: replace path separators & colons with '-'."""
    s = str(cwd.resolve())
    for ch in (":", "\\", "/"):
        s = s.replace(ch, "-")
    return s


@contextlib.contextmanager
def _no_session_leak():
    """Snapshot ~/.claude/projects/<cwd>/*.jsonl before and after running the
    CLI subprocess, then delete any newly-appeared files. Even with
    --no-session-persistence, the CLI can drop tiny metadata JSONLs that
    pollute `claude --resume` ordering. This guarantees cleanup.
    """
    cwd = Path.cwd()
    proj_dir = Path.home() / ".claude" / "projects" / _project_folder_name(cwd)
    before: set[str] = set()
    if proj_dir.exists():
        try:
            before = {p.name for p in proj_dir.iterdir() if p.suffix == ".jsonl"}
        except OSError:
            pass
    try:
        yield
    finally:
        if not proj_dir.exists():
            return
        try:
            after = {p.name for p in proj_dir.iterdir() if p.suffix == ".jsonl"}
        except OSError:
            return
        for name in after - before:
            try:
                (proj_dir / name).unlink()
            except OSError:
                pass


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "goal":     {"type": "string"},
        "did":      {"type": "array", "items": {"type": "string"}},
        "files":    {"type": "array", "items": {"type": "string"}},
        "learned":  {"type": "array", "items": {"type": "string"}},
        "friction": {"type": "array", "items": {"type": "string"}},
        "wins":     {"type": "array", "items": {"type": "string"}},
        "mood":     {"type": "string"},
        "tags":     {"type": "array", "items": {"type": "string"}},
    },
    "required": ["goal", "did", "files", "learned", "friction", "wins", "mood", "tags"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You extract structured summaries from Claude Code session logs for a personal journal.

Rules you MUST follow:
- Never invent facts. If you're not sure, leave the field empty.
- "learned" is the strictest field: only include items the USER explicitly stated as a realization ("oh, X works because Y", "TIL...", "interesting, Z") OR items that appear in their project memory files. If the user never articulated a lesson, return an empty list — do NOT infer one from what they did.
- "friction" = corrections, retries, frustrations, things that didn't work the first time.
- "wins" = moments of success — user saying "perfect", "thanks", "works", or visible completion of a stated goal.
- "mood" = 1-4 words describing the emotional arc of the session (e.g. "focused and productive", "frustrated then relieved", "curious exploration").
- "goal" = one sentence describing what the user was trying to accomplish.
- "did" = 2-6 short bullets of what actually happened.
- "files" = up to 10 most-edited or most-discussed file paths, already in the input.
- "tags" = 2 to 5 short topic labels (1-2 words, lowercase, hyphenated) for cross-cutting themes. Prefer technical concepts ("rag", "sqlite-vec", "claude-cli", "wasm", "tts", "piper", "windows-tasks"), domains ("journal", "devops", "ml-infra"), or activities ("debugging", "refactor", "deploy"). Avoid generic tags ("python", "code"). Drop tags that wouldn't help a reader find related sessions.

Output ONLY valid JSON matching the supplied schema. No prose, no markdown, no backticks."""

PROMPT_VERSION = "v2"
NARRATION_PROMPT_VERSION = "v3"


NARRATION_SYSTEM = """You are ghostwriting a personal journal entry in the user's first-person voice, based on structured summaries of their day's work sessions.

Core rules — these are non-negotiable:

1. WRITE AS THE USER (first person, past tense, conversational). This is THEIR diary, not a report about them. "I spent the morning on..." not "The user spent the morning on..."

2. NEVER INVENT FACTS. Every concrete detail — tools, file names, bugs, decisions, realizations — must appear in the supplied briefs. If a brief's "learned" field is empty, do NOT manufacture an insight for that project. Not every day has a lesson and that's fine.

3. DON'T JUST LIST — DESCRIBE. A diary says "the tokenizer version fight dragged on until I noticed 0.20.3 was pinned somewhere I hadn't checked." A changelog says "tokenizer version mismatch, resolved." When you mention friction, let the resolution or the feeling come through. When you mention a win, say what made it feel like one.

4. LEARNINGS ARE PRECIOUS — don't dilute them. Only surface items from the "learned" field if they actually read as realizations ("oh, X works because Y"). If the item reads as a factual observation ("repo hadn't been touched in a week"), you may mention it in passing but don't frame it as a lesson.

5. FLOW, DON'T ENUMERATE. No bullet points. No headings inside a project's paragraph. If multiple projects were worked on, use a short subheading or transition between them ("Later in the day turned to..."). Paragraphs, not sections.

6. LENGTH: 250-600 words for a full day across projects. 150-350 words for a single-project day.

7. MOOD MATTERS. Each brief has a "mood" field describing the emotional arc. Honor it — "frustrated then determined" isn't the same as "focused and methodical." Let it shape the tone without stating it outright ("frustrated then determined" → the prose should sound that way, not say those words).

8. CLOSING: end with a short grounding sentence — one that's earned by the content. Not "what a productive day!" — something specific. "Called it a night once the exe finally launched clean." "Closed the laptop still thinking about the Shannon framing." If nothing earned a closer, don't force one; just stop.

9. NO META. Don't mention that you're summarizing briefs or that this is a journal entry. Just write the entry.

10. CROSS-DAY REFERENCES — the anchor rule:
    - When you mention prior work ("earlier this week", "yesterday", "Monday", "four days back"), you MUST include an ISO date bracket like [2026-04-12] immediately after the phrase.
    - The ALLOWED DATES are supplied in the anchor list below. You may cite ONLY those dates, exactly as written. Never invent a date that isn't in the list.
    - If no anchors are provided for a memory you want to mention, do NOT mention it. Cut the reference rather than guess.
    - Forward references forbidden. Never say "tomorrow" or imply future work.
    - Natural phrasing comes first, bracket is metadata: "finally landed the auth thing I first hit four days back [2026-04-09]" reads right. "On [2026-04-09] I first hit the auth thing" reads wrong.

11. MOOD — TWO SIGNALS, LET DISAGREEMENT BECOME TEXTURE:
    - Each brief has an "inferred mood" (an LLM's read of the session arc — advisory) and a "lexical mood" (a rule-based label computed from counted corrections, appreciations, errors — trusted fact).
    - If the two AGREE, lean on the inferred label — it's richer.
    - If they DISAGREE, surface the tension honestly in prose. Example: inferred "focused and productive" but lexical "correction-heavy" → the day probably looked productive on the surface but included more back-and-forth than you'd remember. Write that.
    - Never state either label directly ("the lexical mood was..."). Use them to shape tone.

12. THREADS — ongoing work across days:
    - A "thread" is a project you've been working on across multiple days. Threads are supplied below with their span and status (active/stuck/resolved).
    - When writing about a thread, honor its trajectory. A resolved thread should feel like closure. A stuck thread should feel like you're still in it.
    - Do NOT invent threads. If no threads are listed, every project in today's entry is fresh work — frame it that way.

Output: prose only. No preamble, no markdown headings for the day itself, no trailing notes. Start with the first sentence of the entry."""


def _brief_to_prompt_block(brief: dict, project_name: str, session_id: str) -> str:
    def _fmt_list(items):
        if not items: return "  (none)"
        return "\n".join(f"  - {x}" for x in items)

    lex = brief.get("_lexical") or {}
    lex_line = ""
    if lex:
        lex_line = (f"lexical signals (trusted counts): "
                    f"label={lex.get('label','?')}  "
                    f"corrections={lex.get('corrections',0)}  "
                    f"appreciations={lex.get('appreciations',0)}  "
                    f"errors={lex.get('errors',0)}  "
                    f"edits={lex.get('edits',0)}\n")

    return (
        f"### {project_name}  (session {session_id[:8]})\n"
        f"goal: {brief.get('goal', '')}\n"
        f"inferred mood: {brief.get('mood', '')}\n"
        f"{lex_line}"
        f"did:\n{_fmt_list(brief.get('did', []))}\n"
        f"learned:\n{_fmt_list(brief.get('learned', []))}\n"
        f"friction:\n{_fmt_list(brief.get('friction', []))}\n"
        f"wins:\n{_fmt_list(brief.get('wins', []))}\n"
    )


def _build_narration_message(inp: NarrationInput) -> str:
    lines: list[str] = []
    if inp.scope == "daily":
        lines.append(f"DATE: {inp.date}")
        lines.append(f"SCOPE: full day across {len(inp.briefs)} session(s)")
    else:
        lines.append(f"DATE: {inp.date}")
        lines.append(f"PROJECT: {inp.project_name}")
        lines.append(f"SCOPE: single project, {len(inp.briefs)} session(s)")
    lines.append("")
    lines.append("SESSION BRIEFS:")
    lines.append("")
    for i, b in enumerate(inp.briefs, 1):
        name = b.get("_project_name", inp.project_name or "project")
        sid = b.get("_session_id", f"s{i}")
        lines.append(_brief_to_prompt_block(b, name, sid))
        lines.append("")
    if inp.threads:
        lines.append("ONGOING THREADS (multi-day work you're in the middle of):")
        for t in inp.threads:
            touches_str = ", ".join(t["touches"])
            lines.append(
                f"  - {t['project_name']}  span={t['span_days']}d  "
                f"status={t['status']}  touches=[{touches_str}]"
            )
            if t.get("goal_hint"):
                lines.append(f"    started with: {t['goal_hint']}")
        lines.append("")

    if inp.anchors:
        lines.append("ALLOWED DATE ANCHORS — you may cite ONLY these, exactly as [YYYY-MM-DD]:")
        for a in inp.anchors:
            label = f" — {a['label']}" if a.get("label") else ""
            lines.append(f"  [{a['date']}] · {a['project_name']}{label}")
        lines.append("")
    else:
        lines.append("ALLOWED DATE ANCHORS: (none — do not reference any prior date)")
        lines.append("")

    if inp.prior_entry:
        lines.append("YESTERDAY'S ENTRY (for tonal continuity only — do not repeat content):")
        snippet = inp.prior_entry.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "..."
        lines.append(snippet)
        lines.append("")
    if inp.scope == "daily":
        lines.append("Write today's journal entry now, in first person, prose only.")
    else:
        lines.append(f"Write today's entry for the {inp.project_name} project, in first person, prose only.")
    return "\n".join(lines)


_FENCE_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _coerce_json(text: str) -> dict | None:
    """Accept the result either as pure JSON or extract from fenced/embedded."""
    if not text:
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RX.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # last-ditch: first { to last }
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(s[i : j + 1])
        except json.JSONDecodeError:
            return None
    return None


def _build_user_message(inp: BriefInput, max_chars: int = 12000) -> str:
    lines: list[str] = []
    lines.append(f"SESSION: {inp.project_name} · {inp.date}")
    if inp.started_at and inp.ended_at:
        lines.append(f"Duration: {inp.started_at[11:16]} → {inp.ended_at[11:16]}")
    lines.append("")

    if inp.files_touched:
        lines.append(f"FILES TOUCHED ({len(inp.files_touched)}):")
        for f in inp.files_touched[:20]:
            lines.append(f"  - {f['path']} ({f.get('touch_count', 1)}×)")
        lines.append("")

    if inp.user_prompts:
        lines.append(f"USER PROMPTS ({len(inp.user_prompts)}):")
        for p in inp.user_prompts:
            tag = ""
            if p.get("kind") == "correction": tag = "[correction] "
            elif p.get("kind") == "appreciation": tag = "[win] "
            text = p.get("summary", "").replace("\n", " ").strip()
            lines.append(f"  - {tag}{text}")
        lines.append("")

    if inp.assistant_snippets:
        lines.append(f"NOTABLE ASSISTANT MOMENTS ({len(inp.assistant_snippets)}):")
        for s in inp.assistant_snippets[:40]:
            text = s.get("text", "").replace("\n", " ").strip()
            lines.append(f"  - {text}")
        lines.append("")

    if inp.memory_text:
        lines.append("PROJECT MEMORY (context, do not repeat verbatim):")
        mem = inp.memory_text.strip()
        if len(mem) > 2500:
            mem = mem[:2500] + "\n...[truncated]"
        lines.append(mem)
        lines.append("")

    if inp.prior_brief_hint:
        lines.append("PRIOR SESSION CONTINUITY HINT:")
        lines.append(inp.prior_brief_hint)
        lines.append("")

    lines.append(
        "OUTPUT FORMAT: Return ONLY a single JSON object matching the schema. "
        "No prose, no preamble, no code fences, no commentary. "
        "Your entire reply must start with `{` and end with `}`."
    )
    full = "\n".join(lines)
    if len(full) > max_chars:
        full = full[:max_chars] + "\n...[truncated for length]"
    return full


class ClaudeCodeNarrator:
    name = "claude-code"

    def __init__(self, model: str = "haiku", narration_model: str = "sonnet",
                 binary: str = "claude"):
        self.model = model
        self.narration_model = narration_model
        self.binary = binary

    def narrate_session(self, inp: BriefInput, *, dry_run: bool = False) -> BriefResult:
        user_msg = _build_user_message(inp)
        cmd = [
            self.binary, "-p",
            "--model", self.model,
            "--tools", "",
            "--no-session-persistence",
            "--output-format", "json",
            "--system-prompt", SYSTEM_PROMPT,
            "--json-schema", json.dumps(BRIEF_SCHEMA),
            # prompt is supplied via stdin to dodge command-line length limits
        ]
        if dry_run:
            return BriefResult(
                brief={"__dry_run__": True, "system": SYSTEM_PROMPT, "user": user_msg},
                raw="", cost_usd=0.0, model=self.model,
            )

        try:
            with _no_session_leak():
                proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=180)
        except subprocess.TimeoutExpired:
            raise NarratorError("claude CLI timed out (180s)")

        if proc.returncode != 0:
            raise NarratorError(
                f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}"
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise NarratorError(f"couldn't parse CLI envelope: {e}; stdout head: {proc.stdout[:300]}")

        if envelope.get("is_error"):
            raise NarratorError(f"CLI reported error: {envelope.get('result', '')[:500]}")

        # With --json-schema, the validated object lives under structured_output;
        # result is just a status string. Fall back to result for non-schema calls.
        brief = envelope.get("structured_output")
        result_text = envelope.get("result", "") or ""
        if not isinstance(brief, dict):
            brief = _coerce_json(result_text)
        if not isinstance(brief, dict):
            raise NarratorError(
                f"no structured output; turns={envelope.get('num_turns')}; "
                f"result head: {result_text[:300]!r}"
            )

        return BriefResult(
            brief=brief,
            raw=result_text,
            cost_usd=float(envelope.get("total_cost_usd") or 0.0),
            model=self.model,
        )

    def narrate_day(self, inp: NarrationInput, *, dry_run: bool = False) -> NarrationResult:
        user_msg = _build_narration_message(inp)
        if dry_run:
            return NarrationResult(prose=f"[DRY-RUN]\n---SYSTEM---\n{NARRATION_SYSTEM}\n---USER---\n{user_msg}",
                                   cost_usd=0.0, model=self.narration_model)

        cmd = [
            self.binary, "-p",
            "--model", self.narration_model,
            "--tools", "",
            "--no-session-persistence",
            "--output-format", "json",
            "--system-prompt", NARRATION_SYSTEM,
        ]
        try:
            with _no_session_leak():
                proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=240)
        except subprocess.TimeoutExpired:
            raise NarratorError("claude CLI timed out (240s)")
        if proc.returncode != 0:
            raise NarratorError(f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}")

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise NarratorError(f"couldn't parse CLI envelope: {e}; head: {proc.stdout[:300]}")
        if envelope.get("is_error"):
            raise NarratorError(f"CLI reported error: {envelope.get('result', '')[:500]}")

        prose = (envelope.get("result") or "").strip()
        if not prose:
            raise NarratorError(f"empty prose returned; envelope keys: {list(envelope.keys())}")

        return NarrationResult(
            prose=prose,
            cost_usd=float(envelope.get("total_cost_usd") or 0.0),
            model=self.narration_model,
        )
