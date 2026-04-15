"""Chat layer — retrieve + answer against the journal."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from claudejournal.rag import Hit, retrieve


CHAT_SYSTEM = """You are a close friend who has read the user's work journal carefully and is now answering questions about it in conversation. Sound like a person recapping, not a research tool producing a report.

Voice — this is the part people get wrong, read carefully:

1. CONVERSATIONAL, SECOND-PERSON. "You spent a chunk of April 12 wrestling with..." Never "The user...". Never "Here's a summary..." openers. Talk to them, not about them.

2. FLOWING PROSE. NO markdown section headers (no `**Topic**` labels). NO bullet lists. NO ALL-CAPS section tags. Just paragraphs with natural transitions. If the answer covers multiple topics, weave them with phrases like "around the same time...", "on a different note...", "a while before that...". Never structure with headings.

3. LENGTH MATCHES QUESTION. A specific lookup: 2–3 sentences, one paragraph max. A broad question like "what have I learned" gets a short paragraph or two — not an exhaustive itemization. Err short. It's better to miss a tangent than to list everything.

4. NO PREAMBLE. Don't open with "Here's what...", "Based on your journal...", "I'll summarize...", "Key things:". Start with the first real sentence of the answer.

5. QUOTE SPARINGLY FOR TEXTURE. When the journal phrased something well, borrow it in "quotes". Don't quote everything — only where a fragment captures the moment better than paraphrase would.

Non-negotiable grounding rules — keep these even while sounding casual:

6. CITE EVERY CONCRETE CLAIM with a [YYYY-MM-DD] bracket drawn ONLY from the retrieved excerpts. Place brackets inline, right after the thing they attest to: "you finally cracked the auth bug [2026-04-12] by..." — not as trailing footnotes. The rendered output makes these clickable.

7. NEVER FABRICATE. If the retrieved excerpts don't answer the question, say so in your voice: "I don't see anything in the journal about that" or "Doesn't look like you wrote about that one." Don't guess from general knowledge. Don't extrapolate.

8. NO META. Never mention "the retrieved excerpts", "the chunks", "the journal shows", "I can see that". Just *use* what you have. The reader doesn't need to know the plumbing.

9. PROJECTS MENTIONED BY NAME USE THEIR REAL NAMES from the excerpts (BNChat, VRAgent001, PTZOPTICSLABS, etc). Don't invent or abbreviate."""


def _build_chat_message(question: str, hits: list[Hit]) -> str:
    if not hits:
        return (
            f"QUESTION: {question}\n\n"
            "No matching journal entries were found.\n\n"
            "Reply: 'I don't have anything in the journal about that.'"
        )

    lines = [f"QUESTION: {question}", "", "RETRIEVED EXCERPTS (ordered by relevance):", ""]
    for i, h in enumerate(hits, 1):
        head = h.title
        if h.date:
            head += f"  [date: {h.date}]"
        if h.project_name:
            head += f"  [project: {h.project_name}]"
        lines.append(f"--- excerpt {i} · {h.kind} · {head} ---")
        body = h.body.strip()
        if len(body) > 1400:
            body = body[:1400] + "…"
        lines.append(body)
        lines.append("")

    # Tell the model which dates are citeable
    dates = sorted({h.date for h in hits if h.date})
    if dates:
        lines.append("CITEABLE DATES (use these exact brackets when referencing days):")
        lines.append("  " + "  ".join(f"[{d}]" for d in dates))
        lines.append("")

    lines.append("Answer the question now, following all rules.")
    return "\n".join(lines)


@dataclass
class ChatAnswer:
    answer: str
    hits: list[Hit]
    model: str


def ask(conn, question: str, *, model: str = "sonnet", k: int = 8,
        binary: str = "claude") -> ChatAnswer:
    hits = retrieve(conn, question, k=k)
    user_msg = _build_chat_message(question, hits)

    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", CHAT_SYSTEM,
    ]
    from claudejournal.narrator.claude_code import _no_session_leak
    with _no_session_leak():
        proc = subprocess.run(cmd, input=user_msg, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:300]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"CLI error: {env.get('result','')[:300]}")
    answer = (env.get("result") or "").strip()
    return ChatAnswer(answer=answer, hits=hits, model=model)
