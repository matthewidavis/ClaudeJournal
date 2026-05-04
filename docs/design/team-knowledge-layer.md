# Team knowledge layer — strategy note (FUTURE DIRECTION)

**Status:** Future direction, not deferred implementation.
**Date:** 2026-05-04.
**Originating discussion:** session that shipped weekly v3 + monthly
v3 prompt hardening, captured the multi-source ingestion + yearly
rollup design notes, and explored "could ClaudeJournal go team-wide?"

This is **not** a design note in the same sense as
`multi-source-ingestion.md` or `yearly-rollup.md`. Those are deferred
*implementations* — known files, known signatures, ready to pick up.
This document is **strategy and positioning** — capturing the
conceptual framing for a future product so that if/when the
conversation resumes, we don't relitigate the boundaries.

---

## The question that prompted this

User raised: *"We currently have designed this as a single user / PC
solution. I have mentioned larger goals of combining such from
multiple users — like a team or department. I started wondering about
the value of a journal at a group or department or company level and
think likely has little value. What does have more value is something
more akin to the WikiLLM but possibly feeding from these independent
ClaudeJournals?"*

The instinct is right. This document captures **why** a team journal
has little value and what the actually-valuable team-level artifact
looks like instead.

---

## Why a "team journal" is the wrong product

A journal is **first-person, retrospective, voice-driven**. That's
the whole point — it's *your* journal, in *your* voice, reflecting on
*your* experience.

Aggregating multiple people's first-person reflections into one
artifact:

- **Strips the voice.** "I spent the morning on…" becomes incoherent
  across authors. Picking one voice is a lie. Writing in committee
  voice ("the team explored…") loses the emotional truth that makes
  a journal warm.
- **Loses the strategic clarity of real synthesis.** A real synthesis
  picks a position and makes claims. A merged-journal of five days
  by five authors makes no claims; it just lists experiences.
- **Produces neither a true diary nor a true knowledge artifact.**
  Worst of both worlds.

The same failure applies one layer up: a team-level project arc
retrospective would either pick one voice (false to the others) or
read as committee prose (true to no one). The synthesis layer
ClaudeJournal operates on — first-person past-tense reflective prose
— **does not survive aggregation across authors.**

---

## What the actually-valuable team artifact is

The architecture we've already built points at the right answer:
**a shared knowledge surface that absorbs structured signal from each
person's individual journal, produces wiki-style content with
attribution, and stays clearly separate from the personal layer.**

Roughly: WikiLLM, fed by individual ClaudeJournals as private
upstream data sources.

### Three layers, in order

#### Layer 1 — Personal journal (today's product, unchanged)

Each person runs ClaudeJournal locally on their machine. Corpus is
private. Daily entries, narrations, arcs are theirs alone, in their
voice. Nothing about going team-wide changes this layer.

#### Layer 2 — Curated extracts (new, narrow surface)

Each journal **publishes a sanitised, structured subset** to a shared
destination. Critically: not the prose, not the friction, not the
emotional arc. Specifically the **factual atoms with provenance**:

- Individual `learned` items
- Entity profile claims (e.g. "PTZOptics encodes pan/tilt as signed
  24-bit at 32,768 units per degree")
- Resolved-friction patterns ("atomic write via .tmp rename prevents
  power-loss checkpoint corruption")
- Explicit feedback / standing notes the user marks publishable

What this layer **isn't**: prose synthesis. What it **is**:
publishable knowledge atoms, each carrying author + date + project
context as provenance.

Crucial design decisions for this layer:

- **Default private.** Authors must opt in per item or per category.
  Never auto-publish.
- **Attribution preserved.** Every atom carries `author`, `date`,
  `source_project`. The wiki uses these.
- **Reversible.** Authors can unpublish.
- **Diff-able / reviewable.** Treating publishable extracts like a
  pull request lets the author check what's leaving their personal
  corpus.

#### Layer 3 — Team WikiLLM (the actual product)

A separate system ingests the curated extracts from N team members
and synthesises:

- **Topic pages, multi-author.** Everything the team has learned
  about FreeD across all members. Each claim cited per author
  ("learned by Matthew 2026-04, corroborated by Sarah 2026-06,
  contradicted by Jordan 2026-09 → see disagreement").
- **Disagreement surfacing.** When two team members' learnings
  contradict, the wiki shows both with attribution. The
  disagreement itself is signal worth surfacing.
- **Onboarding artifact.** New team members read the team wiki to
  absorb hard-won knowledge without making five people retell.
- **Cross-author transfer.** Same shape as ClaudeJournal's
  cross-project transfer: "Sarah's FastAPI lessons might apply to
  your current project," but across people.
- **No first-person prose.** The wiki reads like a wiki — atomic
  claims, sources, structured. Not a diary written by committee.

---

## The clean separation

The reason this is the right shape: it preserves what each layer is
good at.

| Layer | Form | Voice | Audience | Privacy |
|---|---|---|---|---|
| Personal journal | Reflective prose | First person | Self | Private |
| Curated extracts | Structured atoms | Author-attributed | Team layer | Opt-in publish |
| Team wiki | Wiki articles | Synthesised, sourced | Team | Shared |

The journal stays personal because it's still personal. The wiki
becomes possible because the data feeding it is *already* structured
factual atoms — exactly the format wikis want, exactly the format
journals already extract via `learned` / `entities` / `feedback
memories`.

---

## What we already have that maps onto this

The architecture in the personal product already produces nearly
everything the curated-extract layer would publish:

- **Brief schema** (`learned`, `friction`, `wins`, `tags`) — the
  natural extract format. Each `learned` bullet is already a
  publishable atom.
- **Entity profile pages** — already mini-wiki-articles. They have
  one author today; the team wiki has many authors per page.
- **Annotations table** — the explicit-correction / explicit-pin
  surface. Annotations marked "publishable" become high-trust
  team-wiki contributions.
- **Cross-project connections** — exactly what cross-author transfer
  would look like, just generalized over authors instead of
  projects.
- **MCP layer** — a future MCP tool like `journal_publish_extract`
  could push to the team wiki, leaving the source journal
  untouched.

So the team layer **isn't a separate codebase**. It's a downstream
consumer of structured outputs ClaudeJournal already produces.

---

## Real strategic decisions deferred to that later conversation

If/when we pick this up, these aren't trivial:

1. **Where the team wiki lives.** Shared server? Git repo of
   synthesised pages? Cloud DB? Each has trade-offs (control vs.
   discoverability vs. ops burden).
2. **Publish workflow.** Self-publish (low friction, high noise) vs.
   PR-style review (gatekept, slower) vs. automatic-with-undo
   (middle ground).
3. **Conflict semantics.** Always show both? Prefer most recent?
   Pin-to-author for personal-experience claims, surface-disagreement
   for factual ones?
4. **Onboarding access model.** Read-only team membership? Self-serve
   invite? Org-level licensing?
5. **What stays private always.** Friction / mood / personal
   reflections never publish, even with author opt-in. The personal
   layer's *emotional* content is non-publishable as a category.
6. **Cross-team / cross-org.** Does this scale to "multiple teams in
   one company" or "multiple companies in one industry," or is the
   trust model strictly per-team?

These are product-strategy questions, not implementation questions.
They get answered when there's a real audience to ship to.

---

## Why this is "future direction" not "deferred implementation"

The other design notes in this directory describe known refactors
or features against a working product, with file lists and effort
estimates. This one describes **a different product** with a
different audience.

Concretely:

- **Multi-source ingestion** is "extend the existing personal
  product to absorb non-Claude-Code sources." Same audience, more
  inputs.
- **Yearly rollup** is "complete the daily → weekly → monthly →
  yearly cascade." Same audience, fuller cascade.
- **Team knowledge layer** is "build a different product that
  consumes the personal product's outputs." Different audience,
  different surface.

Conflating these would be a category error. The team wiki is
worth doing only after:

1. The personal product is solid and proven (today: shipped, getting
   refined session-by-session).
2. There is a real team that wants this (today: hypothetical).
3. The strategic decisions in the previous section have answers
   (today: open).

When all three are true, this document captures the conceptual
framing so the design conversation can start from "given this
framing, what do we build?" rather than relitigating the journal-
vs-wiki distinction.

---

## When to revisit

**Trigger conditions:**

- A team or org expresses concrete interest in shared journal-derived
  knowledge.
- The personal product has been used at scale for long enough that
  its data outputs are demonstrably useful (not just "interesting").
- Strategic clarity exists on at least 3 of the 6 deferred decisions
  above.

**Anti-trigger conditions** (when it would be premature):

- "Wouldn't it be cool if a team could share journals?" with no
  specific team.
- The personal product still has rough edges in core surfaces.
- No clear answer to "what stays private."

---

## Note on the "WikiLLM" framing

The user invoked WikiLLM as an analogue. The framing is right:
WikiLLM is structured, sourced, multi-contributor knowledge that
synthesises across atomic contributions. That's what a team
knowledge layer over journals looks like.

Worth borrowing more than just the name when the time comes:
WikiLLM's surfacing of disagreement, attribution, and revision
history is exactly the contract a team-knowledge layer needs.
