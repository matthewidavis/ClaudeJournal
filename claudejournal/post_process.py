"""Post-process narrator prose: rewrite date brackets to links,
detect unanchored past-tense references as hallucination warnings."""
from __future__ import annotations

import html
import re


_ANCHOR_RX = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")

# Inline markdown — conservative, no headers/lists. Applied AFTER html escape,
# so ordering handles the fact that `` ` `` / `*` aren't html-escaped.
_CODE_RX = re.compile(r"`([^`\n]+)`")
_BOLD_RX = re.compile(r"\*\*([^*\n]+)\*\*")
_ITAL_RX = re.compile(r"(?<![\*A-Za-z0-9])\*([^*\n]+)\*(?![\*A-Za-z0-9])")
# Bare URL autolinker. Runs AFTER html.escape, so we match the escaped form.
# Stop at whitespace or quote-ish chars; trim trailing punctuation that's
# almost always sentence-terminal rather than part of the URL.
_URL_RX = re.compile(r"(https?://[^\s<>\"'()\[\]]+)")
_URL_TRAILING_PUNCT = ".,;:!?"


def _autolink_urls(escaped: str) -> str:
    def _sub(m: re.Match) -> str:
        url = m.group(1)
        trail = ""
        while url and url[-1] in _URL_TRAILING_PUNCT:
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return m.group(0)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>{trail}'
    return _URL_RX.sub(_sub, escaped)


def _apply_inline_markdown(escaped: str) -> str:
    # URLs first so the markdown passes don't mangle underscores/asterisks in them.
    s = _autolink_urls(escaped)
    s = _CODE_RX.sub(r"<code>\1</code>", s)
    s = _BOLD_RX.sub(r"<strong>\1</strong>", s)
    s = _ITAL_RX.sub(r"<em>\1</em>", s)
    return s

# Past-tense temporal phrases that should carry an anchor bracket.
# If one of these appears WITHOUT a nearby [YYYY-MM-DD] on the same sentence,
# flag as a hallucination signal.
_PAST_TENSE_PHRASES = [
    r"\byesterday\b",
    r"\blast (?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\bthe other day\b",
    r"\ba (?:few|couple (?:of )?)? ?days? (?:ago|back)\b",
    r"\bearlier (?:this (?:week|month)|today)\b",
    r"\b(?:two|three|four|five|six|seven) days (?:ago|back)\b",
    r"\bon (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
]
_PAST_RX = re.compile("|".join(_PAST_TENSE_PHRASES), re.IGNORECASE)


def link_anchors(prose: str, base_path: str = "./") -> str:
    """Replace [YYYY-MM-DD] with <a href="{base_path}index.html#YYYY-MM-DD">[YYYY-MM-DD]</a>.

    The journal is feed-shaped — every day lives as a fragment of the home
    page (or the relevant project's index). base_path should be the relative
    path from the current page up to either the site root or the project root.

    Returns HTML-safe text with anchor links embedded. Caller should NOT
    html-escape the result again.
    """
    escaped = html.escape(prose, quote=False)
    escaped = _apply_inline_markdown(escaped)
    # When base_path is "./" and we're already on the feed page, a bare
    # fragment "#date" scrolls without reloading. Otherwise use the explicit
    # index.html#date form so it works from nested pages too.
    if base_path in ("", "./"):
        def _sub(m: re.Match) -> str:
            return f'<a class="anchor" href="#{m.group(1)}">[{m.group(1)}]</a>'
    else:
        def _sub(m: re.Match) -> str:
            return f'<a class="anchor" href="{base_path}index.html#{m.group(1)}">[{m.group(1)}]</a>'
    return _ANCHOR_RX.sub(_sub, escaped)


def detect_unanchored(prose: str) -> list[str]:
    """Return list of unanchored past-tense phrases — hallucination signals."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", prose):
        if not sentence.strip():
            continue
        past = _PAST_RX.findall(sentence)
        if not past:
            continue
        if _ANCHOR_RX.search(sentence):
            continue
        # Past tense phrase without an anchor in same sentence.
        # findall returns str when the regex has 0 or 1 group, tuple of
        # group strings when it has multiple. _PAST_RX has multiple
        # alternations with no capturing groups → str. Tuple branch
        # kept for safety in case the regex evolves.
        for hit in past:
            if isinstance(hit, tuple):
                phrase = next((x for x in hit if x), "")
            else:
                phrase = hit
            if phrase:
                out.append(f"{phrase!r} in: {sentence.strip()[:160]}")
    return out


def anchored_dates(prose: str) -> list[str]:
    return sorted(set(_ANCHOR_RX.findall(prose)))
