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


def _build_chat_message(question: str, hits: list[Hit],
                        history: list[dict] | None = None) -> str:
    """Build the user-side message sent to the chat model.

    `history`: optional list of prior turns as {role, text} dicts. When
    present, the prior conversation is rendered above the current
    question so the model has multi-turn context. Each request is still
    a single CLI invocation — we're constructing the multi-turn
    transcript in-message, not relying on Claude CLI's session memory.
    """
    history = history or []

    if not hits:
        # Even with prior history, an empty retrieval result for the
        # current question is the honest signal — answer accordingly.
        prior_block = ""
        if history:
            prior_lines = ["PRIOR CONVERSATION:"]
            for turn in history:
                role = turn.get("role", "user")
                tag = "User" if role == "user" else "You"
                txt = (turn.get("text") or "").strip()
                if txt:
                    prior_lines.append(f"> {tag}: {txt}")
            prior_lines.append("")
            prior_block = "\n".join(prior_lines) + "\n"
        return (
            f"{prior_block}"
            f"QUESTION: {question}\n\n"
            "No matching journal entries were found.\n\n"
            "Reply: 'I don't have anything in the journal about that.'"
        )

    lines: list[str] = []

    if history:
        lines.append("PRIOR CONVERSATION (for context — already part of "
                     "this dialogue, do not re-summarise):")
        for turn in history:
            role = turn.get("role", "user")
            tag = "User" if role == "user" else "You"
            txt = (turn.get("text") or "").strip()
            if txt:
                lines.append(f"> {tag}: {txt}")
        lines.append("")
        lines.append(f"CURRENT QUESTION: {question}")
    else:
        lines.append(f"QUESTION: {question}")

    lines.extend(["", "RETRIEVED EXCERPTS (ordered by relevance — drawn "
                  "for the CURRENT question):", ""])
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

    if history:
        lines.append("Answer the CURRENT question, drawing on the prior "
                     "conversation for context. Don't re-recap what the "
                     "prior turns already established.")
    else:
        lines.append("Answer the question now, following all rules.")
    return "\n".join(lines)


def _retrieval_query(question: str, history: list[dict] | None = None) -> str:
    """Build the query string used for RAG retrieval.

    Vague follow-up questions like "and what about X?" don't retrieve
    well on their own — we prepend the last 1–2 user questions so the
    BM25/vector search has the topic context. Only user turns are
    included; assistant answers would dilute the query with restated
    journal prose.
    """
    history = history or []
    prior_user_qs = [
        (turn.get("text") or "").strip()
        for turn in history
        if turn.get("role") == "user"
    ][-2:]
    prior_user_qs = [q for q in prior_user_qs if q]
    if not prior_user_qs:
        return question
    return " | ".join(prior_user_qs + [question])


@dataclass
class ChatAnswer:
    answer: str
    hits: list[Hit]
    model: str


def ask(conn, question: str, *, model: str = "sonnet", k: int = 8,
        binary: str = "claude",
        history: list[dict] | None = None) -> ChatAnswer:
    """Answer a journal question with optional multi-turn context.

    `history`: list of prior turns as {role: 'user'|'assistant', text}
      dicts, oldest first. When present, the model sees the prior
      conversation (constructed in-message — we still call the CLI as
      a single non-persistent invocation) and the RAG query is enriched
      with the last 1–2 user questions so vague follow-ups resolve
      against the right corpus context. Empty / None means single-shot.
    """
    history = history or []
    rag_query = _retrieval_query(question, history)
    hits = retrieve(conn, rag_query, k=k)
    user_msg = _build_chat_message(question, hits, history=history)

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
