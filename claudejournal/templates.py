"""Warm diary templates — feed-first, centered reading column, floating chat."""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Iterable

from claudejournal.post_process import link_anchors, link_doc_titles, link_topic_titles


CSS = """
:root {
  --bg: #faf6ec;
  --paper: #fffaf0;
  --fg: #2a211b;
  --muted: #8a7f70;
  --accent: #8a4a1f;
  --accent-soft: #b97a4a;
  --rule: #ebe3d3;
  --chip: #f0e8d5;
  --warn: #9a3d20;
  --ok: #4d6a3a;
  --shadow: 0 2px 14px rgba(70, 50, 20, 0.06);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font: 17px/1.72 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, ui-serif, serif;
  color: var(--fg); background: var(--bg);
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.wrap { max-width: 720px; margin: 0 auto; padding: 48px 24px 120px; }
.site-head { text-align: center; margin-bottom: 48px; }
.site-head h1 {
  font-size: 38px; margin: 0; letter-spacing: -0.01em;
  font-weight: 500; color: var(--fg);
}
.site-head h1 a.site-home-link {
  color: inherit; text-decoration: none;
  cursor: pointer; transition: opacity 0.15s ease;
}
.site-head h1 a.site-home-link:hover { opacity: 0.7; }
.site-head .sub {
  color: var(--muted); font-size: 14px; margin-top: 6px;
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
}
.filter-bar {
  margin: 22px auto 0; padding-top: 18px; border-top: 1px solid var(--rule);
}
.filter-row {
  display: flex; flex-wrap: wrap; gap: 6px; justify-content: center;
  min-height: 26px;
}
/* Navigation row sits beneath the filter-modes row with a touch of extra
   space so the two reads as visually distinct groups. */
.filter-nav { margin-top: 4px; margin-bottom: 4px; opacity: 0.95; }
.filter-nav:empty { display: none; }
.filter-axes { margin-bottom: 8px; }
.filter-options:empty { display: none; }
.filter-chip {
  display: inline-block; padding: 3px 12px; font-size: 12px;
  border: 1px solid var(--rule); border-radius: 14px; cursor: pointer;
  background: var(--paper); color: var(--muted); text-decoration: none;
  font-family: ui-monospace, Consolas, monospace;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}
.filter-chip:hover { border-color: var(--accent-soft); color: var(--accent); }
.filter-chip.active {
  background: var(--accent); color: var(--paper); border-color: var(--accent);
}
.filter-chip.active:hover { background: #6f3916; }
.filter-chip.active::after { content: " ×"; opacity: 0.8; }
.filter-chip.axis { font-weight: 500; }

/* Row 0 — "mode" chips (Find + view: Daily/Weekly/Monthly). Softer default
   look distinguishes them from the per-facet axis row. Active state is the
   same accent fill so the selection feedback stays consistent across rows. */
.filter-modes { margin-bottom: 8px; }
.filter-chip.mode {
  border-color: var(--accent-soft);
  color: var(--accent);
  background: transparent;
  font-weight: 500;
}
.filter-chip.mode:hover { background: var(--accent-soft); color: var(--paper); border-color: var(--accent-soft); }
.filter-chip.mode.active { background: var(--accent); color: var(--paper); border-color: var(--accent); }
.filter-chip.mode.active:hover { background: #6f3916; }

/* Clear chip — sits at the end of Row 0. Distinct from filter-state chips:
   dark ink foreground on paper, outlined. On the main feed it resets
   filters in place; on deep-link pages it navigates to the feed. Same
   visual treatment either way so it reads as one consistent affordance. */
.filter-chip.home {
  border-color: var(--fg);
  color: var(--fg);
  background: transparent;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.filter-chip.home:hover {
  background: var(--fg); color: var(--paper); border-color: var(--fg);
}
.filter-empty {
  text-align: center; color: var(--muted); font-style: italic;
  padding: 40px 20px; font-size: 15px;
}

/* Long option pools (e.g. 100+ topics) — inline filter + scroll */
.filter-longpool {
  display: flex; flex-direction: column; gap: 8px; align-items: center;
  width: 100%; max-width: 680px; margin: 0 auto;
}
.filter-longpool-search {
  width: min(360px, 80%); padding: 5px 12px; font-size: 12px;
  border: 1px solid var(--rule); border-radius: 14px;
  background: var(--paper); color: var(--fg);
  font-family: ui-monospace, Consolas, monospace; outline: none;
}
.filter-longpool-search:focus { border-color: var(--accent-soft); }
.filter-longpool-list {
  display: flex; flex-wrap: wrap; gap: 6px; justify-content: center;
  max-height: 220px; overflow-y: auto; width: 100%;
  padding: 6px 8px; border: 1px solid var(--rule); border-radius: 10px;
  background: var(--paper);
}
.filter-longpool-empty {
  color: var(--muted); font-style: italic; font-size: 12px;
  padding: 20px; font-family: ui-serif, Georgia, serif;
}

/* Hide controls — filter and search apply independently; either hides. */
article.entry.filter-hidden, article.entry.search-hidden,
.week-break.filter-hidden, .week-rollup-wrap.filter-hidden,
.week-break.search-hidden, .week-rollup-wrap.search-hidden {
  display: none !important;
}

/* Search match highlighting — punchy so hits are obvious in dense prose. */
article.entry mark.search-hit, .week-rollup mark.search-hit,
.inspect-content mark {
  background: #ffd24a; color: #1a1a1a;
  padding: 0 3px; border-radius: 3px;
  font-weight: 600;
  box-shadow: 0 0 0 1px rgba(140, 90, 20, 0.25);
}

/* Inline search input — fills the sub-chip row when "Search" axis is active */
.filter-search {
  font: 13px/1.4 ui-monospace, Consolas, monospace;
  padding: 4px 12px; min-width: 260px;
  border: 1px solid var(--accent-soft); border-radius: 14px;
  background: var(--paper); color: var(--fg); outline: none;
}
.filter-search:focus { border-color: var(--accent); }
.filter-search-hint {
  font: 11px ui-monospace, Consolas, monospace; color: var(--muted);
  align-self: center;
}
article.entry mark.search-hit,
.week-rollup mark.search-hit {
  background: #ffe49a; color: inherit; padding: 0 2px; border-radius: 2px;
}

/* Day entry */
.entry { margin: 56px 0; scroll-margin-top: 32px; }
.entry-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 16px; margin-bottom: 14px; padding-bottom: 8px;
  border-bottom: 1px solid var(--rule);
}
.entry-head h2 {
  font-size: 24px; margin: 0; font-weight: 500; letter-spacing: -0.005em;
}
.entry-head h2 .year {
  font-size: 0.62em; color: var(--muted); font-weight: 400;
  letter-spacing: 0.04em; margin-left: 2px;
  font-family: ui-monospace, Consolas, monospace;
  vertical-align: 0.15em;
}
.entry-head {
  /* Day-identity row: title on the left, header chips (currently just
     "updated") flowing to the right. The .meta wrapper preserves the
     existing layout slot; chips inside use the standard inspect-chip
     styling so they read consistently with the bottom row. */
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 8px;
}
.entry-head h2 { flex: 1 1 auto; }
.entry-head .meta {
  flex: 0 0 auto;
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace; white-space: nowrap;
  display: inline-flex; gap: 6px; align-items: baseline;
}

/* TTS — per-entry play buttons. Audio is served from pre-rendered WAVs
   under out/audio/. No browser-side fallback: if a WAV is missing the
   button opens the "still being generated" modal instead of trying to
   synthesize in-browser (which was unreliable and required ~100MB of
   model downloads). */
.tts-play {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; margin-left: 8px; padding: 0;
  border: 1px solid var(--rule); border-radius: 50%;
  background: var(--paper); color: var(--accent-soft); cursor: pointer;
  font-size: 10px; line-height: 1; vertical-align: 0.15em;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.tts-play:hover { border-color: var(--accent); color: var(--accent); background: var(--chip); }
.tts-play.playing { background: var(--accent); color: var(--paper); border-color: var(--accent); }
.tts-play.paused { background: var(--accent-soft); color: var(--paper); border-color: var(--accent-soft); }
.tts-play.loading { opacity: 0.6; cursor: wait; }

/* "Audio not ready" modal — shown when a play click resolves to a
   missing WAV. Message is deliberately single-state (piper is now a
   hard pip dependency, so "install piper" is no longer a real case). */
#tts-not-ready {
  position: fixed; inset: 0; background: rgba(250, 246, 236, 0.75);
  backdrop-filter: blur(2px); -webkit-backdrop-filter: blur(2px);
  display: none; align-items: center; justify-content: center;
  z-index: 60; padding: 24px;
}
#tts-not-ready.open { display: flex; }
#tts-not-ready .tts-nr-card {
  max-width: 420px; background: var(--paper); border: 1px solid var(--rule);
  border-radius: 10px; padding: 22px 26px;
  box-shadow:
    0 1px 3px rgba(70, 50, 20, 0.08),
    0 10px 30px rgba(70, 50, 20, 0.18);
}
#tts-not-ready h3 { margin: 0 0 8px; font-size: 16px; font-weight: 500; }
#tts-not-ready p { margin: 0 0 14px; font-size: 14px; line-height: 1.55; color: var(--fg); }
#tts-not-ready button {
  padding: 7px 16px; border-radius: 4px; cursor: pointer;
  border: 1px solid var(--accent-soft); background: var(--accent-soft);
  color: var(--paper); font: 13px ui-sans-serif, system-ui, sans-serif;
}
#tts-not-ready button:hover { background: var(--accent); border-color: var(--accent); }
.entry-body p { margin: 0 0 16px; }
.entry-body p:last-child { margin-bottom: 0; }
.entry-body a.anchor {
  color: var(--accent); text-decoration: none; font-size: 0.8em;
  border-bottom: 1px dotted var(--accent); padding: 0 2px;
  white-space: nowrap; vertical-align: baseline;
}
.entry-body a.anchor:hover { background: var(--chip); }
.entry-body code, .a-row code, .week-rollup code {
  background: var(--chip); padding: 1px 6px; border-radius: 3px;
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  font-size: 0.86em; color: #5a3618;
}
.entry-body strong, .a-row strong, .week-rollup strong { font-weight: 600; color: var(--fg); }
.entry-body em, .a-row em, .week-rollup em { font-style: italic; }
.entry-empty {
  color: var(--muted); font-style: italic; font-size: 15px;
  padding: 8px 0 4px; border-left: 2px solid var(--rule); padding-left: 14px;
}

/* Inspect chips — per-category toggles, multiple can be open at once */
.inspect-row {
  margin-top: 18px; padding-top: 10px;
  border-top: 1px dotted var(--rule);
  /* Four chips per row on desktop, falling to two on narrow screens.
     Grid auto-places the chips, so 8 chips render as 4 + 4 (or 4 + 3
     etc. when fewer present). Each chip stretches to fill its cell so
     the row reads as a tidy table-style block rather than ragged
     flex-wrap. */
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
@media (max-width: 600px) {
  .inspect-row { grid-template-columns: repeat(2, 1fr); }
}
.inspect-chip {
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  border: 1px solid var(--rule); background: var(--paper);
  color: var(--muted); padding: 3px 12px; border-radius: 14px;
  cursor: pointer; user-select: none;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}
.inspect-chip:hover { border-color: var(--accent-soft); color: var(--accent); }
.inspect-chip.open {
  background: var(--accent); color: var(--paper); border-color: var(--accent);
}
/* Parenthetical count inside chip labels — keeps the at-a-glance signal
   without the count dominating the chip width. Color matches the chip's
   own muted-text shade so it reads as legibly as the label itself. */
.inspect-chip .chip-count {
  color: inherit; font-weight: 400;
  margin-left: 1px;
}
.inspect-panel {
  margin-top: 12px; font-size: 14px; line-height: 1.6;
  padding: 12px 18px; background: var(--paper);
  border-left: 2px solid var(--rule); border-radius: 2px;
  max-height: 520px; overflow-y: auto;
  position: relative;
}
.inspect-panel[hidden] { display: none; }
.brief-block + .brief-block {
  margin-top: 18px; padding-top: 18px; border-top: 1px dashed var(--rule);
}
.inspect-search {
  position: sticky; top: 0; z-index: 1;
  display: block; width: 100%; box-sizing: border-box;
  font: inherit; font-family: ui-monospace, Consolas, monospace;
  font-size: 12.5px; padding: 5px 10px;
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--bg); color: var(--fg);
  margin-bottom: 10px;
}
.inspect-search:focus { outline: none; border-color: var(--accent-soft); }
.inspect-content { font-size: inherit; }
.inspect-content .hidden-by-filter { display: none; }
.inspect-empty-match {
  color: var(--muted); font-style: italic; font-size: 13px;
  padding: 8px 0;
}
.inspect-content mark {
  background: #ffe49a; color: inherit; padding: 0 2px; border-radius: 2px;
}
.inspect-panel h4 {
  font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); margin: 10px 0 6px; font-weight: 500;
}
.inspect-panel h4:first-child { margin-top: 0; }
.inspect-panel ul { padding-left: 18px; margin: 4px 0; }
.inspect-panel li { margin: 2px 0; }
.inspect-panel .files li {
  font-family: ui-monospace, Consolas, monospace; font-size: 12.5px;
  color: var(--muted); list-style: none; padding-left: 0;
}
.inspect-panel blockquote {
  margin: 6px 0; padding: 2px 12px; border-left: 2px solid var(--accent-soft);
  color: #4a3a2d; font-size: 14px;
}
.inspect-panel blockquote.correction { border-left-color: var(--warn); }
.inspect-panel blockquote.appreciation { border-left-color: var(--ok); }
.inspect-panel .snippet {
  margin: 6px 0; padding: 2px 12px; border-left: 2px solid #c9a368;
  color: #4a3a2d; font-size: 14px; font-style: italic;
}

/* Week divider (chapter break) */
.week-break {
  text-align: center; margin: 72px 0 56px; color: var(--muted);
  font-family: ui-serif, Georgia, serif; font-style: italic;
  position: relative;
}
.week-break::before, .week-break::after {
  content: ""; display: inline-block; width: 40px; height: 1px;
  background: var(--accent-soft); vertical-align: middle; margin: 0 16px;
}
.week-break a { color: var(--accent); text-decoration: none; }
.week-break a:hover { border-bottom: 1px solid var(--accent-soft); }
.week-rollup {
  max-width: 640px; margin: 20px auto 0; padding: 18px 24px;
  background: var(--paper); border-left: 3px solid var(--accent-soft);
  border-radius: 2px; box-shadow: var(--shadow);
  font-size: 15.5px; line-height: 1.7; text-align: left;
}
.week-rollup p { margin: 0 0 12px; } .week-rollup p:last-child { margin: 0; }

.month-break {
  text-align: center; margin: 96px 0 64px; color: var(--accent);
  font-family: ui-serif, Georgia, serif; font-weight: 500;
  font-size: 17px; letter-spacing: 0.03em;
  position: relative;
}
.month-break::before, .month-break::after {
  content: ""; display: inline-block; width: 72px; height: 2px;
  background: var(--accent-soft); vertical-align: middle; margin: 0 20px;
}
.month-rollup {
  max-width: 680px; margin: 28px auto 0; padding: 22px 28px;
  background: var(--paper); border-left: 4px solid var(--accent);
  border-radius: 2px; box-shadow: var(--shadow);
  font-size: 16px; line-height: 1.75; text-align: left;
}
.month-rollup p { margin: 0 0 14px; } .month-rollup p:last-child { margin: 0; }
.month-break.filter-hidden, .month-rollup-wrap.filter-hidden,
.month-break.search-hidden, .month-rollup-wrap.search-hidden {
  display: none !important;
}
.month-rollup mark.search-hit { background: #f5e6a8; }

/* ── Per-document page ──────────────────────────────────────────────── */
.doc-page {
  max-width: 720px; margin: 20px auto; padding: 22px 28px;
  background: var(--paper); border: 1px solid var(--rule);
  border-radius: 4px; box-shadow: var(--shadow);
  font-size: 15px; line-height: 1.65;
}
.doc-page h2 { margin: 0 0 4px; font-size: 22px; font-weight: 500; }
.doc-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 14px;
}
.doc-meta code {
  background: var(--chip); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; margin-right: 2px;
}
.doc-hook {
  font-size: 16px; line-height: 1.55; color: var(--fg);
  font-style: italic; border-left: 3px solid var(--accent-soft);
  padding: 4px 0 4px 14px; margin: 0 0 16px;
}
.doc-section { margin: 18px 0; }
.doc-section h3 {
  font-size: 13px; font-weight: 600; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 6px;
}
.doc-section p { margin: 0 0 10px; }
.doc-section ul { padding-left: 22px; margin: 4px 0; }
.doc-section li { margin: 4px 0; }
.doc-note {
  background: var(--bg); padding: 12px 16px; border-radius: 4px;
  border-left: 3px solid var(--accent-soft);
}
.doc-note p { font-style: italic; color: var(--fg); }
.doc-download {
  margin: 22px 0 6px; font-size: 13px;
}
.doc-download a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent); padding-bottom: 1px;
}
.doc-download a:hover { border-bottom-style: solid; }
.doc-ext { color: var(--muted); font-size: 12px; }
.doc-excerpt { margin-top: 22px; }
.doc-excerpt summary {
  cursor: pointer; font-size: 13px; color: var(--muted);
  padding: 8px 0; border-top: 1px solid var(--rule);
  font-family: ui-monospace, Consolas, monospace;
}
.doc-excerpt summary:hover { color: var(--accent); }
.doc-excerpt-hint { opacity: 0.7; font-size: 11px; }
.doc-excerpt-body {
  padding: 12px 0; font-size: 14px; line-height: 1.6;
  color: var(--fg); font-family: ui-serif, Georgia, serif;
}
.doc-excerpt-body p { margin: 0 0 10px; }
.doc-trunc { color: var(--muted); font-style: italic; font-size: 13px; }

/* Document entry in the main feed (Library view). Sits alongside daily
   entries as a peer; slightly tighter block to visually distinguish. */
article.entry.doc-entry {
  background: var(--paper); border-left: 3px solid var(--accent-soft);
  padding: 14px 18px; margin: 14px 0;
}
article.entry.doc-entry .meta { color: var(--accent); opacity: 0.85; }
.doc-feed-hook {
  font-size: 15px; line-height: 1.55; font-style: italic;
  color: var(--fg); margin: 8px 0;
}
.doc-feed-section { margin: 12px 0; }
.doc-feed-section h4 {
  margin: 0 0 4px; font-size: 12px; font-weight: 600;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em;
}
.doc-feed-section p { margin: 0 0 6px; font-size: 14px; line-height: 1.6; }
.doc-feed-section ul { padding-left: 22px; margin: 4px 0; font-size: 14px; line-height: 1.6; }
.doc-feed-section li { margin: 3px 0; }
.doc-feed-note {
  background: var(--bg); padding: 10px 14px; border-radius: 4px;
  border-left: 3px solid var(--accent-soft);
}
.doc-feed-note p { font-style: italic; color: var(--fg); margin: 0; }
.doc-feed-tags { margin: 6px 0; }
.doc-feed-tags code {
  background: var(--chip); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; color: var(--muted); margin-right: 3px;
}
.doc-feed-more {
  margin: 10px 0 0; font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
}
.doc-feed-more a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent);
}

/* Doc title links — wherever a narrator mentioned a document, its title
   is wrapped in this class so clicking lands on the doc's page. */
a.doc-link {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent-soft);
  padding-bottom: 1px;
}
a.doc-link:hover { border-bottom-style: solid; }

/* Topic title links — wherever narration prose uses a tag name that has a
   topic page, it's wrapped in this class. Dashed underline distinguishes
   from doc-link (dotted) so the two affordances are visually distinct. */
a.topic-link {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dashed var(--accent-soft);
  padding-bottom: 1px;
}
a.topic-link:hover { border-bottom-style: solid; }

/* ── Per-topic wiki page ────────────────────────────────────────────── */
.topic-page {
  max-width: 720px; margin: 20px auto; padding: 22px 28px;
  background: var(--paper); border: 1px solid var(--rule);
  border-left: 4px solid var(--accent-soft);
  border-radius: 4px; box-shadow: var(--shadow);
  font-size: 15px; line-height: 1.7;
}
.topic-page h2 { margin: 0 0 6px; font-size: 22px; font-weight: 500; }
.topic-tag-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin-bottom: 14px;
  font-family: ui-monospace, Consolas, monospace;
}
.topic-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 16px;
}
.topic-meta code {
  background: var(--chip); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; margin-right: 2px;
}
.topic-body { margin: 0 0 18px; }
.topic-body p { margin: 0 0 14px; }
.topic-body p:last-child { margin-bottom: 0; }
.topic-footer {
  margin-top: 20px; padding-top: 12px; border-top: 1px solid var(--rule);
  font-size: 12px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace;
}
.topic-footer a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent-soft);
}
.topic-footer a:hover { border-bottom-style: solid; }

/* ── Backlinks "Referenced from" section ───────────────────────────── */
.backlinks-section {
  margin-top: 24px; padding-top: 14px; border-top: 1px solid var(--rule);
}
.backlinks-heading {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin: 0 0 10px;
  font-family: ui-monospace, Consolas, monospace; font-weight: 500;
}
.backlink-group {
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 4px 8px; margin-bottom: 6px; font-size: 12px;
}
.backlink-scope {
  color: var(--muted); font-family: ui-monospace, Consolas, monospace;
  min-width: 76px; flex-shrink: 0;
}
.backlink-list { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.backlink-item {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent-soft);
}
.backlink-item:hover { border-bottom-style: solid; }

/* ── Per-project arc page ───────────────────────────────────────────── */
.arc-page {
  max-width: 720px; margin: 20px auto; padding: 22px 28px;
  background: var(--paper); border: 1px solid var(--rule);
  border-left: 4px solid var(--accent);
  border-radius: 4px; box-shadow: var(--shadow);
  font-size: 15px; line-height: 1.7;
}
.arc-page h2 { margin: 0 0 6px; font-size: 22px; font-weight: 500; }
.arc-tag-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin-bottom: 14px;
  font-family: ui-monospace, Consolas, monospace;
}
.arc-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 16px;
}
.arc-meta code {
  background: var(--chip); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; margin-right: 2px;
}
.arc-body { margin: 0 0 18px; }
.arc-body p { margin: 0 0 14px; }
.arc-body p:last-child { margin-bottom: 0; }
.arc-footer {
  margin-top: 20px; padding-top: 12px; border-top: 1px solid var(--rule);
  font-size: 12px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace;
}
.arc-footer a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent-soft);
}
.arc-footer a:hover { border-bottom-style: solid; }

/* Day-has-docs indicator — small chip in the day header. */
.day-docs {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11.5px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace;
  margin-left: 10px;
}
.day-docs a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent-soft);
}
.day-docs a:hover { border-bottom-style: solid; }

/* Empty day */
.day-activity-only {
  color: var(--muted); font-size: 14px; font-style: italic;
  padding: 8px 0; margin-bottom: 8px;
}
.interlude {
  max-width: 540px; margin: 10px auto; padding: 18px 24px;
  background: var(--paper); border: 1px solid var(--rule);
  border-radius: 4px; text-align: center;
  font-style: italic; color: #4a3a2d; font-size: 15.5px; line-height: 1.7;
  box-shadow: var(--shadow);
}
.interlude .tag {
  display: block; font-style: normal; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin-bottom: 10px;
  font-family: ui-monospace, Consolas, monospace;
}
.interlude pre {
  font-family: ui-monospace, Consolas, monospace; font-style: normal;
  font-size: 13.5px; line-height: 1.4; margin: 0; white-space: pre;
  text-align: left; display: inline-block;
}
.interlude p { margin: 0 0 10px; } .interlude p:last-child { margin: 0; }

/* Project nav breadcrumb */
.crumb {
  text-align: center; color: var(--muted); font-size: 13px;
  margin-bottom: 10px; font-family: ui-monospace, Consolas, monospace;
}
.crumb a { color: var(--accent); text-decoration: none; }
.crumb a:hover { border-bottom: 1px dotted var(--accent); }

/* Library pill — paired with Ask in a flex wrapper, top-left. Both live
   in the same action cluster so "things I can do with the journal" reads
   as one affordance, with filled (Ask) vs outlined (Library) signaling
   primary vs secondary. The wrapper handles positioning so the two pills
   flow past each other when Ask expands on hover. */
#library-fab {
  position: static; z-index: 40;
  height: 34px; padding: 0 16px;
  border-radius: 17px; background: var(--paper); color: var(--accent);
  border: 1px solid var(--accent-soft); cursor: pointer;
  box-shadow: 0 2px 8px rgba(90, 50, 20, 0.12);
  font: 500 13px/1 ui-sans-serif, system-ui, -apple-system, Helvetica, sans-serif;
  letter-spacing: 0.02em;
  display: inline-flex; align-items: center; gap: 7px;
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.18s ease, padding 0.22s ease;
}
#library-fab:hover {
  background: var(--accent-soft); color: var(--paper);
  border-color: var(--accent-soft);
  box-shadow: 0 4px 12px rgba(90, 50, 20, 0.22);
  padding: 0 20px;
}
#library-fab .lib-icon { font-size: 13px; line-height: 1; opacity: 0.9; }
#library-fab .hint {
  font-weight: 400; font-size: 11.5px; opacity: 0;
  max-width: 0; overflow: hidden; white-space: nowrap;
  transition: max-width 0.25s ease, opacity 0.2s ease 0.05s, margin-left 0.25s ease;
}
#library-fab:hover .hint { opacity: 0.85; max-width: 120px; margin-left: 4px; }

/* Library modal — shares structural pattern with chat-modal but its own
   namespace so the two can coexist. */
#library-modal {
  position: fixed; inset: 0; background: rgba(250, 246, 236, 0.75);
  backdrop-filter: blur(2px); -webkit-backdrop-filter: blur(2px);
  display: none; align-items: center; justify-content: center;
  z-index: 50; padding: 24px;
}
#library-modal.open { display: flex; }
#library-panel {
  width: 100%; max-width: 720px; max-height: 84vh;
  background: var(--paper); border-radius: 10px;
  /* Layered shadow: a tight close-to-edge shadow for definition, plus a
     larger soft one for depth. Against a paper-toned veil backdrop this
     gives the panel clear lift without darkening the page. */
  box-shadow:
    0 1px 3px rgba(70, 50, 20, 0.08),
    0 10px 30px rgba(70, 50, 20, 0.18),
    0 24px 60px rgba(70, 50, 20, 0.14);
  border: 1px solid var(--rule);
  display: flex; flex-direction: column; overflow: hidden;
}
#library-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 20px; border-bottom: 1px solid var(--rule);
  /* Subtle tone shift so the header has weight without shouting — same
     shade the page background uses, so it reads as a "band" not a panel. */
  background: var(--bg);
}
#library-head h3 {
  margin: 0; font-size: 16px; font-weight: 500;
  color: var(--fg); letter-spacing: 0.01em;
}
#library-close {
  background: none; border: none; color: var(--muted); font-size: 20px;
  cursor: pointer; line-height: 1;
}
#library-close:hover { color: var(--fg); }
#library-body {
  flex: 1; overflow-y: auto; padding: 16px 20px;
  display: flex; flex-direction: column; gap: 20px;
}
.lib-section h4 {
  margin: 0 0 8px; font-size: 13px; font-weight: 600;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em;
}
.lib-add-form {
  display: flex; flex-direction: column; gap: 10px;
  padding: 14px; background: var(--paper); border: 1px solid var(--rule);
  border-radius: 8px;
}
.lib-add-form label {
  display: flex; flex-direction: column; gap: 4px;
  font-size: 12px; color: var(--muted);
}
.lib-add-form input[type="text"],
.lib-add-form textarea,
.lib-add-form select {
  font: 14px ui-sans-serif, system-ui, -apple-system, sans-serif;
  padding: 7px 10px; border: 1px solid var(--rule); border-radius: 4px;
  background: var(--paper); color: var(--fg);
}
.lib-add-form textarea {
  resize: vertical; min-height: 56px; font-family: inherit;
}
.lib-add-form select[multiple] { min-height: 88px; }

/* Project picker — searchable chip list. Matches the filter-longpool
   pattern from the main filter bar so the UX idiom is consistent. */
.lib-project-search {
  width: 100%; padding: 6px 10px; font-size: 13px;
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--paper); color: var(--fg);
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
}
.lib-project-search:focus { outline: none; border-color: var(--accent-soft); }
.lib-project-list {
  display: flex; flex-wrap: wrap; gap: 5px;
  max-height: 140px; overflow-y: auto;
  padding: 8px; margin-top: 6px;
  /* Slightly darker than the chips themselves so each chip reads as a
     raised outlined pill — same trick the top-page filter row uses. */
  background: var(--bg); border: 1px solid var(--rule); border-radius: 4px;
}
/* Project chips mirror the main filter-chip idiom exactly so the UX
   language is consistent: outlined at rest, accent-bordered on hover,
   fully accent-filled only when selected. Uses <button> for accessibility
   (it's a toggle control, not a link), which means we have to override
   the browser's default button chrome explicitly. */
.lib-project-chip {
  appearance: none; -webkit-appearance: none;
  display: inline-block; padding: 3px 12px; font-size: 12px; line-height: 1.4;
  border: 1px solid var(--rule); border-radius: 14px; cursor: pointer;
  background: var(--paper); color: var(--muted); text-decoration: none;
  font-family: ui-monospace, Consolas, monospace;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}
.lib-project-chip:hover { border-color: var(--accent-soft); color: var(--accent); }
.lib-project-chip.selected {
  background: var(--accent); color: var(--paper); border-color: var(--accent);
}
.lib-project-chip.selected:hover { background: #6f3916; }
.lib-project-chip.selected::after { content: " ×"; opacity: 0.8; }
.lib-project-empty {
  color: var(--muted); font-style: italic; font-size: 12px;
  padding: 8px; width: 100%; text-align: center;
}
.lib-project-count { color: var(--muted); font-size: 11px; margin-top: 4px; }
.lib-drop-zone {
  border: 2px dashed var(--rule); border-radius: 6px; padding: 18px;
  text-align: center; color: var(--muted); font-size: 13px;
  background: var(--paper); cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.lib-drop-zone.drag-over {
  border-color: var(--accent); background: var(--accent-soft); color: var(--paper);
}
.lib-drop-zone .lib-file-name {
  display: block; margin-top: 6px; color: var(--fg); font-weight: 500;
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
}
.lib-add-form .lib-actions { display: flex; justify-content: flex-end; gap: 8px; }
/* Scope the action-button styles to .lib-actions explicitly — an un-scoped
   `.lib-add-form button` rule was matching the project chips (which are
   <button>s nested inside the form) and painting them with the Add
   button's accent-fill. Chips keep their own .lib-project-chip styling. */
.lib-add-form .lib-actions button {
  font: 13px ui-sans-serif, system-ui, -apple-system, sans-serif;
  padding: 7px 16px; border-radius: 4px; cursor: pointer;
  border: 1px solid var(--accent-soft); background: var(--accent-soft); color: var(--paper);
  transition: background 0.15s ease, border-color 0.15s ease;
}
.lib-add-form .lib-actions button:hover { background: var(--accent); border-color: var(--accent); }
.lib-add-form .lib-actions button[type="reset"] {
  background: var(--paper); color: var(--fg); border-color: var(--rule);
}
.lib-add-form .lib-actions button[type="reset"]:hover { background: var(--paper); border-color: var(--accent-soft); color: var(--accent); }
.lib-add-form .lib-actions button:disabled { opacity: 0.5; cursor: wait; }
.lib-status {
  font-size: 12px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace;
  min-height: 16px;
}
.lib-status.lib-error { color: #a03020; }
.lib-status.lib-ok { color: var(--ok, #3d7a3d); }
.lib-list { display: flex; flex-direction: column; gap: 6px; }
.lib-row {
  display: grid; grid-template-columns: 84px 1fr auto;
  gap: 12px; align-items: start;
  padding: 10px 12px; background: var(--paper);
  border: 1px solid var(--rule); border-radius: 6px;
  font-size: 13px;
}
.lib-row .lib-date {
  color: var(--muted); font-family: ui-monospace, Consolas, monospace;
  font-size: 12px; padding-top: 2px;
}
.lib-row .lib-meta { color: var(--muted); font-size: 11.5px; margin-top: 2px; }
.lib-row .lib-title { color: var(--fg); font-weight: 500; }
.lib-row .lib-actions-cell { display: flex; gap: 6px; }
.lib-row .lib-edit,
.lib-row .lib-remove {
  background: none; border: 1px solid var(--rule); border-radius: 4px;
  color: var(--muted); cursor: pointer; padding: 3px 10px; font-size: 12px;
}
.lib-row .lib-edit:hover { border-color: var(--accent-soft); color: var(--accent); }
.lib-row .lib-remove:hover { border-color: #a03020; color: #a03020; }

/* Edit-mode affordances on the add form. Header picks up a tint so the
   mode switch is obvious without a full panel restyle. Cancel button
   only appears during edit. */
.lib-add-form.editing { border-color: var(--accent-soft); }
.lib-form-title {
  font-size: 13px; font-weight: 500; color: var(--accent);
  margin: -2px 0 4px;
}
.lib-add-form:not(.editing) .lib-form-title { display: none; }
.lib-add-form:not(.editing) #lib-cancel-edit { display: none; }
.lib-add-form.editing .lib-drop-zone { display: none; }
.lib-empty {
  color: var(--muted); font-style: italic; font-size: 13px;
  text-align: center; padding: 20px 0;
}

/* Top-left action cluster — Ask (primary, filled) paired with Library
   (secondary, outlined). Wrapper is the one fixed-position element so
   the two pills flow naturally when Ask widens on hover. */
#top-actions {
  position: fixed; top: 18px; left: 18px; z-index: 40;
  display: flex; gap: 10px; align-items: center;
}

/* Ask pill — primary action, always reachable. */
#chat-fab {
  position: static; z-index: 40;
  height: 34px; padding: 0 18px;
  border-radius: 17px; background: var(--accent); color: var(--paper);
  border: 1px solid var(--accent); cursor: pointer;
  box-shadow: 0 2px 10px rgba(90, 50, 20, 0.22);
  font: 700 13px/1 ui-sans-serif, system-ui, -apple-system, Helvetica, sans-serif;
  letter-spacing: 0.02em;
  display: inline-flex; align-items: center; gap: 7px;
  transition: background 0.15s ease, box-shadow 0.18s ease, padding 0.22s ease;
}
#chat-fab:hover {
  background: #6f3916;
  box-shadow: 0 4px 14px rgba(90, 50, 20, 0.32);
  padding: 0 22px;
}
#chat-fab .spark {
  font-size: 11px; line-height: 1;
  transform: translateY(-0.5px);
  opacity: 0.95;
}
#chat-fab .label { font-weight: 700; }
#chat-fab .hint {
  font-weight: 400; font-size: 11.5px; opacity: 0;
  max-width: 0; overflow: hidden; white-space: nowrap;
  transition: max-width 0.25s ease, opacity 0.2s ease 0.05s, margin-left 0.25s ease;
}
#chat-fab:hover .hint {
  opacity: 0.85; max-width: 160px; margin-left: 4px;
}
#chat-modal {
  position: fixed; inset: 0; background: rgba(30, 20, 10, 0.35);
  display: none; align-items: flex-end; justify-content: center;
  z-index: 50; padding: 24px;
}
#chat-modal.open { display: flex; }
#chat-panel {
  width: 100%; max-width: 680px; max-height: 80vh;
  background: var(--paper); border-radius: 10px; box-shadow: var(--shadow);
  display: flex; flex-direction: column; overflow: hidden;
}
#chat-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 20px; border-bottom: 1px solid var(--rule);
}
#chat-head h3 { margin: 0; font-size: 16px; font-weight: 500; }
#chat-close {
  background: none; border: none; color: var(--muted); font-size: 20px;
  cursor: pointer; line-height: 1;
}
#chat-close:hover { color: var(--fg); }
#chat-log {
  flex: 1; overflow-y: auto; padding: 16px 20px;
  display: flex; flex-direction: column; gap: 14px;
}
#chat-log:empty::before {
  content: "Ask the journal anything — ‘what was the Shannon framing?’, ‘when did I fix libomp?’, ‘what did I learn this month?’";
  color: var(--muted); font-style: italic; font-size: 14px;
}
.q-row { color: var(--muted); font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
.a-row {
  background: var(--bg); border-left: 3px solid var(--accent);
  padding: 12px 16px; border-radius: 4px;
  font-size: 15px; line-height: 1.6;
}
.a-row p { margin: 0 0 10px; } .a-row p:last-child { margin: 0; }
.a-row a.anchor {
  color: var(--accent); font-size: 0.82em;
  border-bottom: 1px dotted var(--accent); text-decoration: none;
  padding: 0 2px; white-space: nowrap;
}
.a-row .sources {
  color: var(--muted); font-family: ui-monospace, Consolas, monospace;
  font-size: 11px; margin-top: 10px; padding-top: 8px;
  border-top: 1px dashed var(--rule);
}
.loading { color: var(--muted); font-style: italic; }
#chat-form {
  display: flex; gap: 8px; padding: 14px 20px;
  border-top: 1px solid var(--rule); background: var(--bg);
}
#chat-input {
  flex: 1; font: inherit; padding: 10px 14px;
  border: 1px solid var(--rule); border-radius: 6px; background: var(--paper);
}
#chat-form button {
  font: inherit; padding: 10px 18px; border: 1px solid var(--accent);
  background: var(--accent); color: var(--paper); border-radius: 6px; cursor: pointer;
}
#chat-form button:disabled { opacity: 0.5; cursor: wait; }

footer {
  color: var(--muted); font-size: 12px; margin-top: 64px;
  padding-top: 12px; border-top: 1px solid var(--rule); text-align: center;
  font-family: ui-monospace, Consolas, monospace;
}

/* ── Open loops standalone page (out/loops.html) ───────────────────── */
.loops-page {
  max-width: 720px; margin: 20px auto; padding: 0 0 40px;
}
.loops-page h2 { margin: 0 0 6px; font-size: 22px; font-weight: 500; }
.loops-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 24px;
}
.loops-project-group { margin-bottom: 32px; }
.loops-project-heading {
  font-size: 13px; font-weight: 600; color: var(--fg);
  font-family: ui-monospace, Consolas, monospace;
  margin: 0 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--rule);
}
.loop-item {
  margin: 0 0 14px; padding: 10px 14px;
  border: 1px solid var(--rule); border-left: 3px solid var(--warn);
  border-radius: 4px; background: var(--paper); font-size: 14px;
  line-height: 1.55;
}
.loop-text { margin: 0 0 6px; color: var(--fg); }
.loop-footer {
  color: var(--muted); font-size: 11px;
  font-family: ui-monospace, Consolas, monospace;
  display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center;
}
.loop-age { color: var(--warn); font-weight: 500; }
.loop-tags code {
  background: var(--chip); padding: 1px 6px; border-radius: 9px;
  font-size: 10px; margin-right: 2px;
}
/* "Mark resolved" button on loop items (Phase E7) */
.loop-resolve-btn {
  font-size: 11px; font-family: inherit; cursor: pointer;
  padding: 2px 8px; border-radius: 8px;
  border: 1px solid var(--ok); background: transparent; color: var(--ok);
  margin-left: auto;
  transition: background 0.15s, color 0.15s;
}
.loop-resolve-btn:hover { background: var(--ok); color: var(--paper); }
.loop-resolve-btn:disabled { opacity: 0.5; cursor: default; }
.loops-empty {
  color: var(--muted); font-style: italic; text-align: center;
  padding: 40px 0; font-size: 15px;
}
/* Loops chip panel: hybrid surface listing this day's loops inline plus
   a "view all" link. Items are tight rows with date · project · age and
   the friction text on a second line. */
.loop-list { list-style: none; padding: 0; margin: 0 0 8px 0; }
.loop-row {
  margin: 0 0 8px 0; padding: 6px 8px;
  border-left: 2px solid var(--warn);
  background: var(--paper);
}
.loop-meta {
  font-size: 11px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 2px;
}
.loop-meta .loop-date { color: var(--fg); }
.loop-meta .loop-proj { font-weight: 600; }
.loop-meta .loop-age { color: var(--warn); margin-left: 4px; }
.loop-text { font-size: 13px; line-height: 1.45; color: var(--fg); }
.loop-more-note {
  margin: 0; font-size: 12px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace; text-align: right;
}
.loop-more-note a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px dotted var(--accent);
}
.loop-more-note a:hover { border-bottom-style: solid; }

/* ── Learnings standalone page (out/learnings.html) ─────────────────── */
.learnings-page {
  max-width: 720px; margin: 20px auto; padding: 0 0 40px;
}
.learnings-page h2 { margin: 0 0 6px; font-size: 22px; font-weight: 500; }
.learnings-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 14px;
}
.learnings-search {
  /* Sticky filter input mirrors the inline inspect-search look but
     widens for the standalone-page context. */
  margin-bottom: 18px;
}
.learnings-tag-group { margin-bottom: 32px; }
.learnings-tag-heading {
  font-size: 13px; font-weight: 600; color: var(--fg);
  font-family: ui-monospace, Consolas, monospace;
  margin: 0 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--rule);
}
.learning-item {
  margin: 0 0 12px; padding: 10px 14px;
  border: 1px solid var(--rule); border-left: 3px solid var(--ok);
  border-radius: 4px; background: var(--paper); font-size: 14px;
  line-height: 1.55;
}
.learning-text { margin: 0 0 6px; color: var(--fg); }
.learning-footer {
  color: var(--muted); font-size: 11px;
  font-family: ui-monospace, Consolas, monospace;
  display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center;
}
.learning-count { color: var(--ok); font-weight: 500; }
.learnings-empty {
  color: var(--muted); font-style: italic; text-align: center;
  padding: 40px 0; font-size: 15px;
}
/* Entity chips in the inspect panel */
.entity-group { margin: 6px 0; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
/* When the search filter hides every entity in a group, collapse the
   group label too so the panel doesn't show a row of category headers
   pointing at nothing. Uses :has() (broadly supported in modern
   browsers); older engines fall back to showing the empty group, which
   is harmless. */
.entity-group:not(:has(.entity-chip:not(.hidden-by-filter))) { display: none; }
.entity-group-label {
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
  min-width: 70px;
}
.entity-group-items { display: flex; flex-wrap: wrap; gap: 4px; }
.entity-chip {
  display: inline-block;
  font-size: 0.72rem;
  padding: 1px 7px;
  border-radius: 10px;
  text-decoration: none;
  border: 1px solid currentColor;
  transition: opacity 0.15s;
}
.entity-chip:hover { opacity: 0.75; }
.entity-person    { color: var(--accent); }
.entity-ai_model  { color: var(--ok, #4caf77); }
.entity-library   { color: var(--warn, #d4a017); }
.entity-service   { color: var(--muted); }

/* ── Temporal echoes panel (inside the "memory" inspect chip) ─────────── */
.echo-banner {
  margin: 0;
  font-size: 12px; color: var(--muted);
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center;
}
.echo-item { display: inline; }
.echo-item a { color: var(--muted); text-decoration: none; border-bottom: 1px dotted var(--muted); }
.echo-item a:hover { color: var(--accent); border-bottom-color: var(--accent); }
.echo-sep { color: var(--rule); }

/* ── Temporal echoes standalone page (out/echoes.html) ───────────────── */
.echoes-page {
  max-width: 720px; margin: 20px auto; padding: 0 0 40px;
}
.echoes-page h2 { margin: 0 0 6px; font-size: 22px; font-weight: 500; }
.echoes-meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace;
  margin-bottom: 24px;
}
.echoes-section { margin-bottom: 36px; }
.echoes-section-heading {
  font-size: 13px; font-weight: 600; color: var(--fg);
  font-family: ui-monospace, Consolas, monospace;
  margin: 0 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--rule);
}
.echo-card {
  margin: 0 0 12px; padding: 10px 14px;
  border: 1px solid var(--rule); border-left: 3px solid var(--accent-soft);
  border-radius: 4px; background: var(--paper); font-size: 14px;
  line-height: 1.55;
}
.echo-card-title {
  font-weight: 500; color: var(--fg); margin-bottom: 4px;
}
.echo-card-title a { color: var(--accent); text-decoration: none; }
.echo-card-title a:hover { text-decoration: underline; }
.echo-card-body {
  color: var(--muted); font-size: 13px;
  font-style: italic; margin-bottom: 4px;
}
.echo-card-footer {
  color: var(--muted); font-size: 11px;
  font-family: ui-monospace, Consolas, monospace;
  display: flex; flex-wrap: wrap; gap: 4px 12px;
}
.echo-friction-card {
  border-left-color: var(--warn);
}
.echo-milestone-card {
  border-left-color: var(--ok);
}
.echoes-empty {
  color: var(--muted); font-style: italic; text-align: center;
  padding: 40px 0; font-size: 15px;
}

/* ── Annotations (Phase E) ───────────────────────────────────────────── */
/* Annotate button — lives next to the inspect chips */
.annotate-btn {
  font-size: 11px; font-family: inherit; cursor: pointer;
  padding: 2px 8px; border-radius: 10px;
  border: 1px solid var(--rule); background: transparent;
  color: var(--muted);
  transition: border-color 0.15s, color 0.15s;
  white-space: nowrap;
}
.annotate-btn:hover { border-color: var(--accent-soft); color: var(--accent); }
/* Annotation form panel */
.annotation-form {
  margin: 10px 0 6px; padding: 12px 14px;
  border: 1px solid var(--rule); border-radius: 6px;
  background: var(--chip); display: none;
}
.annotation-form.open { display: block; }
.annotation-form textarea {
  width: 100%; box-sizing: border-box;
  min-height: 72px; padding: 8px 10px;
  font-family: inherit; font-size: 13px; line-height: 1.5;
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--paper); color: var(--fg); resize: vertical;
}
.annotation-form textarea:focus { outline: none; border-color: var(--accent-soft); }
.annotation-form-row {
  display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap;
}
.annotation-form select {
  font-family: inherit; font-size: 12px; padding: 4px 6px;
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--paper); color: var(--fg); cursor: pointer;
}
.annotation-save-btn {
  font-size: 12px; font-family: inherit; cursor: pointer;
  padding: 4px 12px; border-radius: 6px;
  border: 1px solid var(--accent); background: var(--accent); color: var(--paper);
}
.annotation-save-btn:hover { background: var(--accent-soft); border-color: var(--accent-soft); }
.annotation-cancel-btn {
  font-size: 12px; font-family: inherit; cursor: pointer;
  padding: 4px 10px; border-radius: 6px;
  border: 1px solid var(--rule); background: transparent; color: var(--muted);
}
.annotation-cancel-btn:hover { color: var(--fg); }
.annotation-form-msg { font-size: 11px; color: var(--muted); margin-left: auto; }
/* Rendered annotation blocks (below AI prose) */
.annotations-block { margin: 14px 0 6px; display: flex; flex-direction: column; gap: 8px; }
.annotation-item {
  padding: 9px 13px;
  border-left: 3px solid var(--rule); border-radius: 0 4px 4px 0;
  background: var(--chip); font-size: 13px; line-height: 1.5;
  position: relative;
}
.annotation-item.type-append  { border-left-color: var(--ok); }
.annotation-item.type-correction { border-left-color: var(--warn); }
.annotation-item.type-override { border-left-color: var(--accent); }
.annotation-item-label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  font-weight: 600; color: var(--muted); margin-bottom: 4px;
}
.annotation-item.type-append    .annotation-item-label { color: var(--ok); }
.annotation-item.type-correction .annotation-item-label { color: var(--warn); }
.annotation-item.type-override  .annotation-item-label { color: var(--accent); }
.annotation-item-text { color: var(--fg); white-space: pre-wrap; word-break: break-word; }
.annotation-contradiction-warn {
  display: inline-block; font-size: 10px;
  color: var(--warn); margin-left: 6px; vertical-align: middle;
  cursor: help;
}
.annotation-delete-btn {
  position: absolute; top: 7px; right: 8px;
  font-size: 10px; font-family: inherit; cursor: pointer;
  padding: 2px 6px; border-radius: 4px;
  border: 1px solid transparent; background: transparent;
  color: var(--muted); opacity: 0;
  transition: opacity 0.15s;
}
.annotation-item:hover .annotation-delete-btn { opacity: 1; }
.annotation-delete-btn:hover { border-color: var(--warn); color: var(--warn); }
"""


INSPECT_WIDGET = """
<script>
(function() {
  document.addEventListener('click', e => {
    const chip = e.target.closest('.inspect-chip');
    if (!chip) return;
    e.preventDefault();
    const panelId = chip.dataset.panel;
    const panel = document.getElementById(panelId);
    if (!panel) return;
    const open = !panel.hasAttribute('hidden');
    // Mutual exclusion within the same entry — close any sibling inspect
    // panels/chips so only one pane is visible at a time per day.
    const entry = chip.closest('article.entry') || document;
    entry.querySelectorAll('.inspect-chip.open').forEach(c => {
      if (c !== chip) c.classList.remove('open');
    });
    entry.querySelectorAll('.inspect-panel').forEach(p => {
      if (p !== panel) p.setAttribute('hidden', '');
    });
    if (open) { panel.setAttribute('hidden', ''); chip.classList.remove('open'); }
    else      { panel.removeAttribute('hidden');   chip.classList.add('open'); }
  });

  // Per-panel live search with match highlighting.
  // We cache each filterable item's original innerHTML on first use, then
  // rebuild it with <mark> tags only inside text nodes so existing markup
  // (<span class="meta"> etc.) isn't broken.
  const origCache = new WeakMap();
  function escRegex(s) { return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); }
  function highlightInto(el, q) {
    if (!origCache.has(el)) origCache.set(el, el.innerHTML);
    const orig = origCache.get(el);
    if (!q) { el.innerHTML = orig; return; }
    const rx = new RegExp('(' + escRegex(q) + ')', 'gi');
    const tmp = document.createElement('div');
    tmp.innerHTML = orig;
    const walker = document.createTreeWalker(tmp, NodeFilter.SHOW_TEXT);
    const textNodes = []; let n;
    while ((n = walker.nextNode())) textNodes.push(n);
    textNodes.forEach(t => {
      if (!t.nodeValue || !rx.test(t.nodeValue)) return;
      const parts = t.nodeValue.split(new RegExp('(' + escRegex(q) + ')', 'gi'));
      const frag = document.createDocumentFragment();
      parts.forEach((p, i) => {
        if (!p) return;
        if (i % 2 === 1) {
          const m = document.createElement('mark');
          m.textContent = p;
          frag.appendChild(m);
        } else {
          frag.appendChild(document.createTextNode(p));
        }
      });
      t.parentNode.replaceChild(frag, t);
    });
    el.innerHTML = tmp.innerHTML;
  }

  document.addEventListener('input', e => {
    const input = e.target.closest('.inspect-search');
    if (!input) return;
    const content = document.getElementById(input.dataset.target);
    if (!content) return;
    const q = input.value.trim();
    const qLower = q.toLowerCase();
    let visible = 0;
    content.querySelectorAll('.filterable').forEach(el => {
      if (!origCache.has(el)) origCache.set(el, el.innerHTML);
      const hay = el.textContent.toLowerCase();
      const match = !qLower || hay.includes(qLower);
      el.classList.toggle('hidden-by-filter', !match);
      if (match) {
        visible++;
        highlightInto(el, q);
      }
    });
    const empty = content.querySelector('.inspect-empty-match');
    if (empty) {
      if (visible === 0 && q) empty.removeAttribute('hidden');
      else empty.setAttribute('hidden', '');
    }
  });
})();
</script>
"""


ANNOTATION_WIDGET = """
<script>
(function() {
  // Annotation CRUD widget — handles the inline "annotate" form on daily entries.
  // Called by render_day_entry via the annotate button in the inspect row.

  function _esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Build an annotation-item HTML string from a record
  function _annotationHtml(ann) {
    const label = {append: 'append', correction: 'correction', override: 'override'}[ann.annotation_type] || ann.annotation_type;
    const warnHtml = ann._contradiction
      ? '<span class="annotation-contradiction-warn" title="This annotation may not be reflected in the current AI prose. Re-run the pipeline to regenerate.">&#9888;</span>'
      : '';
    return (
      '<div class="annotation-item type-' + _esc(ann.annotation_type) + '" data-ann-id="' + ann.id + '">' +
        '<button class="annotation-delete-btn" data-ann-id="' + ann.id + '" title="Delete annotation">delete</button>' +
        '<div class="annotation-item-label">' + _esc(label) + warnHtml + '</div>' +
        '<div class="annotation-item-text">' + _esc(ann.text) + '</div>' +
      '</div>'
    );
  }

  // Load and render annotations for an entry
  function loadAnnotations(dateKey, scope, container) {
    fetch('/api/annotations?scope=' + encodeURIComponent(scope) + '&key=' + encodeURIComponent(dateKey))
      .then(function(r) { return r.json(); })
      .then(function(list) {
        if (!Array.isArray(list)) return;
        container.innerHTML = list.length
          ? list.map(_annotationHtml).join('')
          : '';
        // Wire delete buttons
        container.querySelectorAll('.annotation-delete-btn').forEach(function(btn) {
          btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var id = btn.dataset.annId;
            if (!confirm('Delete this annotation?')) return;
            fetch('/api/annotations/' + id, {method: 'DELETE'})
              .then(function() { loadAnnotations(dateKey, scope, container); });
          });
        });
      })
      .catch(function(err) { console.warn('annotation load failed', err); });
  }

  // Wire all annotate buttons
  document.querySelectorAll('.annotate-btn').forEach(function(btn) {
    var entryEl = btn.closest('article.entry');
    if (!entryEl) return;
    var dateKey = entryEl.id;   // entry id = YYYY-MM-DD for daily entries
    var scope   = btn.dataset.scope || 'daily';
    var formEl  = entryEl.querySelector('.annotation-form');
    var containerEl = entryEl.querySelector('.annotations-block');
    if (!formEl || !containerEl) return;

    // Initial load
    loadAnnotations(dateKey, scope, containerEl);

    // Toggle form open/close
    btn.addEventListener('click', function() {
      var isOpen = formEl.classList.toggle('open');
      btn.textContent = isOpen ? 'close' : 'annotate';
    });

    // Cancel
    var cancelBtn = formEl.querySelector('.annotation-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function() {
        formEl.classList.remove('open');
        btn.textContent = 'annotate';
        formEl.querySelector('textarea').value = '';
        var msgEl = formEl.querySelector('.annotation-form-msg');
        if (msgEl) msgEl.textContent = '';
      });
    }

    // Save
    var saveBtn = formEl.querySelector('.annotation-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        var ta  = formEl.querySelector('textarea');
        var sel = formEl.querySelector('select');
        var msg = formEl.querySelector('.annotation-form-msg');
        var text = (ta ? ta.value : '').trim();
        var ann_type = sel ? sel.value : 'append';
        if (!text) { if (msg) msg.textContent = 'Text required.'; return; }
        if (msg) msg.textContent = 'Saving…';
        fetch('/api/annotations', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({scope: scope, key: dateKey, annotation_type: ann_type, text: text}),
        })
        .then(function(r) { return r.json(); })
        .then(function(res) {
          if (res.ok) {
            if (ta) ta.value = '';
            if (msg) msg.textContent = 'Saved.';
            loadAnnotations(dateKey, scope, containerEl);
            setTimeout(function() { if (msg) msg.textContent = ''; }, 2000);
          } else {
            if (msg) msg.textContent = 'Error: ' + (res.error || 'unknown');
          }
        })
        .catch(function(err) { if (msg) msg.textContent = 'Network error.'; console.warn(err); });
      });
    }
  });
})();
</script>
"""


FILTER_WIDGET = """
<script>
(function() {
  const data = window.__FILTERS__ || {projects: [], weeks: [], months: [], moods: [], learnings: [], years: [], tags: [], topic_pages: [], arc_pages: [], entities: []};
  if (!data.projects.length && !data.weeks.length && !data.months.length
      && !data.moods.length && !data.learnings.length && !data.years.length
      && !(data.tags || []).length && !(data.entities || []).length) return;
  // `axis` + `value` drive the per-facet filter row (Project, Topic, …, Find).
  // `views` is a Set of entry types currently shown. Row 0 chips toggle
  // membership — multi-select, not mutually exclusive. Default is
  // {daily, weekly}: day entries read as the timeline, weekly rollups give
  // natural rhythm breaks. Monthly is kept off by default (archival, would
  // otherwise clutter casual scrolling). Persisted to localStorage so the
  // preference survives reloads.
  const VIEWS_KEY = 'cj.views';
  const DEFAULT_VIEWS = ['daily', 'weekly'];
  function loadViews() {
    try {
      const raw = localStorage.getItem(VIEWS_KEY);
      // Never stored = first visit: apply the curated default.
      if (raw === null) return new Set(DEFAULT_VIEWS);
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return new Set(DEFAULT_VIEWS);
      // Explicit empty set is a valid state (= show all) — preserve it.
      return new Set(arr.filter(x => ['daily','weekly','monthly'].includes(x)));
    } catch (e) { return new Set(DEFAULT_VIEWS); }
  }
  function saveViews(views) {
    try { localStorage.setItem(VIEWS_KEY, JSON.stringify([...views])); } catch (e) {}
  }
  // `mode` is null | 'overview' | 'timeline'.
  // Only project and topic axes use it; all others go straight to values.
  const AXES_WITH_MODE = new Set(['project', 'topic']);
  const state = {axis: null, value: null, mode: null, views: loadViews()};
  const modesRow = document.getElementById('filter-modes');
  const axesRow = document.getElementById('filter-axes');
  const options = document.getElementById('filter-options');
  if (!axesRow || !options) return;
  const feed = document.getElementById('feed');
  const empty = document.getElementById('filter-empty');

  function makeChip(label, cls, onClick) {
    const a = document.createElement('a');
    a.className = 'filter-chip' + (cls ? ' ' + cls : '');
    a.textContent = label;
    a.href = '#';
    a.addEventListener('click', e => { e.preventDefault(); onClick(); });
    return a;
  }
  const AXIS_LABELS = {project: 'Project', topic: 'Topic', year: 'Year', month: 'Month', week: 'Week', mood: 'Mood', learning: 'Aha moment', entity: 'Tools', search: 'Find'};
  // Entry-type filter. 'all' shows everything; the others hide the two
  // element kinds that don't match (dailies are <article.entry>, weeklies
  // live in .week-rollup-wrap, monthlies in .month-rollup-wrap).
  const VIEW_KEYS = ['daily', 'weekly', 'monthly', 'library'];
  const VIEW_LABELS = {daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly', library: 'Library'};
  const AXIS_KEYS = ['project', 'topic', 'year', 'month', 'week', 'mood', 'learning', 'entity'];

  const poolFor = (axis) => {
    if (axis === 'project') return data.projects;
    if (axis === 'topic') return data.tags || [];
    if (axis === 'learning') return data.learnings || [];
    if (axis === 'entity') return data.entities || [];
    if (axis === 'search') return ['__search__'];  // pseudo — chip appears
    return data[axis + 's'] || [];
  };

  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  const navRow = document.getElementById('filter-nav');
  function render() {
    if (modesRow) clearChildren(modesRow);
    if (navRow) clearChildren(navRow);
    clearChildren(axesRow);
    clearChildren(options);

    // --- Row 0: "mode" chips. Find (search) sits here alongside the
    // entry-type toggle (Daily/Weekly/Monthly). Selecting a view chip is
    // exclusive — clicking the active one reverts to 'all'. ---
    if (modesRow) {
      const anchorBase = window.__ANCHOR_BASE__ || './';
      // "Home" specifically means the main feed (index.html at site root).
      // Standalone pages at site root (loops.html / graph.html /
      // learnings.html / echoes.html) share anchor_base='./' but aren't
      // home — they're nav destinations. Treat them as deep-link pages
      // for Clear-chip purposes so the user always has a way back.
      const _path = (window.location.pathname || '').toLowerCase();
      const _atRoot = (anchorBase === './' || anchorBase === '');
      const _isIndex = _path === '/' || _path.endsWith('/index.html') || _path === '';
      const onHomePage = _atRoot && _isIndex;
      const filtered = !!state.axis || state.views.size > 0;
      // Clear chip — sits at the end of the modes row. On deep-link pages
      // it's always present and navigates back to the main feed. On the
      // main feed itself it's only present when something is filtered;
      // clicking it resets all filters in place. Pristine main feed = no
      // chip (no-op).
      const showClear = !onHomePage || filtered;
      const findActive = state.axis === 'search';
      modesRow.appendChild(makeChip(AXIS_LABELS.search, 'mode axis-search' + (findActive ? ' active' : ''), () => {
        if (state.axis === 'search') { state.axis = null; state.value = null; state.mode = null; }
        else { state.axis = 'search'; state.value = null; state.mode = null; }
        apply();
      }));
      VIEW_KEYS.forEach(v => {
        const isActive = state.views.has(v);
        modesRow.appendChild(makeChip(VIEW_LABELS[v], 'mode view-' + v + (isActive ? ' active' : ''), () => {
          // Toggle this view in/out. An empty set is treated as "show all"
          // by the visibility helper below, so deselecting every chip
          // reads the same as selecting every chip — intuitive on click.
          if (isActive) state.views.delete(v);
          else state.views.add(v);
          saveViews(state.views);
          apply();
        }));
      });
      // Clear chip stays trailing on the filter row (Row 0a). On deep-link
      // pages (including the standalone nav targets) it always shows; on
      // the main feed it shows only when filters are active.
      if (showClear) {
        modesRow.appendChild(makeChip('Clear', 'home', () => {
          if (onHomePage) {
            state.axis = null; state.value = null; state.mode = null;
            state.views.clear();
            saveViews(state.views);
            apply();
          } else {
            window.location.href = anchorBase + 'index.html';
          }
        }));
      }
    }
    // Navigation chips (Graph / Loops / Learnings / Echoes) live on their
    // own row (Row 0b) so they read as destinations, not filter state.
    // Each navigates to a standalone page; on those pages the chip is
    // marked .active for visual confirmation.
    if (navRow) {
      const anchorBaseN = window.__ANCHOR_BASE__ || './';
      const navTargets = [
        ['Graph', 'graph', 'graph.html'],
        ['Loops', 'loops', 'loops.html'],
        ['Learnings', 'learnings', 'learnings.html'],
        ['Echoes', 'echoes', 'echoes.html'],
      ];
      const currentPath = (window.location.pathname || '').toLowerCase();
      navTargets.forEach(([label, key, href]) => {
        const isActive = currentPath.endsWith('/' + href);
        navRow.appendChild(makeChip(label, 'mode mode-' + key + (isActive ? ' active' : ''), () => {
          // Toggle semantics: clicking the active nav chip on its own
          // destination page navigates back to the main feed, so users
          // can deselect it the same way they would any filter chip.
          if (isActive) {
            window.location.href = anchorBaseN + 'index.html';
          } else {
            window.location.href = anchorBaseN + href;
          }
        }));
      });
    }

    // --- Axis row: always present, stable positions. Click toggles active. ---
    AXIS_KEYS.forEach(k => {
      const pool = poolFor(k);
      if (!pool.length) return;
      const isActive = state.axis === k;
      const cls = 'axis axis-' + k + (isActive ? ' active' : '');
      const chip = makeChip(AXIS_LABELS[k], cls, () => {
        if (state.axis === k) { state.axis = null; state.value = null; state.mode = null; }
        else { state.axis = k; state.value = null; state.mode = null; }
        apply();
      });
      axesRow.appendChild(chip);
    });

    // --- Sub row: depends on state ---
    if (!state.axis) return;

    if (state.axis === 'search') {
      const inp = document.createElement('input');
      inp.type = 'search';
      inp.className = 'filter-search';
      inp.placeholder = 'find in the journal...';
      inp.value = state.value || '';
      inp.addEventListener('input', () => {
        state.value = inp.value;
        applyVisibility();
        syncUrl();
      });
      options.appendChild(inp);
      setTimeout(() => inp.focus(), 60);
      const hint = document.createElement('span');
      hint.className = 'filter-search-hint';
      hint.id = 'filter-search-hint';
      options.appendChild(hint);
      return;
    }

    // For project/topic axes: show mode chips (Overview/Timeline) before values.
    // Mode must be selected before the value list appears. Once mode is set,
    // fall through to the normal value-rendering path below.
    if (AXES_WITH_MODE.has(state.axis) && !state.mode && !state.value) {
      // Show only the two mode chips — no values yet.
      options.appendChild(makeChip('Overview', 'mode' + (state.mode === 'overview' ? ' active' : ''), () => {
        state.mode = 'overview'; state.value = null; apply();
      }));
      options.appendChild(makeChip('Timeline', 'mode' + (state.mode === 'timeline' ? ' active' : ''), () => {
        state.mode = 'timeline'; state.value = null; apply();
      }));
      return;
    }

    // If mode is set for project/topic, show the active mode chip as a
    // "heading" (clickable to clear mode) then the value list or selected value.
    if (AXES_WITH_MODE.has(state.axis) && state.mode && !state.value) {
      options.appendChild(makeChip(state.mode === 'overview' ? 'Overview' : 'Timeline',
        'mode active', () => { state.mode = null; state.value = null; apply(); }));
    }

    if (state.value) {
      // Show the selected value as a single active sub-chip (click to clear).
      let label = state.value;
      const lookup = {week: data.weeks, month: data.months, mood: data.moods, learning: data.learnings, year: data.years, topic: data.tags, entity: data.entities};
      const pool = lookup[state.axis];
      if (pool) {
        const hit = pool.find(x => x.key === state.value);
        if (hit) label = hit.label;
      }
      options.appendChild(makeChip(label, 'active', () => {
        state.value = null; apply();
      }));
    } else if (!AXES_WITH_MODE.has(state.axis) || state.mode) {
      // Build the list of choices. In Overview mode for project/topic, only
      // show values that have a generated page. Long lists get an inline filter.
      let raw = (state.axis === 'project')
        ? data.projects.map(p => ({ key: p, label: p }))
        : poolFor(state.axis);

      // Gate Overview mode to values with generated pages only.
      if (state.mode === 'overview') {
        const pagesSet = state.axis === 'topic'
          ? new Set(data.topic_pages || [])
          : new Set(data.arc_pages || []);
        raw = raw.filter(x => pagesSet.has(x.key || x));
      }

      // Derive the click handler based on mode.
      const onClick = (x) => {
        if (state.mode === 'overview') {
          // Navigate to the static page rather than filtering the feed.
          // anchor_base must be prefixed — on deep-link pages (e.g. a
          // project arc) the current URL is /projects/<name>/, and a
          // bare 'projects/<other>/index.html' would resolve to
          // /projects/<name>/projects/<other>/ (nested). Using
          // __ANCHOR_BASE__ makes the href site-root-relative regardless
          // of which page the user is on.
          const base = window.__ANCHOR_BASE__ || './';
          const key = x.key || x;
          let href = '';
          if (state.axis === 'topic') {
            // Find the slug from topic_pages_map if available, else mangle key.
            const slugMap = data.topic_pages_map || {};
            const slug = slugMap[key] || key.toLowerCase().replace(/[^\w-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
            href = base + 'topics/' + slug + '.html';
          } else {
            href = base + 'projects/' + encodeURIComponent(key) + '/index.html';
          }
          window.location.href = href;
        } else {
          state.value = x.key || x; apply();
        }
      };

      const LONG = 30;
      if (raw.length > LONG) {
        const wrap = document.createElement('div');
        wrap.className = 'filter-longpool';
        const box = document.createElement('input');
        box.type = 'search';
        box.placeholder = 'filter ' + (AXIS_LABELS[state.axis] || '').toLowerCase() + '…';
        box.className = 'filter-longpool-search';
        box.autocomplete = 'off';
        const list = document.createElement('div');
        list.className = 'filter-longpool-list';
        const renderList = (needle) => {
          list.innerHTML = '';
          const q = (needle || '').trim().toLowerCase();
          const shown = raw.filter(x => !q || (x.label || x).toLowerCase().includes(q));
          shown.forEach(x => list.appendChild(makeChip(x.label || x, '',
            () => onClick(x))));
          if (!shown.length) {
            const empty = document.createElement('div');
            empty.className = 'filter-longpool-empty';
            empty.textContent = 'no matches';
            list.appendChild(empty);
          }
        };
        box.addEventListener('input', () => renderList(box.value));
        renderList('');
        wrap.appendChild(box);
        wrap.appendChild(list);
        options.appendChild(wrap);
        setTimeout(() => box.focus(), 60);
      } else {
        raw.forEach(x => options.appendChild(makeChip(x.label || x, '',
          () => onClick(x))));
      }
    }
  }

  // Cache original HTML for reversible search highlights
  const origCache = new WeakMap();
  function escRegex(s) { return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'); }
  function highlightInto(el, rx) {
    if (!origCache.has(el)) origCache.set(el, el.innerHTML);
    const orig = origCache.get(el);
    if (!rx) { el.innerHTML = orig; return false; }
    const tmp = document.createElement('div'); tmp.innerHTML = orig;
    const walker = document.createTreeWalker(tmp, NodeFilter.SHOW_TEXT);
    const nodes = []; let n;
    while ((n = walker.nextNode())) nodes.push(n);
    let any = false;
    nodes.forEach(t => {
      if (!t.nodeValue) return;
      const parts = t.nodeValue.split(rx);
      if (parts.length <= 1) return;
      any = true;
      const frag = document.createDocumentFragment();
      parts.forEach((p, i) => {
        if (!p) return;
        if (i % 2 === 1) {
          const m = document.createElement('mark');
          m.className = 'search-hit';
          m.textContent = p;
          frag.appendChild(m);
        } else frag.appendChild(document.createTextNode(p));
      });
      t.parentNode.replaceChild(frag, t);
    });
    el.innerHTML = tmp.innerHTML;
    return any;
  }

  let wasSearching = false;
  let wasHighlightingTopic = false;
  function applyVisibility() {
    // Search handling: search axis filters and highlights; other axes hide entries.
    const searching = state.axis === 'search' && state.value && state.value.trim();
    const qText = searching ? state.value.trim() : '';
    const qLower = qText.toLowerCase();
    const rx = searching ? new RegExp('(' + escRegex(qText) + ')', 'gi') : null;

    // Topic highlight: when a topic is selected, paint matches of the topic
    // word inside visible entries. Useful because topic tags are almost
    // always words that literally appear in the prose.
    const highlightingTopic = state.axis === 'topic' && state.value && state.value.trim();
    const topicRx = highlightingTopic
      ? new RegExp('(' + escRegex(state.value.trim()) + ')', 'gi') : null;

    let anyShown = 0;
    // Deep-link pages (project arcs, topic pages, doc pages) reuse the site
    // header but have no <main id="feed"> — there's nothing to filter, so
    // bail out early and leave the chip widget purely navigational.
    if (!feed) return;
    const entries = feed.querySelectorAll('article.entry');
    // Empty selection = show all — so the user can deselect every chip and
    // land on the same view as selecting every chip. Intuition wins over
    // forcing "at least one".
    const viewShows = v => state.views.size === 0 || state.views.has(v);
    entries.forEach(el => {
      // Restore pristine HTML when ANY highlight-producing state changes.
      const restore = searching || wasSearching || highlightingTopic || wasHighlightingTopic;
      if (restore && origCache.has(el)) {
        el.innerHTML = origCache.get(el);
      }

      // Doc entries belong to the 'library' view; everything else is 'daily'.
      const viewKey = el.dataset.view === 'library' ? 'library' : 'daily';
      if (!viewShows(viewKey)) {
        el.classList.add('filter-hidden');
        el.classList.remove('search-hidden');
        return;
      }

      let axisShow = true;
      if (state.axis && state.value && state.axis !== 'search') {
        if (state.axis === 'project') {
          const projs = (el.dataset.projects || '').split(',');
          axisShow = projs.includes(state.value);
        } else if (state.axis === 'week')     axisShow = el.dataset.week === state.value;
        else if (state.axis === 'month')       axisShow = el.dataset.month === state.value;
        else if (state.axis === 'mood')        axisShow = el.dataset.mood === state.value;
        else if (state.axis === 'learning')    axisShow = el.dataset.learning === state.value;
        else if (state.axis === 'year')        axisShow = el.dataset.year === state.value;
        else if (state.axis === 'topic') {
          const tags = (el.dataset.tags || '').split(',').filter(Boolean);
          axisShow = tags.includes(state.value);
        } else if (state.axis === 'entity') {
          const ents = (el.dataset.entities || '').split(',').filter(Boolean);
          axisShow = ents.includes(state.value);
        }
      }
      el.classList.toggle('filter-hidden', !axisShow);

      if (searching) {
        const hay = el.textContent.toLowerCase();
        const match = hay.includes(qLower);
        el.classList.toggle('search-hidden', !match);
        if (match) {
          highlightInto(el, rx);
          anyShown++;
          // Auto-expand any inspect panels whose own content matched so the
          // highlighted hit is actually visible, not buried behind a closed chip.
          el.querySelectorAll('.inspect-panel').forEach(p => {
            if (!p.textContent.toLowerCase().includes(qLower)) return;
            p.removeAttribute('hidden');
            const chip = el.querySelector('.inspect-chip[data-panel="' + p.id + '"]');
            if (chip) chip.classList.add('open');
            // Scroll the panel's own overflow to its first match, without
            // scrolling the surrounding page.
            const firstMark = p.querySelector('mark.search-hit');
            if (firstMark) {
              const mRect = firstMark.getBoundingClientRect();
              const pRect = p.getBoundingClientRect();
              p.scrollTop = p.scrollTop + (mRect.top - pRect.top) - 16;
            }
          });
        }
      } else {
        el.classList.remove('search-hidden');
        if (axisShow) anyShown++;
        // Topic highlight runs only on still-visible entries; search branch
        // (above) handles its own highlighting path.
        if (highlightingTopic && axisShow) {
          highlightInto(el, topicRx);
        }
      }
    });

    // Weekly rollup blocks — visible if 'weekly' is in the set, or if the
    // set is empty (= show all).
    const weeklyAllowed = viewShows('weekly');
    feed.querySelectorAll('.week-break, .week-rollup-wrap').forEach(el => {
      let show = weeklyAllowed;
      if (show) {
        if (searching) show = false;
        else if (state.axis && state.value) {
          if (state.axis === 'week') show = el.dataset.week === state.value;
          else if (state.axis === 'month') {
            const w = el.dataset.week;
            show = w && !!feed.querySelector('article.entry[data-week="' + w + '"][data-month="' + state.value + '"]');
          }
          else show = false;
        }
      }
      el.classList.toggle('filter-hidden', !show);
      el.classList.toggle('search-hidden', searching && weeklyAllowed);
      // Count rollup wraps (not break dividers) so the "no matches" check
      // reflects content, not decorations. Only count when dailies are
      // hidden — otherwise they'd double-count alongside dailies.
      if (show && !viewShows('daily') && el.classList.contains('week-rollup-wrap')) anyShown++;
    });

    // Monthly rollup blocks — visible if 'monthly' is in the set, or empty.
    const monthlyAllowed = viewShows('monthly');
    feed.querySelectorAll('.month-break, .month-rollup-wrap').forEach(el => {
      let show = monthlyAllowed;
      if (show) {
        if (searching) show = false;
        else if (state.axis && state.value) {
          if (state.axis === 'month') show = el.dataset.month === state.value;
          else if (state.axis === 'year') show = (el.dataset.month || '').slice(0, 4) === state.value;
          else show = false;
        }
      }
      el.classList.toggle('filter-hidden', !show);
      el.classList.toggle('search-hidden', searching && monthlyAllowed);
      if (show && !viewShows('daily') && !viewShows('weekly') && el.classList.contains('month-rollup-wrap')) anyShown++;
    });

    if (empty) empty.style.display = (state.value && !anyShown) ? '' : 'none';

    const hint = document.getElementById('filter-search-hint');
    if (hint) {
      if (searching) hint.textContent = anyShown + ' match' + (anyShown === 1 ? '' : 'es');
      else hint.textContent = '';
    }
    wasSearching = !!searching;
    wasHighlightingTopic = !!highlightingTopic;
  }
  function syncUrl() {
    const parts = [];
    // Only surface views in the URL if they differ from the default —
    // keeps casual URLs short. `views=daily,weekly` reserved for shares.
    const viewArr = [...state.views].sort();
    const defArr = [...DEFAULT_VIEWS].sort();
    if (viewArr.join(',') !== defArr.join(',')) parts.push('views=' + viewArr.join(','));
    if (state.axis) parts.push('axis=' + state.axis);
    if (state.mode) parts.push('mode=' + state.mode);
    if (state.value) parts.push('value=' + encodeURIComponent(state.value));
    const hash = parts.length ? '#' + parts.join('&') : '';
    if (location.hash !== hash) history.replaceState(null, '', location.pathname + hash);
  }
  function apply() { render(); applyVisibility(); syncUrl(); }
  // Global reset hook — used by the site-title link and anywhere else
  // that wants Clear-equivalent semantics without going through the chip.
  window.__resetFilters = function() {
    state.axis = null; state.value = null; state.mode = null;
    state.views.clear();
    saveViews(state.views);
    apply();
  };
  function parseHash() {
    const h = location.hash.replace(/^#/, '');
    if (!h) return;
    const params = Object.fromEntries(h.split('&').map(kv => {
      const [k, v] = kv.split('=');
      return [k, v ? decodeURIComponent(v) : ''];
    }));
    // Only adopt filter-related hashes; date anchors like #2026-04-12 are ignored here.
    if (['project','week','month','mood','learning','search','year','topic','entity'].includes(params.axis)) {
      state.axis = params.axis;
      if (params.mode && ['overview','timeline'].includes(params.mode)) state.mode = params.mode;
      if (params.value) state.value = params.value;
    }
    if (params.views) {
      const incoming = params.views.split(',').map(s => s.trim())
        .filter(x => VIEW_KEYS.includes(x));
      if (incoming.length) state.views = new Set(incoming);
    }
  }
  parseHash();
  apply();
})();
</script>
"""


REFRESH_WIDGET = """
<div id="refresh-bar" style="display:none;">
  <span id="refresh-msg"></span>
  <button id="refresh-btn" type="button">Refresh</button>
  <button id="schedule-btn" type="button" title="Scheduled auto-refresh" aria-label="Schedule">⚙</button>
</div>
<div id="schedule-modal" class="sched-modal" aria-hidden="true">
  <div class="sched-card" role="dialog" aria-label="Schedule settings">
    <h3>Auto-refresh schedule</h3>
    <p id="sched-state" class="sched-state">Checking…</p>
    <div class="sched-row">
      <label>Time: <input id="sched-hour" type="number" min="0" max="23" style="width:52px"> :
        <input id="sched-min" type="number" min="0" max="59" style="width:52px"></label>
    </div>
    <div class="sched-row sched-auto-row">
      <label><input id="sched-auto" type="checkbox">
        Auto-refresh when new content is detected</label>
      <p class="sched-hint">The page will quietly regenerate briefs/narrations in the
        background whenever it notices new session data. Disable if you'd rather click
        Refresh manually.</p>
    </div>
    <div class="sched-actions">
      <button id="sched-install" type="button" class="sched-primary">Install</button>
      <button id="sched-uninstall" type="button" class="sched-secondary" hidden>Remove</button>
      <button id="sched-close" type="button" class="sched-secondary">Close</button>
    </div>
    <p id="sched-raw" class="sched-raw"></p>
  </div>
</div>
<style>
#refresh-bar {
  position: fixed; top: 12px; right: 12px; z-index: 30;
  background: var(--paper); border: 1px solid var(--rule); border-radius: 20px;
  padding: 6px 14px; font: 12px ui-monospace, Consolas, monospace;
  color: var(--muted); box-shadow: var(--shadow);
  display: flex; align-items: center; gap: 10px;
}
#refresh-bar.has-updates { border-color: var(--accent-soft); color: var(--accent); }
#refresh-bar button {
  font: inherit; padding: 2px 10px; border: 1px solid var(--accent);
  background: var(--accent); color: var(--paper); border-radius: 12px; cursor: pointer;
}
#refresh-bar button:disabled { opacity: 0.5; cursor: wait; }
#refresh-bar.running button { background: var(--muted); border-color: var(--muted); }
#schedule-btn {
  background: transparent !important; color: var(--muted) !important;
  border: 1px solid var(--rule) !important; padding: 2px 6px !important;
}
#schedule-btn:hover { border-color: var(--accent-soft) !important; color: var(--accent) !important; }

.sched-modal { display: none; position: fixed; inset: 0; z-index: 2000;
  background: rgba(42, 33, 27, 0.35); align-items: center; justify-content: center; }
.sched-modal.open { display: flex; }
.sched-card {
  width: min(420px, 92vw); background: var(--paper); border: 1px solid var(--rule);
  border-radius: 10px; padding: 22px 24px; box-shadow: var(--shadow);
  font: 14px "Iowan Old Style", Palatino, Georgia, serif; color: var(--fg);
}
.sched-card h3 { margin: 0 0 10px; font-size: 18px; font-weight: 500; }
.sched-state { color: var(--muted); font-size: 13px; margin: 0 0 14px;
  font-family: ui-monospace, Consolas, monospace; }
.sched-state.installed { color: var(--ok); }
.sched-row { margin: 14px 0; font-size: 13px; }
.sched-row input[type="number"] { font: inherit; padding: 3px 6px; border: 1px solid var(--rule);
  border-radius: 4px; background: var(--bg); color: var(--fg); text-align: center; }
.sched-auto-row label { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.sched-auto-row input[type="checkbox"] { margin: 0; cursor: pointer; }
.sched-hint { margin: 6px 0 0 22px; font-size: 11px; color: var(--muted); line-height: 1.45; }
.sched-actions { display: flex; gap: 8px; margin-top: 16px; }
.sched-primary { flex: 1; padding: 6px 12px; background: var(--accent);
  color: var(--paper); border: none; border-radius: 4px; cursor: pointer; font: inherit; }
.sched-secondary { padding: 6px 12px; background: var(--paper); color: var(--fg);
  border: 1px solid var(--rule); border-radius: 4px; cursor: pointer; font: inherit; }
.sched-raw { margin-top: 14px; font-family: ui-monospace, Consolas, monospace;
  font-size: 11px; color: var(--muted); white-space: pre-wrap;
  max-height: 100px; overflow-y: auto; }
</style>
<script>
(function() {
  const bar = document.getElementById('refresh-bar');
  const msg = document.getElementById('refresh-msg');
  const btn = document.getElementById('refresh-btn');
  let polling = false;
  const AUTO_KEY = 'cj.autoRefresh';
  // Default ON — unset key reads as enabled; only an explicit '0' disables.
  const autoEnabled = () => localStorage.getItem(AUTO_KEY) !== '0';
  // Poll the server every 60s so the page detects new content without a reload.
  // When auto-refresh is on, detection triggers a pipeline run immediately.
  const STATUS_POLL_MS = 60 * 1000;
  async function checkStatus() {
    // First: is a refresh already in flight from a prior page? Re-attach if so.
    try {
      const rr = await fetch('/api/refresh', {cache:'no-store'});
      if (rr.ok) {
        const rs = await rr.json();
        if (rs.running) {
          bar.style.display = 'flex';
          bar.classList.add('running');
          btn.disabled = true;
          msg.textContent = fmtProgress(rs);
          pollRefresh();
          return;
        }
      }
    } catch (e) { return; /* server not available */ }
    try {
      const r = await fetch('/api/status', {cache: 'no-store'});
      if (!r.ok) return;
      const data = await r.json();
      if (data.error) return;
      bar.style.display = 'flex';
      bar.classList.remove('running');
      if (data.has_updates) {
        bar.classList.add('has-updates');
        msg.textContent = `${data.total_pending} update${data.total_pending===1?'':'s'} available`;
        btn.disabled = false; btn.textContent = 'Refresh now';
        if (autoEnabled() && !polling) {
          msg.textContent = `auto-refreshing (${data.total_pending} new)`;
          startRefresh();
        }
      } else {
        bar.classList.remove('has-updates');
        msg.textContent = 'up to date';
        btn.disabled = true; btn.textContent = 'Refresh';
      }
    } catch (e) { /* server not available */ }
  }
  async function startRefresh() {
    btn.disabled = true; bar.classList.add('running');
    msg.textContent = 'running...';
    try {
      const r = await fetch('/api/refresh', {method:'POST'});
      if (r.status === 409) { msg.textContent = 'already running'; }
      pollRefresh();
    } catch (e) { msg.textContent = 'error: ' + e.message; btn.disabled = false; }
  }
  function fmtProgress(s) {
    const stage = s.stage || 'running';
    if (s.total && s.total > 1) {
      const lbl = s.label ? ' · ' + s.label : '';
      return `${stage}  ${s.done}/${s.total}${lbl}`;
    }
    return stage + '...';
  }
  async function pollRefresh() {
    if (polling) return;
    polling = true;
    const tick = async () => {
      try {
        const r = await fetch('/api/refresh', {cache:'no-store'});
        const s = await r.json();
        if (s.running) {
          msg.textContent = fmtProgress(s);
          setTimeout(tick, 1500);
        } else {
          bar.classList.remove('running');
          if (s.error) { msg.textContent = 'error: ' + s.error.slice(0,80); btn.disabled = false; }
          else { msg.textContent = 'done — reloading'; setTimeout(() => location.reload(), 800); }
          polling = false;
        }
      } catch (e) { polling = false; btn.disabled = false; msg.textContent = 'error'; }
    };
    tick();
  }
  btn.addEventListener('click', startRefresh);
  if (location.protocol.startsWith('http')) {
    checkStatus();
    setInterval(checkStatus, STATUS_POLL_MS);
  }

  // --- Schedule modal ---
  const schBtn = document.getElementById('schedule-btn');
  const modal  = document.getElementById('schedule-modal');
  const $state = document.getElementById('sched-state');
  const $hour  = document.getElementById('sched-hour');
  const $min   = document.getElementById('sched-min');
  const $inst  = document.getElementById('sched-install');
  const $un    = document.getElementById('sched-uninstall');
  const $close = document.getElementById('sched-close');
  const $raw   = document.getElementById('sched-raw');
  const $auto  = document.getElementById('sched-auto');

  // Reflect persisted preference (per-browser) whenever the modal opens
  // and write back on toggle. Stored as '1' / '0' to keep the check trivial.
  if ($auto) {
    $auto.checked = autoEnabled();
    $auto.addEventListener('change', () => {
      localStorage.setItem(AUTO_KEY, $auto.checked ? '1' : '0');
      if ($auto.checked) checkStatus();  // kick immediately if pending
    });
  }

  async function loadSchedule() {
    $state.textContent = 'Checking…';
    $state.classList.remove('installed');
    $raw.textContent = '';
    try {
      const r = await fetch('/api/schedule', {cache: 'no-store'});
      const s = await r.json();
      if (s.installed) {
        $state.textContent = 'Installed — runs daily at ' + (s.time || '(unknown time)');
        $state.classList.add('installed');
        $un.hidden = false;
        $inst.textContent = 'Update';
        if (s.time && /\\d\\d:\\d\\d/.test(s.time)) {
          const [h, m] = s.time.split(':').map(x => parseInt(x, 10));
          if (!isNaN(h)) $hour.value = h;
          if (!isNaN(m)) $min.value = m;
        }
      } else {
        $state.textContent = 'No schedule installed — the journal will only update when you click Refresh.';
        $un.hidden = true;
        $inst.textContent = 'Install';
        if (!$hour.value) $hour.value = 23;
        if (!$min.value)  $min.value = 30;
      }
    } catch (e) {
      $state.textContent = 'Could not reach server: ' + e.message;
    }
  }
  function openModal() { modal.classList.add('open'); loadSchedule(); }
  function closeModal() { modal.classList.remove('open'); }
  schBtn?.addEventListener('click', openModal);
  $close?.addEventListener('click', closeModal);
  modal?.addEventListener('click', (ev) => { if (ev.target === modal) closeModal(); });

  $inst?.addEventListener('click', async () => {
    const hour = parseInt($hour.value, 10), minute = parseInt($min.value, 10);
    if (isNaN(hour) || isNaN(minute)) { $raw.textContent = 'Enter a valid time.'; return; }
    $inst.disabled = true; $raw.textContent = 'Installing…';
    try {
      const r = await fetch('/api/schedule/install', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ hour, minute })
      });
      const s = await r.json();
      $raw.textContent = s.raw || (s.ok ? 'Installed.' : 'Failed.');
      await loadSchedule();
    } catch (e) { $raw.textContent = 'error: ' + e.message; }
    finally { $inst.disabled = false; }
  });
  $un?.addEventListener('click', async () => {
    if (!confirm('Remove the nightly schedule?')) return;
    $un.disabled = true; $raw.textContent = 'Removing…';
    try {
      const r = await fetch('/api/schedule/uninstall', {method: 'POST'});
      const s = await r.json();
      $raw.textContent = s.raw || (s.ok ? 'Removed.' : 'Failed.');
      await loadSchedule();
    } catch (e) { $raw.textContent = 'error: ' + e.message; }
    finally { $un.disabled = false; }
  });
})();
</script>
"""

ASK_BUTTON = """
<button id="chat-fab" type="button" title="Ask the journal" aria-label="Ask the journal">
  <span class="spark">&#x2726;</span><span class="label">Ask</span><span class="hint">the journal</span>
</button>
"""


LIBRARY_BUTTON = """
<button id="library-fab" type="button" title="Manage library" aria-label="Manage library">
  <span class="lib-icon">&#x1F4DA;</span><span class="lib-label">Library</span><span class="hint">add reading</span>
</button>
"""


LIBRARY_WIDGET = """
<div id="library-modal" role="dialog" aria-label="Manage library">
  <div id="library-panel">
    <div id="library-head">
      <h3>Library</h3>
      <button id="library-close" aria-label="Close">&times;</button>
    </div>
    <div id="library-body">

      <div class="lib-section">
        <h4>Add a document</h4>
        <form class="lib-add-form" id="lib-add-form">
          <div class="lib-form-title" id="lib-form-title"></div>
          <div class="lib-drop-zone" id="lib-drop">
            Drop a file here, or click to pick
            <input type="file" id="lib-file" accept=".pdf,.md,.txt,.html" hidden>
            <span class="lib-file-name" id="lib-file-name"></span>
          </div>
          <label>Title <span style="opacity:.6">(optional, defaults to filename)</span>
            <input type="text" id="lib-title" maxlength="200">
          </label>
          <label>Projects <span style="opacity:.6">(click to toggle, optional)</span>
            <input type="text" id="lib-project-search" class="lib-project-search" placeholder="filter projects…" autocomplete="off">
            <div class="lib-project-list" id="lib-project-list"></div>
            <div class="lib-project-count" id="lib-project-count"></div>
          </label>
          <label>Tags <span style="opacity:.6">(comma-separated, optional)</span>
            <input type="text" id="lib-tags" placeholder="quantization, ml-infra">
          </label>
          <label>Why you're adding it — the narrator uses this
            <textarea id="lib-note" placeholder="what you want the journal to remember about this"></textarea>
          </label>
          <div class="lib-actions">
            <button type="button" id="lib-cancel-edit">Cancel</button>
            <button type="reset">Clear</button>
            <button type="submit" id="lib-submit">Add</button>
          </div>
          <div class="lib-status" id="lib-status"></div>
        </form>
      </div>

      <div class="lib-section">
        <h4>Current documents</h4>
        <div class="lib-list" id="lib-list">
          <div class="lib-empty">loading…</div>
        </div>
      </div>

    </div>
  </div>
</div>
<script>
(function() {
  const fab = document.getElementById('library-fab');
  const modal = document.getElementById('library-modal');
  const closeBtn = document.getElementById('library-close');
  const form = document.getElementById('lib-add-form');
  const formTitle = document.getElementById('lib-form-title');
  const cancelEditBtn = document.getElementById('lib-cancel-edit');
  const fileInput = document.getElementById('lib-file');
  const fileName = document.getElementById('lib-file-name');
  const drop = document.getElementById('lib-drop');
  const titleInput = document.getElementById('lib-title');
  const projectSearch = document.getElementById('lib-project-search');
  const projectList = document.getElementById('lib-project-list');
  const projectCount = document.getElementById('lib-project-count');
  const tagsInput = document.getElementById('lib-tags');
  const noteInput = document.getElementById('lib-note');
  const submitBtn = document.getElementById('lib-submit');
  const statusEl = document.getElementById('lib-status');
  const listEl = document.getElementById('lib-list');
  if (!fab || !modal) return;

  // Keep the file the user picked; cleared on reset or after successful upload.
  let currentFile = null;
  // All projects known to the server (stable across refreshes); and the
  // subset currently selected. Filter-text lives on the DOM input.
  let allProjects = [];
  const selectedProjects = new Set();
  // Edit mode: when non-null, submit PATCHes that doc id instead of
  // POSTing a new one. The full doc list is cached from the last refresh
  // so Edit clicks can pre-fill the form without a separate fetch.
  let editingId = null;
  let lastDocs = [];

  function setStatus(msg, kind) {
    statusEl.textContent = msg || '';
    statusEl.classList.remove('lib-error', 'lib-ok');
    if (kind) statusEl.classList.add('lib-' + kind);
  }

  function openModal() {
    modal.classList.add('open');
    // Edit state shouldn't survive a modal close — otherwise the next
    // opener sees the form still in "Editing: X" mode for a doc they
    // may have already removed.
    if (editingId) exitEdit();
    refresh();
  }
  function closeModal() { modal.classList.remove('open'); }
  fab.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', ev => { if (ev.target === modal) closeModal(); });

  async function refresh() {
    try {
      const r = await fetch('/api/docs', {cache: 'no-store'});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      renderProjects(data.projects || []);
      lastDocs = data.documents || [];
      renderList(lastDocs);
    } catch (e) {
      listEl.textContent = '';
      const err = document.createElement('div');
      err.className = 'lib-empty';
      err.textContent = 'could not load library: ' + e.message;
      listEl.appendChild(err);
    }
  }

  function renderProjects(projects) {
    // Cache the full list; actual chip rendering applies the search filter.
    allProjects = projects || [];
    // Drop selections for projects that are no longer known (rare, e.g.
    // the project was deleted elsewhere while the modal was open).
    const known = new Set(allProjects.map(p => p.id));
    for (const id of Array.from(selectedProjects)) {
      if (!known.has(id)) selectedProjects.delete(id);
    }
    applyProjectFilter();
  }

  function applyProjectFilter() {
    const q = (projectSearch.value || '').trim().toLowerCase();
    while (projectList.firstChild) projectList.removeChild(projectList.firstChild);
    // When searching, narrow the visible set. Selected chips stay visible
    // even when they don't match the query — otherwise the user can't
    // tell what they've picked unless they clear the filter.
    const matches = allProjects.filter(p =>
      selectedProjects.has(p.id) ||
      !q || p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q)
    );
    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'lib-project-empty';
      empty.textContent = 'no projects match';
      projectList.appendChild(empty);
    } else {
      // Sort: alphabetical by default so clicks don't cause visual jumps.
      // Only when the user is actively filtering do we float selected
      // chips to the top — otherwise they'd scroll out of view as they
      // type queries that don't match their picks.
      if (q) {
        matches.sort((a, b) => {
          const sa = selectedProjects.has(a.id) ? 0 : 1;
          const sb = selectedProjects.has(b.id) ? 0 : 1;
          if (sa !== sb) return sa - sb;
          return a.name.localeCompare(b.name);
        });
      } else {
        matches.sort((a, b) => a.name.localeCompare(b.name));
      }
      for (const p of matches) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'lib-project-chip';
        if (selectedProjects.has(p.id)) chip.classList.add('selected');
        chip.textContent = p.name;
        chip.title = p.id;
        chip.addEventListener('click', () => {
          if (selectedProjects.has(p.id)) selectedProjects.delete(p.id);
          else selectedProjects.add(p.id);
          applyProjectFilter();
        });
        projectList.appendChild(chip);
      }
    }
    const total = allProjects.length;
    const sel = selectedProjects.size;
    projectCount.textContent = sel
      ? `${sel} selected · ${total} projects total`
      : `${total} projects — click to select`;
  }

  projectSearch.addEventListener('input', applyProjectFilter);

  function renderList(docs) {
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
    if (!docs.length) {
      const e = document.createElement('div');
      e.className = 'lib-empty';
      e.textContent = 'no documents yet';
      listEl.appendChild(e);
      return;
    }
    for (const d of docs) {
      const row = document.createElement('div');
      row.className = 'lib-row';

      const date = document.createElement('div');
      date.className = 'lib-date';
      date.textContent = d.added_date || '';
      row.appendChild(date);

      const middle = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'lib-title';
      title.textContent = d.title || d.filename || d.id;
      middle.appendChild(title);

      const meta = document.createElement('div');
      meta.className = 'lib-meta';
      const parts = [];
      if (d.filename && d.filename !== d.title) parts.push(d.filename);
      if (d.projects && d.projects.length) parts.push('projects: ' + d.projects.join(', '));
      if (d.tags && d.tags.length) parts.push('tags: ' + d.tags.join(', '));
      parts.push(d.chars.toLocaleString() + ' chars');
      meta.textContent = parts.join(' · ');
      middle.appendChild(meta);
      row.appendChild(middle);

      const actions = document.createElement('div');
      actions.className = 'lib-actions-cell';

      const ed = document.createElement('button');
      ed.type = 'button';
      ed.className = 'lib-edit';
      ed.textContent = 'Edit';
      ed.addEventListener('click', () => startEdit(d.id));
      actions.appendChild(ed);

      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'lib-remove';
      rm.textContent = 'Remove';
      rm.addEventListener('click', () => removeDoc(d.id, d.title || d.filename || d.id));
      actions.appendChild(rm);

      row.appendChild(actions);
      listEl.appendChild(row);
    }
  }

  function startEdit(id) {
    const doc = lastDocs.find(d => d.id === id);
    if (!doc) { setStatus('doc not found in cached list — refreshing', 'error'); refresh(); return; }
    editingId = id;
    form.classList.add('editing');
    formTitle.textContent = `Editing: ${doc.title || doc.filename || doc.id}`;
    document.getElementById('lib-submit').textContent = 'Save changes';
    // Pre-fill form fields from the cached doc row.
    titleInput.value = doc.title || '';
    tagsInput.value = (doc.tags || []).join(', ');
    // Projects chips — sync the Set, then re-render so the paint matches.
    selectedProjects.clear();
    for (const pid of (doc.projects || [])) selectedProjects.add(pid);
    projectSearch.value = '';
    applyProjectFilter();
    noteInput.value = doc.note || '';
    setStatus('editing — cancel to return to Add', '');
  }

  function exitEdit() {
    editingId = null;
    form.classList.remove('editing');
    formTitle.textContent = '';
    document.getElementById('lib-submit').textContent = 'Add';
    form.reset();  // clears inputs, triggers the reset handler to tidy state
  }

  if (cancelEditBtn) cancelEditBtn.addEventListener('click', exitEdit);

  async function removeDoc(id, label) {
    if (!confirm(`Remove "${label}"? This can't be undone. Narrations that referenced it will regenerate on the next cycle.`)) return;
    setStatus('removing ' + id + '...');
    try {
      const r = await fetch('/api/docs/' + encodeURIComponent(id), {method: 'DELETE'});
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || 'HTTP ' + r.status);
      setStatus('removed', 'ok');
      refresh();
    } catch (e) {
      setStatus('error: ' + e.message, 'error');
    }
  }

  // File picker via click or drag. Synced to a single currentFile regardless
  // of which path the user took.
  drop.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) setFile(fileInput.files[0]);
  });
  drop.addEventListener('dragover', ev => {
    ev.preventDefault(); drop.classList.add('drag-over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
  drop.addEventListener('drop', ev => {
    ev.preventDefault(); drop.classList.remove('drag-over');
    if (ev.dataTransfer.files && ev.dataTransfer.files[0]) setFile(ev.dataTransfer.files[0]);
  });
  function setFile(f) {
    currentFile = f;
    fileName.textContent = f.name + '  (' + Math.round(f.size / 1024) + ' KB)';
  }

  form.addEventListener('reset', () => {
    setTimeout(() => {
      currentFile = null;
      fileName.textContent = '';
      selectedProjects.clear();
      projectSearch.value = '';
      applyProjectFilter();
      setStatus('');
    }, 0);
  });

  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    submitBtn.disabled = true;
    try {
      const projects = [...selectedProjects];
      const tags = tagsInput.value.split(',')
        .map(s => s.trim()).filter(Boolean);
      if (editingId) {
        // PATCH path — no file, just metadata. Summary regenerates
        // server-side if title or note changed; the cascade picks it
        // up on next pipeline cycle.
        setStatus('saving changes…');
        const r = await fetch('/api/docs/' + encodeURIComponent(editingId), {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            title: titleInput.value.trim(),
            projects, tags,
            note: noteInput.value.trim(),
          }),
        });
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || 'HTTP ' + r.status);
        setStatus('saved', 'ok');
        exitEdit();
        refresh();
      } else {
        if (!currentFile) { setStatus('pick a file first', 'error'); return; }
        setStatus('reading file…');
        const b64 = await readAsBase64(currentFile);
        setStatus('uploading + summarizing… this can take ~15 seconds');
        const r = await fetch('/api/docs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            filename: currentFile.name,
            content_base64: b64,
            title: titleInput.value.trim(),
            projects, tags,
            note: noteInput.value.trim(),
          }),
        });
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || 'HTTP ' + r.status);
        setStatus('added ' + body.id, 'ok');
        form.reset();
        refresh();
      }
    } catch (e) {
      setStatus('error: ' + e.message, 'error');
    } finally {
      submitBtn.disabled = false;
    }
  });

  function readAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // result is a data URL; strip the "data:*;base64," prefix.
        const s = String(reader.result || '');
        const comma = s.indexOf(',');
        resolve(comma >= 0 ? s.slice(comma + 1) : s);
      };
      reader.onerror = () => reject(reader.error || new Error('file read failed'));
      reader.readAsDataURL(file);
    });
  }
})();
</script>
"""


CHAT_WIDGET = """
<div id="chat-modal" role="dialog" aria-label="Chat with journal">
  <div id="chat-panel">
    <div id="chat-head">
      <h3>Ask the journal</h3>
      <button id="chat-close" aria-label="Close">×</button>
    </div>
    <div id="chat-log"></div>
    <form id="chat-form">
      <input id="chat-input" type="text" placeholder="ask anything..." autocomplete="off" required>
      <button type="submit">Ask</button>
    </form>
  </div>
</div>
<script>
(function() {
  const ANCHOR_RX = /\\[(\\d{4}-\\d{2}-\\d{2})\\]/g;
  const FRAG_BASE = window.__ANCHOR_BASE__ || "./";
  function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function inlineMd(s) {
    // Applied AFTER esc; ordering: code, bold, italic (italic last because its pattern is broadest)
    s = s.replace(/`([^`\\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\\*\\*([^*\\n]+)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*A-Za-z0-9])\\*([^*\\n]+)\\*(?![*A-Za-z0-9])/g, '$1<em>$2</em>');
    return s;
  }
  function linkAnchors(text) {
    let s = esc(text);
    s = inlineMd(s);
    return s.replace(ANCHOR_RX, (m, d) =>
      `<a class="anchor" href="${FRAG_BASE}#${d}">[${d}]</a>`);
  }
  function renderAnswer(text) {
    return text.split(/\\n\\n+/).filter(p => p.trim())
      .map(p => `<p>${linkAnchors(p.trim())}</p>`).join('');
  }
  const fab = document.getElementById('chat-fab');
  const modal = document.getElementById('chat-modal');
  const closeBtn = document.getElementById('chat-close');
  const log = document.getElementById('chat-log');
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  function open() { modal.classList.add('open'); setTimeout(() => input.focus(), 50); }
  function close() { modal.classList.remove('open'); }
  fab.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) close();
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const q = input.value.trim(); if (!q) return;
    const btn = form.querySelector('button'); btn.disabled = true;
    const qRow = document.createElement('div'); qRow.className = 'q-row'; qRow.textContent = '> ' + q;
    const aRow = document.createElement('div'); aRow.className = 'a-row';
    aRow.innerHTML = '<p class="loading">asking...</p>';
    log.append(qRow, aRow); input.value = '';
    aRow.scrollIntoView({behavior:'smooth', block:'end'});
    try {
      const r = await fetch('/api/ask', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({question: q})});
      const data = await r.json();
      if (data.error) { aRow.innerHTML = `<p class="loading">error: ${esc(data.error)}</p>`; }
      else {
        const src = (data.sources||[]).map(s =>
          `${s.kind}@${s.date||'-'}${s.project_name ? ' · '+s.project_name : ''}`).join(' · ');
        aRow.innerHTML = renderAnswer(data.answer) +
          (src ? `<div class="sources">${esc(src)}</div>` : '');
      }
    } catch (err) {
      aRow.innerHTML = `<p class="loading">error: ${esc(err.message)}</p>`;
    } finally {
      btn.disabled = false; input.focus();
      aRow.scrollIntoView({behavior:'smooth', block:'end'});
    }
  });
})();
</script>
"""


TTS_WIDGET = """
<!-- Audio plays pre-rendered WAVs from out/audio/. The browser-side
     vits-web fallback was removed: piper is now a hard pip dependency,
     so a WAV either exists (play it) or is pending (show the modal and
     wait for the next pipeline cycle to produce it). One at a time -
     starting a new play stops whatever was playing. -->
<div id="tts-not-ready" role="dialog" aria-labelledby="tts-nr-title" aria-hidden="true">
  <div class="tts-nr-card">
    <h3 id="tts-nr-title">Audio still being generated</h3>
    <p>Pre-rendered audio for this entry isn’t ready yet. The journal
       synthesises WAVs as part of each pipeline cycle — it’ll be
       available on the next refresh, usually within a few minutes.</p>
    <button id="tts-nr-close" type="button">Got it</button>
  </div>
</div>
<script>
(function() {
  // Shared state so that clicking a second play button stops the first.
  const state = { audio: null, button: null };

  function resetButton(btn) {
    if (!btn) return;
    btn.classList.remove("playing", "paused", "loading");
    btn.textContent = "▶";
    btn.title = "Read aloud";
  }
  function markPlaying(btn) {
    if (!btn) return;
    btn.classList.remove("paused", "loading");
    btn.classList.add("playing");
    btn.textContent = "⏸";
    btn.title = "Pause";
  }
  function markPaused(btn) {
    if (!btn) return;
    btn.classList.remove("playing", "loading");
    btn.classList.add("paused");
    btn.textContent = "▶";
    btn.title = "Resume";
  }

  function stopAll() {
    if (state.audio) {
      try { state.audio.pause(); } catch (e) {}
      try { state.audio.src = ""; } catch (e) {}
    }
    resetButton(state.button);
    state.audio = null;
    state.button = null;
  }

  function showNotReady() {
    const m = document.getElementById("tts-not-ready");
    if (m) m.classList.add("open");
  }

  function wirePlayButton(btn, audioUrl) {
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      // Clicking the currently-playing button toggles pause/resume.
      if (state.button === btn && state.audio) {
        if (state.audio.paused) { state.audio.play().catch(() => {}); markPlaying(btn); }
        else { state.audio.pause(); markPaused(btn); }
        return;
      }
      // Otherwise: stop whatever else is playing, then try to play this one.
      stopAll();
      if (!audioUrl) { showNotReady(); return; }
      btn.classList.add("loading");
      try {
        const head = await fetch(audioUrl, { method: "HEAD" });
        if (!head.ok) { btn.classList.remove("loading"); showNotReady(); return; }
      } catch (e) {
        btn.classList.remove("loading"); showNotReady(); return;
      }
      const audio = new Audio(audioUrl);
      audio.addEventListener("ended", () => {
        if (state.button === btn) { resetButton(btn); state.audio = null; state.button = null; }
      });
      audio.addEventListener("error", () => {
        if (state.button === btn) { resetButton(btn); state.audio = null; state.button = null; }
        showNotReady();
      });
      state.audio = audio;
      state.button = btn;
      try {
        await audio.play();
        markPlaying(btn);
      } catch (e) {
        btn.classList.remove("loading");
        showNotReady();
      }
    });
  }

  function makePlayButton() {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tts-play";
    b.textContent = "▶";
    b.title = "Read aloud";
    b.setAttribute("aria-label", "Read aloud");
    return b;
  }

  const AUDIO_BASE = (window.__ANCHOR_BASE__ || "./") + "audio";

  function injectEntryButtons() {
    // Dailies — anchor-id is YYYY-MM-DD, audio is daily-<id>.wav.
    // Skip doc entries (handled separately below) and skip anything
    // that already has a button (idempotent for MutationObserver).
    // Project-day narrations have no standalone render surface today —
    // project filter just scopes the main index — so we don't need
    // separate per-project-per-day audio buttons.
    document.querySelectorAll("article.entry:not(.doc-entry) .entry-head h2").forEach(h2 => {
      if (h2.querySelector(".tts-play")) return;
      const entry = h2.closest("article.entry");
      const id = entry ? entry.id : null;
      if (!id) return;
      const btn = makePlayButton();
      wirePlayButton(btn, `${AUDIO_BASE}/daily-${id}.wav`);
      h2.appendChild(btn);
    });
    // Weekly rollups — data-week on the wrap element.
    document.querySelectorAll(".week-rollup-wrap").forEach(wb => {
      if (wb.querySelector(".tts-play")) return;
      const week = wb.dataset.week;
      if (!week) return;
      const rollup = wb.querySelector(".week-rollup");
      if (!rollup) return;
      const btn = makePlayButton();
      wirePlayButton(btn, `${AUDIO_BASE}/weekly-${week}.wav`);
      rollup.prepend(btn);
    });
    // Monthly rollups — data-month on the wrap element.
    document.querySelectorAll(".month-rollup-wrap").forEach(mb => {
      if (mb.querySelector(".tts-play")) return;
      const ym = mb.dataset.month;
      if (!ym) return;
      const rollup = mb.querySelector(".month-rollup");
      if (!rollup) return;
      const btn = makePlayButton();
      wirePlayButton(btn, `${AUDIO_BASE}/monthly-${ym}.wav`);
      rollup.prepend(btn);
    });
    // Document entries — data-doc-id on the article. WAV is doc-<id>.wav.
    // Doc entries use the shared doc-page body (not an .entry-head), so
    // we target the doc-page's own <h2> instead.
    document.querySelectorAll("article.entry.doc-entry").forEach(ae => {
      const h2 = ae.querySelector(".doc-page > h2") || ae.querySelector("h2");
      if (!h2 || h2.querySelector(".tts-play")) return;
      const docId = ae.dataset.docId;
      if (!docId) return;
      const btn = makePlayButton();
      wirePlayButton(btn, `${AUDIO_BASE}/doc-${docId}.wav`);
      h2.appendChild(btn);
    });
    // Interlude blocks — data-interlude-date on the wrapper.
    document.querySelectorAll(".interlude[data-interlude-date]").forEach(il => {
      if (il.querySelector(".tts-play")) return;
      const d = il.dataset.interludeDate;
      if (!d) return;
      const btn = makePlayButton();
      wirePlayButton(btn, `${AUDIO_BASE}/interlude-${d}.wav`);
      il.prepend(btn);
    });
  }

  // Modal dismiss — Got it button or clicking the backdrop.
  document.addEventListener("click", (ev) => {
    const m = document.getElementById("tts-not-ready");
    if (!m || !m.classList.contains("open")) return;
    if (ev.target.id === "tts-nr-close" || ev.target === m) m.classList.remove("open");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const m = document.getElementById("tts-not-ready");
    if (m) m.classList.remove("open");
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectEntryButtons);
  } else {
    injectEntryButtons();
  }
  // Re-inject when new entries appear (filter reveal, dynamic loads).
  new MutationObserver(injectEntryButtons).observe(document.body, { childList: true, subtree: true });
})();
</script>
"""


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def layout(title: str, body: str, anchor_base: str = "./") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · ClaudeJournal</title>
<style>{CSS}</style>
</head>
<body>
<script>window.__ANCHOR_BASE__ = {json.dumps(anchor_base)};</script>
<div id="top-actions">{ASK_BUTTON}{LIBRARY_BUTTON}</div>
<div class="wrap">{body}</div>
{FILTER_WIDGET}
{INSPECT_WIDGET}
{ANNOTATION_WIDGET}
{REFRESH_WIDGET}
{CHAT_WIDGET}
{LIBRARY_WIDGET}
{TTS_WIDGET}
</body>
</html>
"""


def _pretty_date(iso: str) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d")
        return dt.strftime("%A, %B %-d")  # Linux/macOS
    except ValueError:
        return iso
    except Exception:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%A, %B %#d")  # Windows
        except Exception:
            return iso


def _pretty_date_safe(iso: str) -> str:
    """Cross-platform day-of-month that doesn't crash."""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d")
    except (ValueError, TypeError):
        return iso
    return dt.strftime("%A, %B ") + str(int(dt.strftime("%d")))


def _pretty_date_year(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%Y")
    except (ValueError, TypeError):
        return ""


def _fmt_generated_at(iso: str) -> str:
    """Short, readable UTC-to-user-friendly form for a narration/brief
    generated_at timestamp. Accepts the ISO form we persist ('%Y-%m-%dT%H:%M:%S[.ffffff][+00:00]')
    and falls back to the raw string if parsing fails."""
    if not iso:
        return ""
    raw = iso.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            # Stamps are stored in UTC (via datetime.now(timezone.utc) in
            # brief/narrate). Render with the Y-M-D + short time so it's
            # legible at a glance.
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            continue
    return raw[:19]  # graceful degrade — first 19 chars of an ISO string


def _relative_timestamp(iso: str) -> str:
    """Compact relative-time label for the header 'updated' chip.

    Recent timestamps (< 7 days) render as 'just now', '12m ago', '4h ago',
    or '3d ago'. Older timestamps fall back to a short absolute date
    ('2026-04-15') so archival entries don't read as confusingly aged.
    Returns empty string when input is unparseable.
    """
    if not iso:
        return ""
    raw = iso.strip()
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return raw[:10]
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    if dt.tzinfo is None:
        # Stored timestamps are UTC; assume so when no tz suffix.
        dt = dt.replace(tzinfo=_tz.utc)
    diff = (now - dt).total_seconds()
    if diff < 0:
        return dt.strftime("%Y-%m-%d")
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    if diff < 7 * 86400:
        return f"{int(diff // 86400)}d ago"
    return dt.strftime("%Y-%m-%d")


def _count_meta(row: dict, mood: str = "") -> str:
    # Counts live only in the inspect chips at the bottom. The header
    # carries the mood only — tone without duplication.
    if mood:
        return f'<span class="mood">{esc(mood)}</span>'
    return ""


def _render_activity_disclosure(row: dict, prompts: list[dict], snippets: list[dict],
                                 files: list[dict], briefs: list[dict] | None,
                                 entry_id: str,
                                 narration_generated_at: str = "",
                                 entities: list[dict] | None = None,
                                 echoes: dict | None = None,
                                 open_loops_items: list[dict] | None = None,
                                 anchor_base: str = "./") -> tuple[str, str, str, str]:
    """Per-category inspect chips. Each chip toggles its own panel. Multiple
    can be open at once.

    Returns (header_chips_html, header_panels_html, inspect_row_html,
    inspect_panels_html). The caller places header_chips_html in the
    entry header (top-right where mood used to live) and the matching
    header_panels_html immediately under the header so the drop-down
    lands where the user clicked. inspect_row_html + inspect_panels_html
    go at the bottom of the article. Splitting the panels by location
    avoids the visual disconnect of a header chip opening a panel at
    the bottom of the entry.

    Inspect-row order: briefs → learned → prompts → moments → files →
    entities → memory → loops.

    Header-row order: updated.

    All meta-info-about-the-day lives in chips, never in the prose body
    or in inline banners. The header carries only the day's identity
    (date + the latest-update chip); the inspect row carries everything
    else.

    entities: [{key, label, type}] for this date.
    echoes: temporal recall dict ({prior_years, recurring_friction, milestones}).
    open_loops_items: list of loop dicts touching today's projects, oldest first.
    """
    n_files = len(files); n_prompts = len(prompts); n_snips = len(snippets)
    n_briefs = len(briefs) if briefs else 0
    # Show "Updated" chip whenever the day has *any* content to timestamp.
    if not (n_files or n_prompts or n_snips or n_briefs or narration_generated_at):
        return ("", "", "", "")

    # Collect all learnings from this day's briefs for the dedicated chip.
    all_learned: list[str] = []
    if briefs:
        for b in briefs:
            for item in (b.get("learned") or []):
                if isinstance(item, str) and item.strip():
                    all_learned.append(item.strip())

    chips: list[str] = []         # bottom inspect row
    header_chips: list[str] = []  # top-right of entry header (Updated only)
    panels: list[str] = []        # panels for inspect-row chips
    header_panels: list[str] = [] # panels for header chips (sit just below
                                  # the header so the drop-down lands where
                                  # the user clicked, not at the bottom of
                                  # the article)

    def _add(kind: str, label: str, body: str, searchable: bool = False,
             search_placeholder: str = "filter...",
             location: str = "inspect",
             extra_chip_class: str = "",
             count: int | None = None) -> None:
        """Build a chip + panel pair.

        `count`: optional integer that renders as a dimmed parenthetical
        after the label, e.g. "briefs (115)". Lets the row stay legible
        as a single line while preserving the at-a-glance signal.
        """
        if not body:
            return
        panel_id = f"insp-{esc(entry_id)}-{kind}"
        cls = "inspect-chip"
        if extra_chip_class:
            cls += f" {extra_chip_class}"
        if count is not None:
            label_html = (
                f'{esc(label)} <span class="chip-count">({count})</span>'
            )
        else:
            label_html = esc(label)
        chip_html = (
            f'<button class="{cls}" type="button" data-panel="{panel_id}">'
            f'{label_html}</button>'
        )
        if searchable:
            inner = (
                f'<input class="inspect-search" type="search" '
                f'placeholder="{esc(search_placeholder)}" '
                f'data-target="{panel_id}-content" aria-label="filter">'
                f'<div class="inspect-content" id="{panel_id}-content">{body}'
                f'<div class="inspect-empty-match" hidden>No matches.</div></div>'
            )
        else:
            inner = body
        panel_html = f'<div class="inspect-panel" id="{panel_id}" hidden>{inner}</div>'
        if location == "header":
            header_chips.append(chip_html)
            header_panels.append(panel_html)
        else:
            chips.append(chip_html)
            panels.append(panel_html)

    # --- briefs (structured, rich) ---
    if briefs:
        body_parts: list[str] = []
        for b in briefs:
            def _ul(items):
                return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>" if items else ""
            bp = []
            if b.get("goal"):     bp.append(f"<h4>Goal</h4><p>{esc(b['goal'])}</p>")
            if b.get("did"):      bp.append("<h4>Did</h4>"      + _ul(b["did"]))
            if b.get("learned"):  bp.append("<h4>Learned</h4>"  + _ul(b["learned"]))
            if b.get("friction"): bp.append("<h4>Friction</h4>" + _ul(b["friction"]))
            if b.get("wins"):     bp.append("<h4>Wins</h4>"     + _ul(b["wins"]))
            if b.get("tags"):
                tag_html = " ".join(f'<code>{esc(t)}</code>' for t in b["tags"] if t)
                if tag_html:
                    bp.append(f"<h4>Tags</h4><p>{tag_html}</p>")
            # Wrap each brief as a single filterable block so search hides
            # non-matching briefs wholesale while highlighting inside matches.
            body_parts.append(f'<div class="filterable brief-block">{"".join(bp)}</div>')
        _add("briefs",
             "briefs",
             "".join(body_parts),
             searchable=True, search_placeholder="filter briefs...",
             count=n_briefs)

    # --- learned (dedicated chip: all learning items across the day's briefs) ---
    if all_learned:
        n_learned = len(all_learned)
        learned_items_html = (
            "<ul>"
            + "".join(f'<li class="filterable">{esc(x)}</li>' for x in all_learned)
            + "</ul>"
        )
        _add("learned",
             "learned",
             learned_items_html,
             searchable=True, search_placeholder="filter learned...",
             count=n_learned)

    # --- prompts ---
    if prompts:
        items = []
        for p in prompts:
            cls = "filterable"
            if p.get("kind") == "correction": cls += " correction"
            elif p.get("kind") == "appreciation": cls += " appreciation"
            items.append(f'<blockquote class="{cls}">{esc(p["summary"])}</blockquote>')
        _add("prompts", "prompts", "".join(items),
             searchable=True, search_placeholder="filter prompts...",
             count=n_prompts)

    # --- snippets (notable moments) ---
    if snippets:
        items = [f'<div class="snippet filterable">{esc(s["text"])}</div>' for s in snippets]
        _add("moments", "moments", "".join(items),
             searchable=True, search_placeholder="filter moments...",
             count=n_snips)

    # --- files ---
    if files:
        items = "".join(
            f'<li class="filterable">{esc(f["path"])} '
            f'<span class="meta">· {f["touch_count"]}×</span></li>'
            for f in files
        )
        _add("files", "files", f"<ul class='files'>{items}</ul>",
             searchable=True, search_placeholder="filter files...",
             count=n_files)

    # --- entities (named-entity chip: people, libraries, AI models, services) ---
    # Links each entity name to the entity-filtered feed view so clicking
    # "React" jumps straight to all days that mention React.
    if entities:
        type_order = {"person": 0, "ai_model": 1, "library": 2, "service": 3}
        type_labels = {"person": "People", "ai_model": "AI Models",
                       "library": "Libraries", "service": "Services"}
        grouped: dict[str, list[dict]] = {}
        for ent in sorted(entities, key=lambda e: (type_order.get(e["type"], 9), e["label"].lower())):
            grouped.setdefault(ent["type"], []).append(ent)
        parts = []
        for etype in ("person", "ai_model", "library", "service"):
            if etype not in grouped:
                continue
            items_html = "".join(
                f'<a href="{esc(anchor_base)}index.html#axis=entity&value={esc(ent["key"])}" '
                f'class="entity-chip entity-{esc(etype)} filterable">'
                f'{esc(ent["label"])}</a>'
                for ent in grouped[etype]
            )
            parts.append(
                f'<div class="entity-group">'
                f'<span class="entity-group-label">{esc(type_labels[etype])}</span>'
                f'<span class="entity-group-items">{items_html}</span>'
                f'</div>'
            )
        n_ents = len(entities)
        _add("entities", "tools", "".join(parts),
             searchable=True, search_placeholder="filter tools...",
             count=n_ents)

    # --- memory (temporal recall echoes) ---
    # Prior-year links, recurring-friction patterns, and project anniversaries.
    # Was a banner between header and prose; now lives here so the entry body
    # is uninterrupted prose and meta-info-about-the-day is uniformly grouped.
    echo_items = _build_echo_items(echoes or {}, anchor_base=anchor_base)
    if echo_items:
        n_echoes = len(echo_items)
        echo_panel = (
            '<div class="echo-banner">'
            + '<span class="echo-sep"> · </span>'.join(echo_items)
            + '</div>'
        )
        _add("memory", "memory", echo_panel, count=n_echoes)

    # --- loops (open frictions touching this day's projects) ---
    # Hybrid panel: lists this day's loops inline, plus a "view all on
    # Loops page" link at the bottom. Click the chip to expand; click the
    # link inside to navigate to the standing page for the full corpus.
    loops_items = open_loops_items or []
    if loops_items:
        n_loops = len(loops_items)
        loop_rows: list[str] = []
        for ll in loops_items[:30]:  # cap inline list at 30 items
            ld = ll.get("date", "")
            lp = ll.get("project_name", ll.get("project_id", "?"))
            la = ll.get("age_days")
            age_str = f' <span class="loop-age">{la}d</span>' if la is not None else ""
            ltext = ll.get("friction", "").strip()
            loop_rows.append(
                f'<li class="loop-row filterable">'
                f'  <div class="loop-meta">'
                f'    <span class="loop-date">{esc(ld)}</span> · '
                f'    <span class="loop-proj">{esc(lp)}</span>{age_str}'
                f'  </div>'
                f'  <div class="loop-text">{esc(ltext)}</div>'
                f'</li>'
            )
        more_note = ""
        if n_loops > len(loop_rows):
            more_note = (
                f'<p class="loop-more-note">'
                f'Showing {len(loop_rows)} of {n_loops}. '
                f'<a href="{esc(anchor_base)}loops.html">View all on Loops page →</a>'
                f'</p>'
            )
        else:
            more_note = (
                f'<p class="loop-more-note">'
                f'<a href="{esc(anchor_base)}loops.html">View all on Loops page →</a>'
                f'</p>'
            )
        loops_body = (
            f'<ul class="loop-list">{"".join(loop_rows)}</ul>'
            f'{more_note}'
        )
        _add("loops", "loops", loops_body,
             searchable=True, search_placeholder="filter loops...",
             count=n_loops)

    # --- updated (narration + brief generation timestamps) ---
    # One chip showing when this entry was last written. The panel lists the
    # daily narration's timestamp plus each brief's own generated_at, so
    # users can tell if an entry is stale relative to fresh briefs.
    narration_line = ""
    if narration_generated_at:
        narration_line = (
            f'<p><strong>Narration written:</strong> '
            f'{esc(_fmt_generated_at(narration_generated_at))}</p>'
        )
    brief_rows = ""
    if briefs:
        rows = []
        # Sort by project name to match the brief panel's order.
        for b in sorted(briefs, key=lambda x: (x.get("_project_name") or "").lower()):
            ts = _fmt_generated_at(b.get("_generated_at", ""))
            if not ts:
                continue
            rows.append(
                f'<li><code>{esc(b.get("_project_name","") or "")}</code> · {esc(ts)}</li>'
            )
        if rows:
            brief_rows = "<p><strong>Briefs generated:</strong></p><ul>" + "".join(rows) + "</ul>"
    if narration_line or brief_rows:
        # Header chip uses a compact relative-time label like "updated 2h ago"
        # (or absolute date if older than 7 days). Falls back to "updated" when
        # we have no narration timestamp but do have brief timestamps.
        if narration_generated_at:
            chip_label = f"updated {_relative_timestamp(narration_generated_at)}"
        else:
            chip_label = "updated"
        _add("updated", chip_label, narration_line + brief_rows,
             location="header")

    inspect_row_html = (
        f'<div class="inspect-row">{"".join(chips)}</div>' if chips else ""
    )
    header_chips_html = "".join(header_chips)
    header_panels_html = "".join(header_panels)
    panels_html = "".join(panels)
    return (header_chips_html, header_panels_html, inspect_row_html, panels_html)


def _iso_week_of(date: str) -> str:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return ""


def render_doc_feed_entry(doc: dict, summary: dict, anchor_base: str = "./") -> str:
    """In-feed doc entry. Renders the same full content as the standalone
    per-doc page — user asked the feed card to BE the summary, not a
    teaser — and wraps it in an <article class="entry doc-entry"> shell
    so the filter/search/view-chip machinery can treat it as a feed item.

    The only intentional difference from the standalone page: no
    back-to-journal crumb, and the title is a link to the standalone
    page so the reader can jump to the excerpt + download view if they
    want to dig further.
    """
    doc_id = doc.get("id", "")
    added_date = doc.get("added_date", "")
    projects = doc.get("_project_names") or []
    tags_all = list(dict.fromkeys(
        (doc.get("_tags_list") or []) + (summary.get("tags") or [])
    ))
    week_attr = _iso_week_of(added_date)
    month_attr = added_date[:7] if len(added_date) >= 7 else ""
    year_attr = added_date[:4] if len(added_date) >= 4 else ""
    projects_attr = ",".join(projects)
    page_body = render_document_page(doc, summary, anchor_base=anchor_base)
    return (
        f'<article class="entry doc-entry" data-view="library" '
        f'data-doc-id="{esc(doc_id)}" '
        f'data-projects="{esc(projects_attr)}" data-week="{esc(week_attr)}" '
        f'data-month="{esc(month_attr)}" data-year="{esc(year_attr)}" '
        f'data-tags="{esc(",".join(tags_all))}">'
        f'{page_body}'
        f'</article>'
    )


def render_document_page(doc: dict, summary: dict, anchor_base: str = "../",
                         backlinks: list[dict] | None = None) -> str:
    """Full-page document view. doc is a row from the documents table;
    summary is the parsed JSON from narrations.prose (scope='document').

    Layout mirrors the daily/weekly/monthly standalone-page idiom so the
    page feels at home in the journal. Everything needed to reference
    this document lives here: summary, user note, tags, projects, a
    collapsible excerpt of the source text, and a download link to the
    original file (served via the API so the raw filesystem stays
    off-limits). """
    title = doc.get("title") or doc.get("original_filename") or doc.get("id", "document")
    added_date = doc.get("added_date", "")
    user_note = (doc.get("user_note") or "").strip()
    project_names = doc.get("_project_names") or []
    tags = doc.get("_tags_list") or []
    excerpt = (doc.get("extracted_text") or "").strip()
    ext = doc.get("ext") or ""

    hook = (summary.get("hook") or "").strip()
    takeaway = (summary.get("takeaway") or "").strip()
    key_points = [k for k in (summary.get("key_points") or []) if isinstance(k, str)]
    summary_tags = [t for t in (summary.get("tags") or []) if isinstance(t, str)]

    meta_parts: list[str] = []
    if added_date: meta_parts.append(f"added {esc(added_date)}")
    if project_names: meta_parts.append("projects: " + esc(", ".join(project_names)))
    all_tags = list(dict.fromkeys(tags + summary_tags))  # de-dupe, preserve order
    if all_tags:
        meta_parts.append("tags: " + " ".join(f"<code>{esc(t)}</code>" for t in all_tags))
    meta_html = (
        f'<div class="doc-meta">{" · ".join(meta_parts)}</div>'
        if meta_parts else ""
    )

    hook_html = f'<p class="doc-hook">{esc(hook)}</p>' if hook else ""
    takeaway_html = (
        f'<div class="doc-section"><h3>Takeaway</h3>'
        f'<p>{esc(takeaway)}</p></div>'
        if takeaway else ""
    )
    points_html = ""
    if key_points:
        items = "".join(f"<li>{esc(p)}</li>" for p in key_points)
        points_html = (
            f'<div class="doc-section"><h3>Key points</h3>'
            f'<ul>{items}</ul></div>'
        )
    note_html = ""
    if user_note:
        note_html = (
            f'<div class="doc-section doc-note"><h3>My note</h3>'
            f'<p>{esc(user_note)}</p></div>'
        )
    # Excerpt: show first ~6 KB, collapsed. Bigger than a daily entry but
    # not a full doc dump — enough to skim / ctrl-F without loading
    # megabytes of prose onto the page.
    excerpt_html = ""
    if excerpt:
        head = excerpt[:6000]
        truncated = len(excerpt) > 6000
        paragraphs = [p.strip() for p in head.split("\n\n") if p.strip()]
        para_html = "".join(f"<p>{esc(p)}</p>" for p in paragraphs) or f"<p>{esc(head)}</p>"
        if truncated:
            para_html += (
                '<p class="doc-trunc">… truncated. '
                f'<a href="/api/docs/{esc(doc["id"])}/file">Download the full file</a> '
                'to read the rest.</p>'
            )
        excerpt_html = (
            f'<details class="doc-section doc-excerpt"><summary>Extracted text '
            f'<span class="doc-excerpt-hint">({len(excerpt):,} characters — first 6,000 shown)</span>'
            f'</summary><div class="doc-excerpt-body">{para_html}</div></details>'
        )
    download_html = (
        f'<p class="doc-download">'
        f'<a href="/api/docs/{esc(doc["id"])}/file">'
        f'Download original file</a>'
        + (f' <span class="doc-ext">({esc(ext)})</span>' if ext else "")
        + '</p>'
    )

    # Title is a link to the standalone doc page. No-op when this body
    # is rendered *as* the standalone page (reloads current URL), but
    # lights up when the same body is embedded in the main feed.
    title_html = (
        f'<h2><a href="{anchor_base}docs/{esc(doc.get("id",""))}.html" '
        f'style="color:inherit;text-decoration:none;">{esc(title)}</a></h2>'
    )
    doc_backlinks_html = _render_backlinks_section(backlinks or [])

    return (
        f'<article class="doc-page">'
        f'  {title_html}'
        f'  {meta_html}'
        f'  {hook_html}'
        f'  {takeaway_html}'
        f'  {points_html}'
        f'  {note_html}'
        f'  {download_html}'
        f'  {excerpt_html}'
        f'  {doc_backlinks_html}'
        f'</article>'
    )


def render_interlude_block(interlude: dict | None) -> str:
    if not interlude or not interlude.get("prose"):
        return ""
    form = interlude.get("form", "")
    prose = interlude["prose"].strip()
    # ASCII forms preserved with pre; others flow as italic paragraphs.
    if form == "ascii_doodle":
        body = f"<pre>{esc(prose)}</pre>"
    else:
        paragraphs = [p.strip() for p in prose.split("\n\n") if p.strip()]
        if len(paragraphs) > 1:
            body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
        else:
            # For single short poems/haiku, preserve line breaks as <br>
            body = "<p>" + "<br>".join(esc(line) for line in prose.split("\n") if line.strip()) + "</p>"
    # data-interlude-date lets the audio injector wire a play button
    # pointing at interlude-<date>.wav. ASCII doodles have no good
    # spoken rendering, so skip audio for those.
    il_date = interlude.get("date", "")
    data_attr = (f' data-interlude-date="{esc(il_date)}"'
                 if il_date and form != "ascii_doodle" else "")
    return (f'<div class="interlude"{data_attr}>'
            f'<span class="tag">a quiet day · {esc(form.replace("_", " "))}</span>'
            f'{body}</div>')


def _build_echo_items(echoes: dict, anchor_base: str) -> list[str]:
    """Return a list of <span class='echo-item'> strings for the day's
    echoes (prior years, recurring friction, milestones). Centralises the
    item rendering so both the inline banner (legacy) and the inspect-chip
    panel can share output.

    Empty list → caller should not render any echoes surface.
    """
    if not echoes:
        return []
    prior_years = echoes.get("prior_years") or []
    recurring = echoes.get("recurring_friction") or []
    milestones = echoes.get("milestones") or []
    items: list[str] = []
    for py in prior_years[:3]:
        py_date = py["date"]
        yr_diff = py["year_diff"]
        label = f"{yr_diff} year{'s' if yr_diff != 1 else ''} ago"
        snippet = py.get("snippet", "")
        title_attr = f' title="{esc(snippet)}"' if snippet else ""
        items.append(
            f'<span class="echo-item">{esc(label)}: '
            f'<a href="{anchor_base}#{esc(py_date)}"{title_attr}>{esc(py_date)}</a>'
            f'</span>'
        )
    for rf in recurring[:2]:
        tag = rf["tag"]
        count = rf["count"]
        items.append(
            f'<span class="echo-item">recurring friction &ldquo;{esc(tag)}&rdquo; '
            f'({count}× across {count} dates)</span>'
        )
    for ms in milestones[:2]:
        items.append(
            f'<span class="echo-item">{esc(ms["project_name"])} is {esc(ms["label"])}</span>'
        )
    return items


def _echo_chip_count(echoes: dict) -> int:
    """How many echo items would be rendered for this day. Used to decide
    whether to surface the inspect chip and to label its count."""
    if not echoes:
        return 0
    n = 0
    n += min(len(echoes.get("prior_years") or []), 3)
    n += min(len(echoes.get("recurring_friction") or []), 2)
    n += min(len(echoes.get("milestones") or []), 2)
    return n


def _render_annotation_block(annotations: list[dict]) -> str:
    """Render pinned user annotations below the AI prose.

    Each annotation is rendered verbatim (no post_process linkification — this
    is the user's voice). The _contradiction flag (set by the render-time
    contradiction guard in render.py) adds a warning icon when the AI prose
    may not reflect the correction.
    """
    if not annotations:
        return ""
    items: list[str] = []
    for ann in annotations:
        label = ann.get("annotation_type", "append")
        text  = ann.get("text", "")
        ann_id = ann.get("id", "")
        has_contra = bool(ann.get("_contradiction"))
        warn_html = (
            '<span class="annotation-contradiction-warn" '
            'title="This annotation may not be reflected in the current AI prose. '
            'Re-run the pipeline to regenerate.">&#9888;</span>'
            if has_contra else ""
        )
        items.append(
            f'<div class="annotation-item type-{esc(label)}" data-ann-id="{esc(str(ann_id))}">'
            f'  <button class="annotation-delete-btn" data-ann-id="{esc(str(ann_id))}" '
            f'          title="Delete annotation">delete</button>'
            f'  <div class="annotation-item-label">{esc(label)}{warn_html}</div>'
            f'  <div class="annotation-item-text">{esc(text)}</div>'
            f'</div>'
        )
    return f'<div class="annotations-block">{"".join(items)}</div>'


def render_day_entry(date: str, narration: str, mood: str,
                     counts_row: dict, prompts: list[dict], snippets: list[dict],
                     files: list[dict], briefs: list[dict] | None,
                     anchor_base: str = "./",
                     projects_in_day: list[str] | None = None,
                     interlude: dict | None = None,
                     month: str = "",
                     mood_label: str = "",
                     has_learning: bool = False,
                     tags: list[str] | None = None,
                     narration_generated_at: str = "",
                     docs_added: list[dict] | None = None,
                     known_docs: list[tuple[str, str]] | None = None,
                     known_topics: list[tuple[str, str]] | None = None,
                     open_loops_count: int = 0,
                     open_loops_items: list[dict] | None = None,
                     entities: list[dict] | None = None,
                     echoes: dict | None = None,
                     annotations: list[dict] | None = None) -> str:
    """Single day entry for the feed. Narration is hero; activity is disclosed.

    open_loops_count: number of open friction loops older than 7 days that
      touch any project active on this day. When > 0, a subtle indicator is
      shown in the entry header meta line linking to loops.html.

    echoes: temporal recall dict as returned by temporal.compute_all_echoes()
      for this date. When provided and non-empty, a subtle "memory" banner is
      rendered between the entry header and body. Omit (or pass None) for
      no banner.

    annotations: list of annotation dicts (from the annotations table) for this
      date. Each may carry a _contradiction boolean flag (set by the render-time
      contradiction guard) that triggers a warning icon in the UI. Annotation
      text is rendered verbatim — never passed through post_process linkification.
    """
    pretty = _pretty_date_safe(date)
    known_docs = known_docs or []
    known_topics = known_topics or []
    # Mood text is no longer surfaced in the header — the chip row carries
    # all meta-info-about-the-day. Keeping the underlying mood label as a
    # data attribute (data-mood) so the filter widget can still match on it.
    _ = mood  # currently unused; kept in the API for back-compat

    if narration:
        paragraphs = "".join(
            f"<p>{link_topic_titles(link_doc_titles(link_anchors(p.strip(), base_path=anchor_base), known_docs, base_path=anchor_base), known_topics, base_path=anchor_base)}</p>"
            for p in narration.split("\n\n") if p.strip()
        )
        body = f'<div class="entry-body">{paragraphs}</div>'
    elif interlude:
        body = render_interlude_block(interlude)
    elif counts_row and counts_row.get("events"):
        body = ('<div class="day-activity-only">'
                'A short day — not enough to pull a diary entry out of, but activity was recorded.'
                '</div>')
    else:
        body = '<div class="entry-empty">Nothing happened today.</div>'

    (header_chips_html, header_panels_html,
     inspect_row_html, panels_html) = _render_activity_disclosure(
        counts_row, prompts, snippets, files, briefs,
        entry_id=date,
        narration_generated_at=narration_generated_at,
        entities=entities,
        echoes=echoes,
        open_loops_items=open_loops_items or [],
        anchor_base=anchor_base,
    )
    # Note: the open-loops surface is now an inspect chip ("loops") inside
    # inspect_row_html, with a hybrid panel that lists this day's loops
    # plus a "view all on Loops page" link. The old top-of-entry loops
    # banner has been removed in favor of the chip.
    _ = open_loops_count  # legacy parameter; the chip uses items directly

    # Annotation blocks (below AI prose) — rendered from pre-loaded annotation rows.
    # The JS ANNOTATION_WIDGET also dynamically re-loads/updates these on save/delete.
    ann_list = annotations or []
    annotations_html = _render_annotation_block(ann_list)

    # Inline annotation form — always present in DOM, toggled open by JS.
    annotate_btn = (
        f'<button class="annotate-btn" data-scope="daily" '
        f'title="Add or view corrections for this entry">annotate</button>'
    )
    annotation_form = (
        f'<div class="annotation-form">'
        f'  <textarea placeholder="Your correction or note for this entry…"></textarea>'
        f'  <div class="annotation-form-row">'
        f'    <select title="Annotation type">'
        f'      <option value="append">append</option>'
        f'      <option value="correction">correction</option>'
        f'      <option value="override">override</option>'
        f'    </select>'
        f'    <button class="annotation-save-btn">Save</button>'
        f'    <button class="annotation-cancel-btn">Cancel</button>'
        f'    <span class="annotation-form-msg"></span>'
        f'  </div>'
        f'</div>'
    )

    projects_attr = ",".join(projects_in_day or [])
    week_attr = _iso_week_of(date)
    year = _pretty_date_year(date)
    year_html = f'<span class="year">{esc(year)}</span>' if year else ""
    entities_attr = ",".join(e["key"] for e in (entities or []))
    return (
        f'<article class="entry" id="{esc(date)}" '
        f'data-projects="{esc(projects_attr)}" data-week="{esc(week_attr)}" '
        f'data-month="{esc(month)}" data-year="{esc(year)}" '
        f'data-mood="{esc(mood_label)}" '
        f'data-learning="{"yes" if has_learning else "no"}" '
        f'data-tags="{esc(",".join(tags or []))}" '
        f'data-entities="{esc(entities_attr)}">'
        f'  <header class="entry-head">'
        f'    <h2><a href="#{esc(date)}" style="color:inherit;text-decoration:none;">{esc(pretty)} {year_html}</a></h2>'
        f'    <span class="meta">{header_chips_html}</span>'
        f'  </header>'
        f'  {header_panels_html}'
        f'  {body}'
        f'  {annotations_html}'
        f'  {annotation_form}'
        f'  {annotate_btn}'
        f'  {inspect_row_html}'
        f'  {panels_html}'
        f'</article>'
    )


def render_week_break(iso_week: str, rollup_prose: str, anchor_base: str = "./",
                      known_docs: list[tuple[str, str]] | None = None,
                      known_topics: list[tuple[str, str]] | None = None,
                      annotations: list[dict] | None = None) -> str:
    known_docs = known_docs or []
    known_topics = known_topics or []
    ann_html = _render_annotation_block(annotations or [])
    if rollup_prose:
        paragraphs = "".join(
            f"<p>{link_topic_titles(link_doc_titles(link_anchors(p.strip(), base_path=anchor_base), known_docs, base_path=anchor_base), known_topics, base_path=anchor_base)}</p>"
            for p in rollup_prose.split("\n\n") if p.strip()
        )
        rollup_html = f'<div class="week-rollup">{paragraphs}</div>'
    else:
        rollup_html = ""
    return (
        f'<div class="week-break" data-week="{esc(iso_week)}">'
        f'  — Week {esc(iso_week)} —'
        f'</div>'
        f'<div class="week-rollup-wrap" data-week="{esc(iso_week)}">{rollup_html}{ann_html}</div>'
        if rollup_html or ann_html else
        f'<div class="week-break" data-week="{esc(iso_week)}">'
        f'  — Week {esc(iso_week)} —'
        f'</div>'
    )


def render_month_break(year_month: str, rollup_prose: str, anchor_base: str = "./",
                       known_docs: list[tuple[str, str]] | None = None,
                       known_topics: list[tuple[str, str]] | None = None,
                       annotations: list[dict] | None = None) -> str:
    """Month divider + attached monthly retrospective, mirrors render_week_break."""
    known_docs = known_docs or []
    known_topics = known_topics or []
    ann_html = _render_annotation_block(annotations or [])
    try:
        pretty = datetime.strptime(year_month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        pretty = year_month
    if rollup_prose:
        paragraphs = "".join(
            f"<p>{link_topic_titles(link_doc_titles(link_anchors(p.strip(), base_path=anchor_base), known_docs, base_path=anchor_base), known_topics, base_path=anchor_base)}</p>"
            for p in rollup_prose.split("\n\n") if p.strip()
        )
        rollup_html = f'<div class="month-rollup">{paragraphs}</div>'
        return (
            f'<div class="month-break" data-month="{esc(year_month)}">'
            f'  ― {esc(pretty)} ―'
            f'</div>'
            f'<div class="month-rollup-wrap" data-month="{esc(year_month)}">{rollup_html}{ann_html}</div>'
        )
    if ann_html:
        return (
            f'<div class="month-break" data-month="{esc(year_month)}">'
            f'  ― {esc(pretty)} ―'
            f'</div>'
            f'<div class="month-rollup-wrap" data-month="{esc(year_month)}">{ann_html}</div>'
        )
    return (
        f'<div class="month-break" data-month="{esc(year_month)}">'
        f'  ― {esc(pretty)} ―'
        f'</div>'
    )


def render_site_header(*, site_title: str, subtitle: str,
                       projects: list[str] | None = None,
                       weeks: list[dict] | None = None,
                       months: list[dict] | None = None,
                       moods: list[dict] | None = None,
                       learnings: list[dict] | None = None,
                       years: list[dict] | None = None,
                       tags: list[dict] | None = None,
                       topic_pages: list[str] | None = None,
                       topic_pages_map: dict[str, str] | None = None,
                       arc_pages: list[str] | None = None,
                       entities: list[dict] | None = None) -> str:
    """The full site header — site title, subtitle, and filter bar — plus
    the window.__FILTERS__ data script. Used by the main feed page AND by
    every standalone deep-link page (weekly / monthly / topic / arc / doc)
    so navigation is consistent: the filter bar IS the navigation, and
    users return to any filtered view by clicking chips rather than
    following a back-arrow breadcrumb.

    entities: [{key, label, type}] for the entity filter axis, sorted by
      day-count descending. Each entity's `key` is the canonical_name (used
      as the data-entities attribute value on entries).

    Returns the data_script + header HTML as one string ready to prepend
    to the page body.
    """
    import json as _json
    has_any = bool(projects or weeks or months or moods or learnings or years or tags
                   or entities)
    filter_bar = ""
    if has_any:
        # Two visual rows in the filter bar:
        #   * filter-modes — entry-type filters (Find, Daily, Weekly,
        #     Monthly, Library) plus the trailing Clear chip. Everything
        #     here changes what's shown on the current page.
        #   * filter-nav — destination chips (Graph, Loops, Learnings,
        #     Echoes). Clicking these navigates to a standalone page.
        # Splitting them prevents users from confusing "filter the feed"
        # with "go to a different surface."
        filter_bar = (
            '<div class="filter-bar">'
            '  <div class="filter-row filter-modes" id="filter-modes"></div>'
            '  <div class="filter-row filter-nav" id="filter-nav"></div>'
            '  <div class="filter-row filter-axes" id="filter-axes"></div>'
            '  <div class="filter-row filter-options" id="filter-options"></div>'
            '</div>'
        )
    data_script = (
        f'<script>\n'
        f'window.__FILTERS__ = {_json.dumps({"projects": projects or [], "weeks": weeks or [], "months": months or [], "moods": moods or [], "learnings": learnings or [], "years": years or [], "tags": tags or [], "topic_pages": topic_pages or [], "topic_pages_map": topic_pages_map or {}, "arc_pages": arc_pages or [], "entities": entities or []})};\n'
        f'</script>'
    )
    # The site title links to Home from anywhere. On a deep-link page it
    # navigates back to the main feed. On the main feed itself it acts as
    # Clear: resets all filter chips AND scrolls to top, so a single click
    # always lands on a pristine, unfiltered home view. The reset reaches
    # into the chip widget via window.__resetFilters (defined in
    # FILTER_WIDGET).
    head = (
        f'<header class="site-head">'
        f'  <h1><a class="site-home-link" href="#" '
        f'onclick="event.preventDefault(); '
        f'var b=window.__ANCHOR_BASE__||\'./\'; '
        f'var p=(window.location.pathname||\'\').toLowerCase(); '
        f'var atIndex=(b===\'./\'||b===\'\')&&(p===\'/\'||p.endsWith(\'/index.html\')||p===\'\'); '
        f'if(atIndex){{ if(window.__resetFilters)window.__resetFilters(); window.scrollTo(0,0); }} '
        f'else {{ window.location.href=b+\'index.html\'; }}'
        f'">{esc(site_title)}</a></h1>'
        f'  <div class="sub">{esc(subtitle)}</div>'
        f'  {filter_bar}'
        f'</header>'
    )
    return data_script + head


def render_feed(entries_html: list[str], *, site_title: str, subtitle: str,
                projects: list[str] | None = None,
                weeks: list[dict] | None = None,
                months: list[dict] | None = None,
                moods: list[dict] | None = None,
                learnings: list[dict] | None = None,
                years: list[dict] | None = None,
                tags: list[dict] | None = None,
                topic_pages: list[str] | None = None,
                topic_pages_map: dict[str, str] | None = None,
                arc_pages: list[str] | None = None,
                entities: list[dict] | None = None,
                crumb_html: str = "") -> str:
    """Compose the feed page. Filtering is client-side via a breadcrumb
    chip bar — see FILTER_WIDGET for the runtime behavior.

    topic_pages: list of tag strings that have generated topic pages.
    topic_pages_map: {tag: slug} map so the JS can build correct hrefs.
    arc_pages: list of project names that have arc pages.
    entities: [{key, label, type}] for the entity filter axis.
    """
    header = render_site_header(
        site_title=site_title, subtitle=subtitle,
        projects=projects, weeks=weeks, months=months, moods=moods,
        learnings=learnings, years=years, tags=tags,
        topic_pages=topic_pages, topic_pages_map=topic_pages_map,
        arc_pages=arc_pages, entities=entities,
    )
    # Project-page crumb (optional) goes between the site header and the
    # feed. Crumbs on deep-link pages (weekly / monthly / topic / arc /
    # doc) were removed — the filter bar is the navigation.
    crumb_block = (
        f'<div class="project-crumb">{crumb_html}</div>' if crumb_html else ""
    )
    body_entries = "\n".join(entries_html) if entries_html else '<p style="text-align:center;color:var(--muted);">No entries yet.</p>'
    return header + crumb_block + '<main id="feed">' + body_entries + '</main><div class="filter-empty" id="filter-empty" style="display:none;">No entries match this filter.</div><footer>claudejournal</footer>'


def render_chat_page() -> str:
    """Kept for deep link, but main chat is the floating bubble."""
    return (
        '<header class="site-head"><h1>Ask the journal</h1>'
        '<div class="sub">The chat is also available on every page via the bubble in the corner.</div></header>'
        '<p style="text-align:center; color:var(--muted);">'
        'Click the <b>?</b> bubble in the bottom-right corner to start.</p>'
    )


def _render_backlinks_section(backlinks: list[dict]) -> str:
    """Render a 'Referenced from' section for topic, arc, and doc pages.

    Each item in backlinks is expected to have: source_scope, source_key,
    link_type, label, scope_label, url — as returned by backlinks.get_backlinks().
    Returns an empty string when the list is empty.
    """
    if not backlinks:
        return ""
    # Group by scope_label for a cleaner layout
    groups: dict[str, list[dict]] = {}
    for item in backlinks:
        groups.setdefault(item.get("scope_label", "Other"), []).append(item)

    rows_html = ""
    for scope_label, items in groups.items():
        links_html = "".join(
            f'<a href="{esc(item["url"])}" class="backlink-item">{esc(item["label"])}</a>'
            for item in items
        )
        rows_html += (
            f'<div class="backlink-group">'
            f'  <span class="backlink-scope">{esc(scope_label)}</span>'
            f'  <span class="backlink-list">{links_html}</span>'
            f'</div>'
        )
    return (
        f'<div class="backlinks-section">'
        f'  <h3 class="backlinks-heading">Referenced from</h3>'
        f'  {rows_html}'
        f'</div>'
    )


def render_topic_page(tag: str, prose: str, anchor_base: str = "../", *,
                      dates: list[str] | None = None,
                      projects: list[str] | None = None,
                      known_docs: list[tuple[str, str]] | None = None,
                      topic_slugs: dict[str, str] | None = None,
                      slug: str = "",
                      generated_at: str = "",
                      backlinks: list[dict] | None = None,
                      annotations: list[dict] | None = None) -> str:
    """Standalone topic wiki page. `prose` is human-readable narration.

    anchor_base: path from the topic page to the site root (default '../').
    dates: list of YYYY-MM-DD dates on which this tag appeared.
    projects: list of project display names involved with this tag.
    known_docs: (title, doc_id) pairs for doc linkification.
    topic_slugs: {tag: slug} mapping for topic linkification.
    annotations: user-authored annotations for this topic (Phase E v2).
    """
    from claudejournal.post_process import link_anchors, link_doc_titles

    known_docs = known_docs or []
    topic_slugs = topic_slugs or {}

    # Build prose HTML with post-processing (link_anchors + link_doc_titles +
    # link_topic_titles deferred to avoid circular import — post_process
    # will be extended in task 7).
    paragraphs_html = ""
    if prose:
        paras = [p.strip() for p in prose.split("\n\n") if p.strip()]
        processed = []
        for p in paras:
            h = link_doc_titles(link_anchors(p, base_path=anchor_base),
                                known_docs, base_path=anchor_base)
            # link_topic_titles applied after; imported lazily to avoid
            # circular import at module load time.
            try:
                from claudejournal.post_process import link_topic_titles
                tag_pairs = [(t, s) for t, s in topic_slugs.items() if t != tag]
                h = link_topic_titles(h, tag_pairs, base_path=anchor_base)
            except (ImportError, AttributeError):
                pass  # task 7 not yet landed — silently skip
            processed.append(f"<p>{h}</p>")
        paragraphs_html = "".join(processed)

    title = tag.replace("-", " ").title()

    meta_parts: list[str] = []
    if dates:
        meta_parts.append(f"{len(dates)} days")
    if projects:
        plist = esc(", ".join(sorted(projects)))
        meta_parts.append(f"projects: {plist}")
    if generated_at:
        meta_parts.append(f"updated {esc(generated_at[:10])}")
    meta_html = (
        f'<div class="topic-meta">{" · ".join(meta_parts)}</div>'
        if meta_parts else ""
    )

    # Audio play button — wired by TTS_WIDGET's MutationObserver, but it
    # doesn't know about topic pages yet; we embed the data-attr so future
    # wiring is trivial. The base filename matches audio.py's convention.
    audio_slug = slug or tag
    audio_html = (
        f'<span data-topic-slug="{esc(audio_slug)}" '
        f'data-audio-base="{esc(anchor_base)}audio/topic-{esc(audio_slug)}.wav"></span>'
    )

    # "View all entries" link — filters the main feed to this tag.
    import urllib.parse
    tag_encoded = urllib.parse.quote(tag, safe="")
    view_link = (
        f'<a href="{anchor_base}index.html#axis=topic&value={tag_encoded}">'
        f'View all entries tagged {esc(tag)}</a>'
    )

    # "Referenced from" backlinks section
    backlinks_html = _render_backlinks_section(backlinks or [])

    ann_html = _render_annotation_block(annotations or [])

    return (
        f'<article class="topic-page">'
        f'  <div class="topic-tag-label">topic</div>'
        f'  <h2>{esc(title)}</h2>'
        f'  {meta_html}'
        f'  {audio_html}'
        f'  <div class="topic-body">{paragraphs_html}</div>'
        f'  {ann_html}'
        f'  {backlinks_html}'
        f'  <div class="topic-footer">{view_link}</div>'
        f'</article>'
    )


def render_arc_page(project_name: str, prose: str, anchor_base: str = "../../", *,
                    first_date: str = "",
                    last_date: str = "",
                    session_count: int = 0,
                    top_tags: list[str] | None = None,
                    known_docs: list[tuple[str, str]] | None = None,
                    topic_slugs: dict[str, str] | None = None,
                    generated_at: str = "",
                    backlinks: list[dict] | None = None,
                    annotations: list[dict] | None = None) -> str:
    """Standalone project arc retrospective page.

    anchor_base: path from the arc page (out/projects/<name>/index.html)
    to the site root — default '../../'.
    """
    from claudejournal.post_process import link_anchors, link_doc_titles

    known_docs = known_docs or []
    topic_slugs = topic_slugs or {}

    paragraphs_html = ""
    if prose:
        paras = [p.strip() for p in prose.split("\n\n") if p.strip()]
        processed = []
        for p in paras:
            h = link_doc_titles(link_anchors(p, base_path=anchor_base),
                                known_docs, base_path=anchor_base)
            try:
                from claudejournal.post_process import link_topic_titles
                tag_pairs = list(topic_slugs.items())
                h = link_topic_titles(h, tag_pairs, base_path=anchor_base)
            except (ImportError, AttributeError):
                pass
            processed.append(f"<p>{h}</p>")
        paragraphs_html = "".join(processed)

    meta_parts: list[str] = []
    if first_date and last_date:
        if first_date == last_date:
            meta_parts.append(esc(first_date))
        else:
            meta_parts.append(f"{esc(first_date)} – {esc(last_date)}")
    if session_count:
        meta_parts.append(f"{session_count} sessions")
    if top_tags:
        tags_html = " ".join(f'<code>{esc(t)}</code>' for t in top_tags[:8])
        meta_parts.append(f"tags: {tags_html}")
    if generated_at:
        meta_parts.append(f"updated {esc(generated_at[:10])}")
    meta_html = (
        f'<div class="arc-meta">{" · ".join(meta_parts)}</div>'
        if meta_parts else ""
    )

    import urllib.parse
    name_encoded = urllib.parse.quote(project_name, safe="")
    view_link = (
        f'<a href="{anchor_base}index.html#axis=project&value={name_encoded}">'
        f'View all entries for {esc(project_name)}</a>'
    )

    backlinks_html = _render_backlinks_section(backlinks or [])

    ann_html = _render_annotation_block(annotations or [])

    return (
        f'<article class="arc-page">'
        f'  <div class="arc-tag-label">project arc</div>'
        f'  <h2>{esc(project_name)}</h2>'
        f'  {meta_html}'
        f'  <div class="arc-body">{paragraphs_html}</div>'
        f'  {ann_html}'
        f'  {backlinks_html}'
        f'  <div class="arc-footer">{view_link}</div>'
        f'</article>'
    )


def render_graph_page(node_count: int = 0, edge_count: int = 0) -> str:
    """Standalone force-directed link graph page.

    Loads graph.json (written by render.py) and renders a D3 force-directed
    visualization. Scope-colored nodes; click navigates to the target page.
    D3 is loaded from CDN; the page degrades gracefully if offline.

    node_count / edge_count are baked into the subtitle for the page header.
    """
    subtitle = ""
    if node_count or edge_count:
        subtitle = f"{node_count} nodes · {edge_count} edges"

    return f"""
<div class="graph-page">
  <div class="graph-header">
    <h2>Link Graph</h2>
    {"<p class='graph-meta'>" + esc(subtitle) + "</p>" if subtitle else ""}
    <p class="graph-legend">
      <span class="gl-dot gl-daily"></span>Daily
      <span class="gl-dot gl-topic"></span>Topic
      <span class="gl-dot gl-document"></span>Document
      <span class="gl-dot gl-project_arc"></span>Project
      <span class="gl-dot gl-weekly"></span>Weekly
      <span class="gl-dot gl-monthly"></span>Monthly
    </p>
    <p class="graph-hint">Click a node to open the page. Drag to explore. Scroll to zoom.</p>
    <div id="graph-filter-banner" class="graph-filter-banner" hidden></div>
  </div>
  <div id="graph-container">
    <svg id="graph-svg"></svg>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>
(function() {{
  const SCOPE_COLOR = {{
    daily:       '#c4a66a',
    project_day: '#b06a3a',
    weekly:      '#8a7f70',
    monthly:     '#6a5a4a',
    topic:       '#8a4a1f',
    project_arc: '#4a6a8a',
    document:    '#4d6a3a',
  }};

  // ── URL hash → active filters ────────────────────────────────────────
  // Two formats are honored. Both can appear at once; they intersect.
  //   Single-axis (chip widget format):  #axis=project&value=ChromaKey
  //   Multi-axis (graph-only format):    #project=ChromaKey&topic=architecture&year=2026
  // Multi-axis wins when both are present, since it's strictly more
  // expressive. Recognised axes: project, topic, year, month, week, entity.
  function parseFilters() {{
    const h = (window.location.hash || '').replace(/^#/, '');
    if (!h) return {{}};
    const params = {{}};
    h.split('&').forEach(kv => {{
      const eq = kv.indexOf('=');
      if (eq < 0) return;
      const k = decodeURIComponent(kv.slice(0, eq));
      const v = decodeURIComponent(kv.slice(eq + 1));
      params[k] = v;
    }});
    const KNOWN = ['project','topic','year','month','week','entity'];
    const filters = {{}};
    KNOWN.forEach(k => {{ if (params[k]) filters[k] = params[k]; }});
    if (params.axis && KNOWN.includes(params.axis) && params.value) {{
      // Don't overwrite if multi-axis already supplied that key.
      if (!filters[params.axis]) filters[params.axis] = params.value;
    }}
    return filters;
  }}

  function nodePassesFilters(node, filters) {{
    // A node passes only when EVERY active filter matches its metadata.
    // Nodes lacking a metadata key for an active filter are hidden — that
    // keeps the result a true intersection rather than a permissive union.
    for (const k in filters) {{
      if (node[k] === undefined || node[k] === null) return false;
      if (String(node[k]) !== String(filters[k])) return false;
    }}
    return true;
  }}

  // Display labels for filter axes — 'entity' surfaces as 'Tools' to match
  // the chip-widget label, others get title-cased.
  const _AXIS_DISPLAY = {{
    project: 'Project', topic: 'Topic', year: 'Year', month: 'Month',
    week: 'Week', entity: 'Tools',
  }};

  function buildFilterBanner(filters) {{
    const banner = document.getElementById('graph-filter-banner');
    if (!banner) return;
    const keys = Object.keys(filters);
    if (!keys.length) {{ banner.setAttribute('hidden', ''); return; }}
    const parts = keys.map(k => {{
      const label = _AXIS_DISPLAY[k] || (k.charAt(0).toUpperCase() + k.slice(1));
      return '<span class="gfb-pill">' + label + ': <strong>'
        + filters[k] + '</strong></span>';
    }});
    banner.innerHTML = 'Filtered: ' + parts.join(' ')
      + ' <a class="gfb-clear" href="./graph.html">clear filter</a>';
    banner.removeAttribute('hidden');
  }}

  const container = document.getElementById('graph-container');
  const svg = d3.select('#graph-svg');
  // Use viewport dimensions directly. clientWidth on the container can
  // disagree with the actual rendered width when the container is
  // breaking out of a constrained ancestor via translateX, and the
  // forceCenter math has to match what the SVG is *visually* showing.
  const width = window.innerWidth || container.clientWidth || 900;
  const height = Math.max(container.clientHeight || 600, 600);
  svg.attr('width', width).attr('height', height);

  const g = svg.append('g');

  // Zoom + pan
  const zoom = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // Tooltip
  const tooltip = d3.select('body').append('div')
    .attr('class', 'graph-tooltip')
    .style('opacity', 0)
    .style('position', 'fixed')
    .style('background', 'var(--paper,#fffaf0)')
    .style('border', '1px solid var(--rule,#ebe3d3)')
    .style('border-radius', '4px')
    .style('padding', '6px 10px')
    .style('font-size', '13px')
    .style('pointer-events', 'none')
    .style('z-index', '9999');

  const filters = parseFilters();
  buildFilterBanner(filters);

  fetch('./graph.json')
    .then(r => r.json())
    .then(graph => {{
      // Apply active filters by hiding non-matching nodes and any edges
      // that touch a hidden node. The simulation runs on the filtered
      // subset so the layout reflects what's actually shown.
      let nodes = graph.nodes;
      let edges = graph.edges;
      if (Object.keys(filters).length) {{
        const visible = new Set();
        nodes = nodes.filter(n => {{
          if (nodePassesFilters(n, filters)) {{ visible.add(n.id); return true; }}
          return false;
        }});
        edges = edges.filter(e => {{
          const sid = (typeof e.source === 'object') ? e.source.id : e.source;
          const tid = (typeof e.target === 'object') ? e.target.id : e.target;
          return visible.has(sid) && visible.has(tid);
        }});
        // Surface a "no matches" indicator if the intersection is empty.
        if (!nodes.length) {{
          document.getElementById('graph-container').innerHTML =
            '<p class="graph-empty">No nodes match the active filter. '
            + '<a href="./graph.html">Clear</a> to see the full graph.</p>';
          return;
        }}
      }}

      const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(edges)
          .id(d => d.id)
          .distance(60)
          .strength(0.4))
        .force('charge', d3.forceManyBody().strength(-120))
        .force('center', d3.forceCenter(width / 2, height / 2))
        .force('collision', d3.forceCollide(14));

      const link = g.append('g')
        .attr('class', 'links')
        .selectAll('line')
        .data(edges)
        .join('line')
        .attr('stroke', '#d0c8b8')
        .attr('stroke-opacity', 0.5)
        .attr('stroke-width', 1);

      const node = g.append('g')
        .attr('class', 'nodes')
        .selectAll('circle')
        .data(nodes)
        .join('circle')
        .attr('r', d => d.scope === 'daily' ? 5 : d.scope === 'topic' ? 8 : 6)
        .attr('fill', d => SCOPE_COLOR[d.scope] || '#aaa')
        .attr('stroke', '#fff')
        .attr('stroke-width', 1.5)
        .style('cursor', 'pointer')
        .on('mouseover', (event, d) => {{
          tooltip.style('opacity', 1)
            .html('<strong>' + d.label + '</strong><br><span style="color:#8a7f70">' + d.scope + '</span>');
        }})
        .on('mousemove', (event) => {{
          tooltip
            .style('left', (event.clientX + 14) + 'px')
            .style('top', (event.clientY - 8) + 'px');
        }})
        .on('mouseout', () => tooltip.style('opacity', 0))
        .on('click', (event, d) => {{
          if (d.url) window.location.href = d.url;
        }})
        .call(d3.drag()
          .on('start', (event, d) => {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          }})
          .on('drag', (event, d) => {{ d.fx = event.x; d.fy = event.y; }})
          .on('end', (event, d) => {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
          }})
        );

      simulation.on('tick', () => {{
        link
          .attr('x1', d => d.source.x)
          .attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x)
          .attr('y2', d => d.target.y);
        node
          .attr('cx', d => d.x)
          .attr('cy', d => d.y);
      }});
    }})
    .catch(err => {{
      document.getElementById('graph-container').innerHTML =
        '<p style="padding:20px;color:var(--warn)">Could not load graph.json: ' + err.message + '</p>';
    }});
}})();
</script>
<style>
/* Unboxed, full-bleed canvas: the SVG container breaks out of the .wrap
   max-width container so the visualization can span the full viewport,
   while the header (title, legend, filter banner) stays inside the
   centered reading column for legibility. The escape trick:
     left: 50%;  transform: translateX(-50%);  width: 100vw;
   pulls the element outward from any constrained ancestor without
   needing to touch .wrap globally.

   .graph-page also cancels the .wrap container's bottom padding so the
   canvas can reach the viewport bottom without a strip of empty space
   below it. negative bottom margin pulls the next sibling (footer)
   up to compensate. */
#graph-container {{
  position: relative; left: 50%; transform: translateX(-50%);
  width: 100vw;
  height: calc(100vh - 220px); min-height: 540px;
  background: transparent; overflow: hidden;
  margin-top: 4px;
  margin-bottom: 0;
}}
#graph-svg {{ width: 100%; height: 100%; display: block; }}
.graph-page {{
  max-width: none; margin: 0 auto;
  padding: 12px 16px 0;
  /* Cancel the .wrap container's padding-bottom (120px) on this page
     only, so empty space below the canvas doesn't look like a layout
     mistake. */
  margin-bottom: -120px;
}}
.graph-header {{ margin-bottom: 8px; max-width: 1100px; margin-left: auto; margin-right: auto; }}
.graph-header h2 {{ margin: 0 0 6px; font-size: 22px; font-weight: 500; }}
.graph-meta {{ margin: 0 0 8px; font-size: 12px; color: var(--muted);
  font-family: ui-monospace, Consolas, monospace; }}
.graph-hint {{ margin: 6px 0 10px; font-size: 12px; color: var(--muted); font-style: italic; }}
.graph-legend {{ margin: 0 0 8px; font-size: 12px; color: var(--muted);
  display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: center; }}
.graph-filter-banner {{
  margin: 6px 0 8px; font-size: 12px; color: var(--fg);
  font-family: ui-monospace, Consolas, monospace;
  padding: 5px 10px; background: var(--paper);
  border-left: 3px solid var(--accent, #8a4a1f); border-radius: 2px;
}}
.gfb-pill {{ margin-right: 12px; color: var(--muted); }}
.gfb-pill strong {{ color: var(--fg); }}
.gfb-clear {{ margin-left: 8px; color: var(--muted); text-decoration: underline; }}
.gfb-clear:hover {{ color: var(--fg); }}
.graph-empty {{
  padding: 40px 20px; text-align: center;
  color: var(--muted); font-style: italic;
}}
.gl-dot {{
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: 3px; vertical-align: middle;
}}
.gl-daily {{ background: #c4a66a; }}
.gl-topic {{ background: #8a4a1f; }}
.gl-document {{ background: #4d6a3a; }}
.gl-project_arc {{ background: #4a6a8a; }}
.gl-weekly {{ background: #8a7f70; }}
.gl-monthly {{ background: #6a5a4a; }}
.mode-graph {{ }}
</style>
"""


def render_loops_page(open_loops: list[dict], anchor_base: str = "./") -> str:
    """Standalone open-loops page (out/loops.html).

    open_loops: list of dicts as returned by openloops.compute_open_loops().
    Groups by project, sorted oldest-first within each group.
    Projects are sorted by the age of their oldest open loop (oldest first).
    """
    if not open_loops:
        return (
            '<div class="loops-page">'
            '  <h2>Open Loops</h2>'
            '  <div class="loops-meta">Unresolved friction items from session briefs.</div>'
            '  <div class="loops-empty">No open loops found — you\'ve resolved everything!</div>'
            '</div>'
        )

    # Group by project_id; sort projects by age of oldest loop desc
    from collections import defaultdict
    by_project: dict[str, list[dict]] = defaultdict(list)
    for loop in open_loops:
        by_project[loop["project_id"]].append(loop)

    # Sort projects: most-aged first (oldest unresolved friction at top)
    project_order = sorted(
        by_project.keys(),
        key=lambda pid: max(l["age_days"] for l in by_project[pid]),
        reverse=True,
    )

    total = len(open_loops)
    n_projects = len(by_project)
    meta = (
        f'{total} open loop{"s" if total != 1 else ""} '
        f'across {n_projects} project{"s" if n_projects != 1 else ""}'
    )

    groups_html = []
    for pid in project_order:
        loops_in_group = sorted(by_project[pid], key=lambda l: l["date"])
        proj_name = loops_in_group[0]["project_name"]

        items_html = []
        for loop in loops_in_group:
            age = loop["age_days"]
            age_str = f"{age}d open" if age < 60 else f"{age // 30}mo open"
            date_str = loop["date"]
            tags = loop.get("tags") or []
            tag_html = ""
            if tags:
                tag_html = (
                    '<span class="loop-tags">'
                    + "".join(f"<code>{esc(t)}</code>" for t in tags[:5])
                    + "</span>"
                )
            # data-date and data-friction drive the "Mark resolved" button (E7)
            friction_escaped = esc(loop["friction"])
            # Store friction text as a data attribute for the JS handler.
            # JSON-encode so special chars survive the HTML attribute.
            import json as _json
            friction_json = esc(_json.dumps(loop["friction"]))
            items_html.append(
                f'<div class="loop-item" data-date="{esc(date_str)}" data-friction={friction_json}>'
                f'  <div class="loop-text">{friction_escaped}</div>'
                f'  <div class="loop-footer">'
                f'    <span class="loop-age">{esc(age_str)}</span>'
                f'    <span class="loop-date">{esc(date_str)}</span>'
                f'    {tag_html}'
                f'    <button class="loop-resolve-btn" '
                f'            title="Mark this friction as manually resolved">'
                f'      resolved</button>'
                f'  </div>'
                f'</div>'
            )

        groups_html.append(
            f'<div class="loops-project-group">'
            f'  <div class="loops-project-heading">{esc(proj_name)}</div>'
            f'  {"".join(items_html)}'
            f'</div>'
        )

    # Inline JS for the "Mark resolved" buttons (Phase E7).
    # POSTs a 'correction' annotation with scope_tag='resolved' on the
    # originating daily entry. On success, hides the loop item immediately.
    # The loops page will correctly exclude this item on next re-render.
    resolve_js = (
        '<script>'
        '(function() {'
        '  document.querySelectorAll(".loop-resolve-btn").forEach(function(btn) {'
        '    btn.addEventListener("click", function() {'
        '      var item = btn.closest(".loop-item");'
        '      if (!item) return;'
        '      var date = item.dataset.date || "";'
        '      var frictionRaw = item.dataset.friction || "";'
        '      var friction;'
        '      try { friction = JSON.parse(frictionRaw); } catch(e) { friction = frictionRaw; }'
        '      if (!date || !friction) return;'
        '      btn.disabled = true;'
        '      btn.textContent = "saving…";'
        '      fetch("/api/annotations", {'
        '        method: "POST",'
        '        headers: {"Content-Type": "application/json"},'
        '        body: JSON.stringify({'
        '          scope: "daily", key: date,'
        '          annotation_type: "correction",'
        '          scope_tag: "resolved",'
        '          text: "Friction resolved: " + friction'
        '        })'
        '      })'
        '      .then(function(r) { return r.json(); })'
        '      .then(function(res) {'
        '        if (res.ok) {'
        '          item.style.transition = "opacity 0.3s";'
        '          item.style.opacity = "0";'
        '          setTimeout(function() { item.remove(); }, 320);'
        '        } else {'
        '          btn.disabled = false;'
        '          btn.textContent = "resolved";'
        '          alert("Error: " + (res.error || "unknown"));'
        '        }'
        '      })'
        '      .catch(function(e) {'
        '        btn.disabled = false;'
        '        btn.textContent = "resolved";'
        '        alert("Network error. Is the server running?");'
        '      });'
        '    });'
        '  });'
        '})();'
        '</script>'
    )
    return (
        f'<div class="loops-page">'
        f'  <h2>Open Loops</h2>'
        f'  <div class="loops-meta">{esc(meta)}</div>'
        f'  {"".join(groups_html)}'
        f'</div>'
        f'{resolve_js}'
    )


def render_learnings_page(learnings: list[dict], anchor_base: str = "./",
                          known_topics: list[tuple[str, str]] | None = None) -> str:
    """Standalone learnings page (out/learnings.html).

    learnings: list of dicts as returned by learnings.aggregate_learnings().
    Each dict: {text, dates, projects, tags, first_seen, times_seen}.
    Groups by primary tag cluster, sorted by recency (most recent first).
    The learning text passes through link_topic_titles so any topic mention
    becomes a link.
    """
    known_topics = known_topics or []

    if not learnings:
        return (
            '<div class="learnings-page">'
            '  <h2>Learnings</h2>'
            '  <div class="learnings-meta">Aggregated insights from session briefs.</div>'
            '  <div class="learnings-empty">No learnings recorded yet.</div>'
            '</div>'
        )

    from collections import defaultdict

    # Group by primary tag (first tag, or "untagged")
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for item in learnings:
        tags = item.get("tags") or []
        primary = tags[0] if tags else "untagged"
        by_tag[primary].append(item)

    # Sort tag groups by most-recent first_seen date
    def _group_recency(tag: str) -> str:
        return max((i["first_seen"] for i in by_tag[tag]), default="")

    tag_order = sorted(by_tag.keys(), key=_group_recency, reverse=True)

    total = len(learnings)
    n_tags = len(by_tag)
    meta = (
        f'{total} learning{"s" if total != 1 else ""} '
        f'across {n_tags} topic area{"s" if n_tags != 1 else ""}'
    )

    groups_html = []
    for tag in tag_order:
        items_in_group = sorted(by_tag[tag], key=lambda i: i["first_seen"], reverse=True)
        tag_label = tag.replace("-", " ").title() if tag != "untagged" else "Untagged"

        items_html = []
        for item in items_in_group:
            times = item.get("times_seen", 1)
            first = item.get("first_seen", "")
            projs = item.get("projects") or []
            proj_str = ", ".join(projs[:3]) + ("…" if len(projs) > 3 else "")

            # Apply topic linkification to the learning text
            raw_text = item["text"]
            try:
                linked_text = link_topic_titles(esc(raw_text), known_topics,
                                                base_path=anchor_base)
            except Exception:
                linked_text = esc(raw_text)

            count_str = f"seen {times}×" if times > 1 else "seen once"
            items_html.append(
                f'<div class="learning-item filterable">'
                f'  <div class="learning-text">{linked_text}</div>'
                f'  <div class="learning-footer">'
                f'    <span class="learning-count">{esc(count_str)}</span>'
                f'    <span class="learning-date">first: {esc(first[:10])}</span>'
                f'    {("<span class=\"learning-proj\">" + esc(proj_str) + "</span>") if proj_str else ""}'
                f'  </div>'
                f'</div>'
            )

        groups_html.append(
            f'<div class="learnings-tag-group">'
            f'  <div class="learnings-tag-heading">{esc(tag_label)}</div>'
            f'  {"".join(items_html)}'
            f'</div>'
        )

    return (
        f'<div class="learnings-page">'
        f'  <h2>Learnings</h2>'
        f'  <div class="learnings-meta">{esc(meta)}</div>'
        f'  <input class="inspect-search learnings-search" type="search" '
        f'placeholder="filter learnings..." '
        f'data-target="learnings-content" aria-label="filter learnings">'
        f'  <div class="inspect-content" id="learnings-content">'
        f'    {"".join(groups_html)}'
        f'    <div class="inspect-empty-match" hidden>No matches.</div>'
        f'  </div>'
        f'</div>'
    )


def render_echoes_page(echoes_by_date: dict[str, dict], anchor_base: str = "./",
                       known_topics: list[tuple[str, str]] | None = None) -> str:
    """Standalone temporal recall page (out/echoes.html).

    echoes_by_date: {date_str: echoes_dict} as returned by
      temporal.compute_all_echoes(). Only dates with at least one echo are
      present in the dict.

    Three sections are rendered:
      1. On This Day — prior-year same-month-day entries (most recent trigger date first)
      2. Recurring Friction — tags that have appeared with friction on 3+ dates
      3. Milestones — project anniversary markers that fell within the journal
    """
    from collections import defaultdict

    known_topics = known_topics or []

    if not echoes_by_date:
        return (
            '<div class="echoes-page">'
            '  <h2>Echoes</h2>'
            '  <div class="echoes-meta">Temporal patterns — prior years, recurring friction, project milestones.</div>'
            '  <div class="echoes-empty">No temporal patterns found yet — come back after a year of journaling.</div>'
            '</div>'
        )

    # ---- Collect and deduplicate across all trigger dates -------------------

    # prior-year: key = (trigger_date, prior_date) so we deduplicate if the
    # same pair would appear from multiple trigger dates (shouldn't happen often
    # but guard it).  Present newest trigger_date first.
    prior_pairs: list[dict] = []  # [{trigger_date, prior_date, snippet, year_diff}]
    seen_prior: set[tuple[str, str]] = set()
    for trigger_date in sorted(echoes_by_date.keys(), reverse=True):
        for py in echoes_by_date[trigger_date].get("prior_years") or []:
            key = (trigger_date, py["date"])
            if key in seen_prior:
                continue
            seen_prior.add(key)
            prior_pairs.append({
                "trigger_date": trigger_date,
                "prior_date": py["date"],
                "snippet": py.get("snippet", ""),
                "year_diff": py["year_diff"],
            })

    # recurring friction: key = tag; aggregate dates seen across all trigger dates
    rf_by_tag: dict[str, dict] = {}  # tag -> {count, dates(set), example_friction}
    for trigger_date, echoes in echoes_by_date.items():
        for rf in echoes.get("recurring_friction") or []:
            tag = rf["tag"]
            if tag not in rf_by_tag:
                rf_by_tag[tag] = {
                    "tag": tag,
                    "count": rf["count"],
                    "dates": set(rf["dates"]),
                    "example_friction": rf["example_friction"],
                }
            else:
                rf_by_tag[tag]["dates"].update(rf["dates"])
                if rf["count"] > rf_by_tag[tag]["count"]:
                    rf_by_tag[tag]["count"] = rf["count"]
                    rf_by_tag[tag]["example_friction"] = rf["example_friction"]
    rf_list = sorted(rf_by_tag.values(), key=lambda x: -x["count"])

    # milestones: key = (project, label) to deduplicate near-threshold hits
    ms_by_key: dict[tuple[str, str], dict] = {}
    for trigger_date, echoes in echoes_by_date.items():
        for ms in echoes.get("milestones") or []:
            key = (ms["project"], ms["label"])
            if key not in ms_by_key:
                ms_by_key[key] = dict(ms, trigger_date=trigger_date)
    ms_list = sorted(ms_by_key.values(), key=lambda x: x.get("trigger_date", ""), reverse=True)

    # ---- Build HTML ---------------------------------------------------------

    sections: list[str] = []
    total_signals = len(prior_pairs) + len(rf_list) + len(ms_list)
    meta = (
        f'{total_signals} signal{"s" if total_signals != 1 else ""}: '
        f'{len(prior_pairs)} prior-year entr{"ies" if len(prior_pairs) != 1 else "y"}, '
        f'{len(rf_list)} recurring friction pattern{"s" if len(rf_list) != 1 else ""}, '
        f'{len(ms_list)} milestone{"s" if len(ms_list) != 1 else ""}'
    )

    # Section 1: On This Day
    if prior_pairs:
        cards = []
        for pp in prior_pairs[:30]:  # cap at 30 to keep page manageable
            trigger = pp["trigger_date"]
            prior = pp["prior_date"]
            yr_diff = pp["year_diff"]
            label = f"{yr_diff} year{'s' if yr_diff != 1 else ''} ago"
            snippet = pp["snippet"]
            snippet_html = (
                f'<div class="echo-card-body">{esc(snippet)}</div>'
                if snippet else ""
            )
            cards.append(
                f'<div class="echo-card">'
                f'  <div class="echo-card-title">'
                f'    <a href="{anchor_base}#{esc(prior)}">{esc(prior)}</a>'
                f'    &mdash; {esc(label)}'
                f'  </div>'
                f'  {snippet_html}'
                f'  <div class="echo-card-footer">'
                f'    <span>triggered on {esc(trigger)}</span>'
                f'  </div>'
                f'</div>'
            )
        sections.append(
            f'<div class="echoes-section">'
            f'  <div class="echoes-section-heading">On This Day</div>'
            f'  {"".join(cards)}'
            f'</div>'
        )

    # Section 2: Recurring Friction
    if rf_list:
        cards = []
        for rf in rf_list[:20]:
            tag = rf["tag"]
            count = rf["count"]
            example = rf["example_friction"]
            all_dates = sorted(rf["dates"])
            date_range = f"{all_dates[0]} – {all_dates[-1]}" if len(all_dates) > 1 else all_dates[0] if all_dates else ""
            # Apply topic linkification to the friction example
            try:
                example_html = link_topic_titles(esc(example), known_topics, base_path=anchor_base)
            except Exception:
                example_html = esc(example)
            cards.append(
                f'<div class="echo-card echo-friction-card">'
                f'  <div class="echo-card-title">'
                f'    <code>{esc(tag)}</code> — friction on {count} date{"s" if count != 1 else ""}'
                f'  </div>'
                f'  <div class="echo-card-body">{example_html}</div>'
                f'  <div class="echo-card-footer">'
                f'    <span>{esc(date_range)}</span>'
                f'  </div>'
                f'</div>'
            )
        sections.append(
            f'<div class="echoes-section">'
            f'  <div class="echoes-section-heading">Recurring Friction</div>'
            f'  {"".join(cards)}'
            f'</div>'
        )

    # Section 3: Milestones
    if ms_list:
        cards = []
        for ms in ms_list[:20]:
            proj_name = ms["project_name"]
            label = ms["label"]
            trigger = ms.get("trigger_date", "")
            first_seen = ms.get("first_seen", "")
            cards.append(
                f'<div class="echo-card echo-milestone-card">'
                f'  <div class="echo-card-title">{esc(proj_name)} — {esc(label)}</div>'
                f'  <div class="echo-card-footer">'
                f'    <span>first seen {esc(first_seen)}</span>'
                f'    {("<span>near " + esc(trigger) + "</span>") if trigger else ""}'
                f'  </div>'
                f'</div>'
            )
        sections.append(
            f'<div class="echoes-section">'
            f'  <div class="echoes-section-heading">Project Milestones</div>'
            f'  {"".join(cards)}'
            f'</div>'
        )

    if not sections:
        sections_html = '<div class="echoes-empty">No patterns to surface yet.</div>'
    else:
        sections_html = "".join(sections)

    return (
        f'<div class="echoes-page">'
        f'  <h2>Echoes</h2>'
        f'  <div class="echoes-meta">{esc(meta)}</div>'
        f'  {sections_html}'
        f'</div>'
    )
