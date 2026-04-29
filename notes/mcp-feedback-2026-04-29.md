# MCP feedback — 2026-04-29

User-captured after running the MCP tools against the live corpus from
another Claude session. Honest assessment of value + friction points
worth addressing in a follow-up pass.

---

## What worked (the user's words)

> Genuinely useful. Three of these findings (lowercase API, normalization
> assumption, NDI bandwidth flag) would each have caused real time loss.
> The arc summaries are the standout — they read like retrospectives a
> thoughtful collaborator would write, which is much higher signal than
> raw transcript grep would produce.

The arc-summary tool (`journal_arc`) is doing exactly what it was meant
to do — surface synthesized retrospectives that compress hundreds of
brief details into a coherent narrative an agent can actually read.

---

## Friction point 1: entity coverage gap

> `journal_tools` had nothing for SCRFD/ArcFace/FAISS but the tools
> clearly exist in your work. Tool extraction pass may be missing them,
> or the trade show project just isn't in the corpus.

**Diagnostic check:** verify whether SCRFD / ArcFace / FAISS appear in
any session brief's prose. If yes, the entity extractor is missing them
— suggests the v2 prompt's exclusion list is too aggressive or the
model doesn't recognize them as named libraries (they read as
acronyms/proper nouns the haiku model may demote to generic terms).

If no, the corpus simply doesn't include the trade-show project's
sessions yet.

**Fix paths:**
- Re-run entity extraction with a tightened prompt that explicitly
  asks for ML-specific libraries (SCRFD, ArcFace, FAISS, ONNX, BLIP,
  etc.) — would re-process all 371 briefs at ~$0.10 cost.
- OR add a post-extraction enrichment pass that scans brief prose
  for known-library tokens against a curated allowlist (cheaper, no
  model calls, but maintenance overhead).
- OR the user manually seeds entities they know are missing via a
  small CLI subcommand `claudejournal entity add <name> <type>`.

The seeding approach is probably the right v1 — it lets the user fix
gaps when they notice them without burning tokens to re-extract the
whole corpus.

---

## Friction point 2: tag coverage trails search coverage

> `journal_topic("face-recognition")` returned nothing despite the
> search clearly finding face-recognition-adjacent content. Tag
> coverage trails search coverage by a lot.

**Real bug.** Tags come from the brief's `tags` field which is haiku-
extracted from session content. Search (FTS5) operates over the full
narration prose. So a topic the user discusses extensively might
never get tagged with the exact phrase the user later searches for.

**Fix paths:**
- **Soft-match in `journal_topic`:** when the exact tag returns nothing,
  fall back to FTS5 search for the term, group hits by date, and
  surface "no exact tag match for X — found N related entries via
  search, want them?" Same UX as the third friction point.
- **Tag synonyms / aliases:** allow the user to map "face-recognition"
  to a known tag like "face-detection" via a small JSON file or DB
  table. Manual but precise.
- **Re-tag via search:** for any term with strong search hits but no
  tag, retroactively add the tag to the matching briefs. Risky — pollutes
  the original brief data; not recommended.

The soft-match fallback is the cheapest and most honest. It tells the
user the tag doesn't exist while still giving them the content they
asked about.

---

## Friction point 3: empty-result ambiguity

> `journal_open_loops("trade show")` returned nothing — fine, but I'd
> want to know whether that means "no open loops" or "no project name
> match." A "0 matches, did you mean: …" hint would help.

**Universal across the MCP read tools.** Every tool that takes a
filter argument has the same ambiguity — `journal_open_loops`,
`journal_arc`, `journal_topic`, `journal_tools`, etc.

**Fix:** when a filtered query returns zero results, don't just say
"No matches." Compute the closest matches in the relevant index and
surface them as suggestions. For project filters, fuzzy-match against
known project display_names. For topic filters, fuzzy-match against
known tag list. For tool filters, fuzzy-match against entity canonical
names.

Format: `"No matches for 'trade show'. Did you mean: 'allThePictures-main',
'ShotSquire-main', 'agenticptz-main'?"` (top 3 by edit distance).

This is a small, uniform change — extract a `_suggest_matches(query,
candidates, limit=3)` helper and use it in every tool's empty-result
path.

---

## Recommended priority

All three are real and the user has the right diagnosis on each.

1. **Friction #3 (empty-result hints)** — smallest change, biggest
   universal UX improvement, applies to every MCP tool. Do first.
2. **Friction #2 (tag→search fallback)** — fixes the "I know this
   exists but the journal can't find it" failure mode. Most painful
   in practice.
3. **Friction #1 (entity coverage gap)** — slightly different shape
   (data-quality, not tool-UX). Manual `entity add` CLI is probably
   the right v1; full re-extraction can come later if the user keeps
   noticing gaps.

All three together are probably half a day of work, no model-call
costs, no schema changes.
