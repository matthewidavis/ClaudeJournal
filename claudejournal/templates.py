"""Warm diary templates — feed-first, centered reading column, floating chat."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable

from claudejournal.post_process import link_anchors


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
.entry-head .meta {
  color: var(--muted); font-size: 12px;
  font-family: ui-monospace, Consolas, monospace; white-space: nowrap;
}
.entry-head .meta .mood { color: var(--accent-soft); font-style: italic; }

/* TTS — per-entry play buttons + floating bubble */
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
.tts-play.loading { opacity: 0.6; cursor: wait; }
.tts-bubble {
  position: fixed; left: 20px; bottom: 20px; z-index: 1000;
  width: 44px; height: 44px; border-radius: 50%;
  background: var(--paper); border: 1px solid var(--rule);
  color: var(--accent); cursor: pointer; box-shadow: var(--shadow);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; transition: transform 0.15s, background 0.15s;
}
.tts-bubble:hover { transform: scale(1.06); background: var(--chip); }
.tts-bubble.playing { background: var(--accent); color: var(--paper); border-color: var(--accent); }
.tts-play.paused { background: var(--accent-soft); color: var(--paper); border-color: var(--accent-soft); }
.tts-restart-inline {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; margin-left: 4px; padding: 0;
  border: 1px solid var(--rule); border-radius: 50%;
  background: var(--paper); color: var(--accent-soft); cursor: pointer;
  font-size: 11px; line-height: 1; vertical-align: 0.15em;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.tts-restart-inline:hover { border-color: var(--accent); color: var(--accent); background: var(--chip); }

/* Scrub bar — slotted below the entry header while that entry is active */
.tts-scrub {
  display: flex; align-items: center; gap: 10px;
  margin: 4px 0 14px; padding: 6px 10px;
  background: var(--chip); border-radius: 20px;
  font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  color: var(--muted);
}
.tts-scrub input[type=range] {
  flex: 1; height: 4px; -webkit-appearance: none; appearance: none;
  background: var(--rule); border-radius: 2px; outline: none; cursor: pointer;
}
.tts-scrub input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none; width: 12px; height: 12px;
  border-radius: 50%; background: var(--accent); cursor: pointer;
}
.tts-scrub input[type=range]::-moz-range-thumb {
  width: 12px; height: 12px; border: none; border-radius: 50%;
  background: var(--accent); cursor: pointer;
}
.tts-scrub .tts-time { white-space: nowrap; }

/* Sentence-follow highlight — noticeable but still warm and paper-like. */
.tts-sentence {
  transition: background-color 0.2s ease, box-shadow 0.2s ease;
  border-radius: 3px;
  padding: 0 2px;
  margin: 0 -2px;
}
.tts-sentence.tts-active {
  background-color: #f5e6a8;   /* warm highlighter yellow, tuned for the paper palette */
  box-shadow: 0 0 0 2px #f5e6a8;
}
.tts-bubble.downloading {
  background: conic-gradient(var(--accent-soft) var(--tts-pct, 0%), var(--paper) 0);
  color: var(--accent);
}
.tts-bubble.downloading::after {
  content: attr(data-pct);
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  font-size: 9px; font-family: ui-monospace, Consolas, monospace;
  color: var(--accent); background: var(--paper);
  border-radius: 50%; width: 30px; height: 30px;
  display: flex; align-items: center; justify-content: center;
}
.tts-panel {
  position: fixed; left: 20px; bottom: 76px; z-index: 1000;
  width: 260px; padding: 14px; background: var(--paper);
  border: 1px solid var(--rule); border-radius: 10px;
  box-shadow: var(--shadow); display: none;
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
}
.tts-panel.open { display: block; }
.tts-panel h4 { margin: 0 0 10px; font-size: 13px; font-weight: 600; color: var(--fg); font-family: inherit; }
.tts-panel label { display: block; margin: 8px 0 4px; color: var(--muted); }
.tts-panel select {
  width: 100%; padding: 4px 6px; border: 1px solid var(--rule);
  border-radius: 4px; background: var(--bg); color: var(--fg);
  font-family: inherit; font-size: 12px;
}
.tts-panel button {
  width: 100%; margin-top: 10px; padding: 6px; cursor: pointer;
  background: var(--accent); color: var(--paper); border: none;
  border-radius: 4px; font-family: inherit; font-size: 12px;
}
.tts-panel button.secondary { background: var(--paper); color: var(--fg); border: 1px solid var(--rule); }
.tts-panel .tts-status { margin-top: 10px; color: var(--muted); font-size: 11px; min-height: 14px; }
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
  display: flex; flex-wrap: wrap; gap: 6px;
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

/* Ask pill — fixed top-left. Primary action, always reachable. */
#chat-fab {
  position: fixed; top: 18px; left: 18px; z-index: 40;
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


FILTER_WIDGET = """
<script>
(function() {
  const data = window.__FILTERS__ || {projects: [], weeks: [], months: [], moods: [], learnings: [], years: [], tags: []};
  if (!data.projects.length && !data.weeks.length && !data.months.length
      && !data.moods.length && !data.learnings.length && !data.years.length
      && !(data.tags || []).length) return;
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
  const state = {axis: null, value: null, views: loadViews()};
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
  const AXIS_LABELS = {project: 'Project', topic: 'Topic', year: 'Year', month: 'Month', week: 'Week', mood: 'Mood', learning: 'Aha moment', search: 'Find'};
  // Entry-type filter. 'all' shows everything; the others hide the two
  // element kinds that don't match (dailies are <article.entry>, weeklies
  // live in .week-rollup-wrap, monthlies in .month-rollup-wrap).
  const VIEW_KEYS = ['daily', 'weekly', 'monthly'];
  const VIEW_LABELS = {daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly'};
  const AXIS_KEYS = ['project', 'topic', 'year', 'month', 'week', 'mood', 'learning'];

  const poolFor = (axis) => {
    if (axis === 'project') return data.projects;
    if (axis === 'topic') return data.tags || [];
    if (axis === 'learning') return data.learnings || [];
    if (axis === 'search') return ['__search__'];  // pseudo — chip appears
    return data[axis + 's'] || [];
  };

  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function render() {
    if (modesRow) clearChildren(modesRow);
    clearChildren(axesRow);
    clearChildren(options);

    // --- Row 0: "mode" chips. Find (search) sits here alongside the
    // entry-type toggle (Daily/Weekly/Monthly). Selecting a view chip is
    // exclusive — clicking the active one reverts to 'all'. ---
    if (modesRow) {
      const findActive = state.axis === 'search';
      modesRow.appendChild(makeChip(AXIS_LABELS.search, 'mode axis-search' + (findActive ? ' active' : ''), () => {
        if (state.axis === 'search') { state.axis = null; state.value = null; }
        else { state.axis = 'search'; state.value = null; }
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
    }

    // --- Axis row: always present, stable positions. Click toggles active. ---
    AXIS_KEYS.forEach(k => {
      const pool = poolFor(k);
      if (!pool.length) return;
      const isActive = state.axis === k;
      const cls = 'axis axis-' + k + (isActive ? ' active' : '');
      const chip = makeChip(AXIS_LABELS[k], cls, () => {
        if (state.axis === k) { state.axis = null; state.value = null; }
        else { state.axis = k; state.value = null; }
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

    if (state.value) {
      // Show the selected value as a single active sub-chip (click to clear).
      let label = state.value;
      const lookup = {week: data.weeks, month: data.months, mood: data.moods, learning: data.learnings, year: data.years, topic: data.tags};
      const pool = lookup[state.axis];
      if (pool) {
        const hit = pool.find(x => x.key === state.value);
        if (hit) label = hit.label;
      }
      options.appendChild(makeChip(label, 'active', () => {
        state.value = null; apply();
      }));
    } else {
      // Build the list of choices. Long lists get an inline filter input
      // and a scrollable container so a 100-tag Topic pool stays tidy.
      const raw = (state.axis === 'project')
        ? data.projects.map(p => ({ key: p, label: p }))
        : poolFor(state.axis);
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
          const shown = raw.filter(x => !q || x.label.toLowerCase().includes(q));
          shown.forEach(x => list.appendChild(makeChip(x.label, '',
            () => { state.value = x.key; apply(); })));
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
        raw.forEach(x => options.appendChild(makeChip(x.label, '',
          () => { state.value = x.key; apply(); })));
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
    const entries = feed.querySelectorAll('article.entry');
    // Empty selection = show all — so the user can deselect every chip and
    // land on the same view as selecting every chip. Intuition wins over
    // forcing "at least one".
    const viewShows = v => state.views.size === 0 || state.views.has(v);
    const viewHidesDailies = !viewShows('daily');
    entries.forEach(el => {
      // Restore pristine HTML when ANY highlight-producing state changes.
      const restore = searching || wasSearching || highlightingTopic || wasHighlightingTopic;
      if (restore && origCache.has(el)) {
        el.innerHTML = origCache.get(el);
      }

      if (viewHidesDailies) {
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
    if (state.value) parts.push('value=' + encodeURIComponent(state.value));
    const hash = parts.length ? '#' + parts.join('&') : '';
    if (location.hash !== hash) history.replaceState(null, '', location.pathname + hash);
  }
  function apply() { render(); applyVisibility(); syncUrl(); }
  function parseHash() {
    const h = location.hash.replace(/^#/, '');
    if (!h) return;
    const params = Object.fromEntries(h.split('&').map(kv => {
      const [k, v] = kv.split('=');
      return [k, v ? decodeURIComponent(v) : ''];
    }));
    // Only adopt filter-related hashes; date anchors like #2026-04-12 are ignored here.
    if (['project','week','month','mood','learning','search','year','topic'].includes(params.axis)) {
      state.axis = params.axis;
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
<button class="tts-bubble" id="tts-bubble" title="Read aloud" aria-label="Read aloud">🔊</button>
<div class="tts-panel" id="tts-panel" role="dialog" aria-label="Text to speech">
  <h4>Read aloud</h4>
  <label for="tts-voice">Voice</label>
  <select id="tts-voice"></select>
  <button id="tts-read-visible" type="button">Read what's on screen</button>
  <button id="tts-stop" type="button" class="secondary">Stop</button>
  <div class="tts-status" id="tts-status"></div>
</div>
<script type="module">
// VITS-web TTS — runs fully in-browser via ONNX/WASM. Model files are
// fetched from HuggingFace on first play and cached in OPFS by the library.
const VOICES = [
  {id: "en_US-libritts-high",        label: "LibriTTS (high) — default"},
  {id: "en_US-libritts_r-medium",    label: "LibriTTS-R (medium)"},
  {id: "en_US-hfc_female-medium",    label: "HFC female"},
  {id: "en_US-hfc_male-medium",      label: "HFC male"},
  {id: "en_US-amy-medium",           label: "Amy"},
  {id: "en_US-ryan-high",            label: "Ryan (high)"},
  {id: "en_US-lessac-high",          label: "Lessac (high)"},
  {id: "en_GB-alan-medium",          label: "Alan UK"},
  {id: "en_GB-jenny_dioco-medium",   label: "Jenny UK"},
];
const DEFAULT_VOICE = "en_US-libritts-high";
const LS_KEY = "claudejournal.tts.voice";

const state = {
  tts: null,        // lazy-loaded vits-web module
  audio: null,      // current HTMLAudioElement
  queue: [],        // pending {text, button}
  current: null,    // current {text, button}
  voice: DEFAULT_VOICE,
  stopToken: 0,
  audioGen: 0,
  paused: false,
  pendingPause: false, // set by restartSession when user was paused — honored after play()
  session: null,       // snapshot of the last fresh enqueue — lets Restart replay
  sourceButton: null,  // the .tts-play that started the current session (or null for "read visible")
  trackerTeardown: null,
};
// Migrate: if stored voice isn't in our list, fall back to default.
const _saved = localStorage.getItem(LS_KEY);
if (_saved && VOICES.some(v => v.id === _saved)) state.voice = _saved;
else localStorage.setItem(LS_KEY, DEFAULT_VOICE);

const $status  = document.getElementById("tts-status");
const $voice   = document.getElementById("tts-voice");
const $bubble  = document.getElementById("tts-bubble");
const $panel   = document.getElementById("tts-panel");
// The big bubble stays a speaker icon. Per-entry ▶ buttons morph into
// pause/resume, and grow an inline ↻ restart sibling while active.
function setBubbleIcon() {
  const src = state.sourceButton;
  // Clean non-source buttons
  document.querySelectorAll(".tts-play").forEach(btn => {
    if (btn !== src) {
      btn.textContent = "▶";
      btn.classList.remove("paused", "playing");
      btn.title = "Read this entry";
      // Remove any stale restart siblings
      const sib = btn.nextElementSibling;
      if (sib && sib.classList.contains("tts-restart-inline")) sib.remove();
    }
  });
  if (!src) return;
  if (state.paused) { src.textContent = "▶"; src.title = "Resume"; src.classList.add("paused"); src.classList.remove("playing"); }
  else              { src.textContent = "⏸"; src.title = "Pause";  src.classList.remove("paused"); src.classList.add("playing"); }
  // Ensure an inline restart button sits next to the source button
  let restart = src.nextElementSibling;
  if (!restart || !restart.classList.contains("tts-restart-inline")) {
    restart = document.createElement("button");
    restart.type = "button";
    restart.className = "tts-restart-inline";
    restart.title = "Restart from beginning";
    restart.setAttribute("aria-label", "Restart");
    restart.textContent = "↻";
    restart.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); restartSession(); });
    src.insertAdjacentElement("afterend", restart);
  }
}

// Populate voice picker
for (const v of VOICES) {
  const opt = document.createElement("option");
  opt.value = v.id; opt.textContent = v.label;
  if (v.id === state.voice) opt.selected = true;
  $voice.appendChild(opt);
}
// Surface environment issues up front — vits-web needs cross-origin
// isolation for multi-threaded wasm + OPFS for model caching.
(function envCheck() {
  const problems = [];
  if (!window.crossOriginIsolated) problems.push("page is not cross-origin isolated (COOP/COEP missing) — restart 'claudejournal serve'");
  if (!window.isSecureContext) problems.push("not a secure context (need https or localhost)");
  if (!navigator.storage?.getDirectory) problems.push("OPFS unavailable in this browser");
  if (problems.length) {
    const msg = "TTS may not work: " + problems.join("; ");
    console.warn("[TTS]", msg);
    setTimeout(() => { if (!$status.textContent) setStatus(msg); }, 50);
  }
})();

$voice.addEventListener("change", () => {
  state.voice = $voice.value;
  localStorage.setItem(LS_KEY, state.voice);
  setStatus("Voice: " + state.voice);
});

function setStatus(msg) { $status.textContent = msg || ""; }

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Mount a scrub bar + sentence tracker for the currently-sourced entry.
// Returns a teardown function.
function mountTracker(sourceBtn, audio) {
  if (!sourceBtn) return () => {};
  const entry = sourceBtn.closest("article.entry, .week-break");
  if (!entry) return () => {};
  const body =
    (entry.matches("article.entry") && entry.querySelector(".entry-body")) ||
    (entry.classList.contains("week-break") && entry.nextElementSibling?.querySelector(".week-rollup")) ||
    entry;

  // ---- Sentence wrap (once) — for the highlight follower ----
  const sentences = [];  // [{el, start, end}] in character offsets
  if (body && !body.dataset.ttsWrapped) {
    wrapSentences(body, sentences);
    body.dataset.ttsWrapped = "1";
    body._ttsSentences = sentences;
  } else if (body && body._ttsSentences) {
    sentences.push(...body._ttsSentences);
  }
  const totalChars = sentences.length ? sentences[sentences.length - 1].end : 0;

  // ---- Scrub bar ----
  const wrap = document.createElement("div");
  wrap.className = "tts-scrub";
  wrap.innerHTML = `
    <span class="tts-time tts-cur">0:00</span>
    <input type="range" min="0" max="1000" value="0" step="1" aria-label="Scrub">
    <span class="tts-time tts-dur">0:00</span>
  `;
  // Place scrub bar directly after the entry header
  const header = entry.matches(".week-break") ? entry : entry.querySelector(".entry-head");
  header.insertAdjacentElement("afterend", wrap);

  const $cur = wrap.querySelector(".tts-cur");
  const $dur = wrap.querySelector(".tts-dur");
  const $rng = wrap.querySelector("input[type=range]");

  // Scrub handling. Use `input` (fires for both drag and click-to-set), and
  // additionally suppress `timeupdate`-driven updates while the range is
  // focused so the thumb doesn't snap back under the user's mouse.
  let seeking = false;
  let seekTimer = 0;
  const commitSeek = () => {
    if (!isFinite(audio.duration) || audio.duration <= 0) return;
    const target = (parseFloat($rng.value) / 1000) * audio.duration;
    if (isFinite(target)) {
      console.log("[TTS] scrub seek ->", target.toFixed(2), "s");
      try { audio.currentTime = target; } catch (e) { console.error("[TTS] seek failed", e); }
    }
  };
  const onInput = () => {
    seeking = true;
    // Debounce: during fast drags, only commit every 60ms. Click-to-set
    // fires once so it commits immediately after the timer.
    clearTimeout(seekTimer);
    seekTimer = setTimeout(() => { commitSeek(); seeking = false; }, 60);
  };
  const onPointerDown = () => { seeking = true; };
  const onPointerUp = () => {
    clearTimeout(seekTimer);
    commitSeek();
    seeking = false;
  };
  $rng.addEventListener("input", onInput);
  $rng.addEventListener("pointerdown", onPointerDown);
  $rng.addEventListener("pointerup", onPointerUp);
  // Don't let clicks on the scrub area bubble up to anything that might
  // restart playback.
  wrap.addEventListener("click", (ev) => ev.stopPropagation());

  let activeIdx = -1;
  const clearActive = () => {
    if (activeIdx >= 0 && sentences[activeIdx]) sentences[activeIdx].el.classList.remove("tts-active");
    activeIdx = -1;
  };
  const onTime = () => {
    const d = audio.duration;
    if (isFinite(d) && d > 0) {
      if (!seeking) $rng.value = String(Math.round((audio.currentTime / d) * 1000));
      $cur.textContent = fmtTime(audio.currentTime);
      $dur.textContent = fmtTime(d);
      if (totalChars > 0) {
        const charPos = (audio.currentTime / d) * totalChars;
        let idx = -1;
        for (let i = 0; i < sentences.length; i++) {
          if (charPos >= sentences[i].start && charPos < sentences[i].end) { idx = i; break; }
        }
        if (idx !== activeIdx) {
          clearActive();
          activeIdx = idx;
          if (idx >= 0) sentences[idx].el.classList.add("tts-active");
        }
      }
    }
  };
  const onMeta = () => { if (isFinite(audio.duration)) $dur.textContent = fmtTime(audio.duration); };
  audio.addEventListener("timeupdate", onTime);
  audio.addEventListener("loadedmetadata", onMeta);

  return () => {
    clearTimeout(seekTimer);
    audio.removeEventListener("timeupdate", onTime);
    audio.removeEventListener("loadedmetadata", onMeta);
    clearActive();
    wrap.remove();
  };
}

// Wrap each sentence in an entry body in a <span.tts-sentence>. Tracks
// character offsets (with punctuation, spacing) so we can map audio time
// to a sentence by proportional char position — good enough for Piper.
function wrapSentences(root, out) {
  // Collect text nodes in DFS order (skip nested inspect panels / buttons)
  const skip = new Set(["BUTTON", "SCRIPT", "STYLE"]);
  const texts = [];
  const walk = (n) => {
    if (!n) return;
    if (n.nodeType === 3) { texts.push(n); return; }
    if (n.nodeType !== 1) return;
    if (skip.has(n.tagName) || n.classList.contains("inspect-panel") ||
        n.classList.contains("inspect-chip") || n.classList.contains("meta")) return;
    for (const c of [...n.childNodes]) walk(c);
  };
  walk(root);
  if (!texts.length) return;
  // Sentence split that keeps the punctuation attached.
  const sentRx = /[^.!?\\n]+[.!?]+[\\s"')\\]]*|[^.!?\\n]+$/g;
  let charOffset = 0;
  for (const tn of texts) {
    const raw = tn.nodeValue;
    if (!raw) continue;
    const frag = document.createDocumentFragment();
    let m;
    sentRx.lastIndex = 0;
    let consumed = 0;
    while ((m = sentRx.exec(raw)) !== null) {
      // Leading whitespace between matches
      if (m.index > consumed) {
        frag.appendChild(document.createTextNode(raw.slice(consumed, m.index)));
        charOffset += (m.index - consumed);
      }
      const span = document.createElement("span");
      span.className = "tts-sentence";
      span.textContent = m[0];
      const start = charOffset;
      charOffset += m[0].length;
      const end = charOffset;
      out.push({ el: span, start, end });
      frag.appendChild(span);
      consumed = m.index + m[0].length;
    }
    if (consumed < raw.length) {
      frag.appendChild(document.createTextNode(raw.slice(consumed)));
      charOffset += (raw.length - consumed);
    }
    tn.parentNode.replaceChild(frag, tn);
  }
}

// Split into sentence-sized chunks so one bad token can't kill a whole
// entry and so playback starts sooner.
// Piper's phonemizer + ONNX session can comfortably synthesize long runs
// in one shot (the vits-web demo uses ~3000 chars). Larger chunks = fewer
// gaps. We still chunk for lookahead + error isolation.
function splitForTTS(text, maxLen = 2800) {
  const sentences = text.match(/[^.!?\\n]+[.!?]+|[^.!?\\n]+$/g) || [text];
  const out = [];
  let buf = "";
  for (const s of sentences) {
    const sp = s.trim();
    if (!sp) continue;
    if ((buf + " " + sp).trim().length > maxLen) {
      if (buf) out.push(buf.trim());
      buf = sp;
    } else {
      buf = (buf ? buf + " " : "") + sp;
    }
  }
  if (buf.trim()) out.push(buf.trim());
  return out;
}

async function loadTTS() {
  if (state.tts) return state.tts;
  setStatus("Loading TTS engine…");
  state.tts = await import("https://cdn.jsdelivr.net/npm/@diffusionstudio/vits-web@1.0.3/+esm");
  return state.tts;
}

// Strip anchors like [2026-04-12], normalize unicode punctuation, and
// force ASCII — the LibriTTS model's phoneme vocab rejects IDs produced
// for exotic characters (curly quotes, em dashes, ellipsis, etc).
function cleanText(el) {
  const clone = el.cloneNode(true);
  clone.querySelectorAll(".inspect-panel, .inspect-chip, .tts-play, button, .meta").forEach(n => n.remove());
  let t = clone.textContent || "";
  t = t.replace(/\\[\\d{4}-\\d{2}-\\d{2}\\]/g, "");
  // Unicode punctuation → ASCII equivalents
  t = t.normalize("NFKD");
  t = t.replace(/[\\u2018\\u2019\\u201A\\u201B\\u2032]/g, "'");
  t = t.replace(/[\\u201C\\u201D\\u201E\\u201F\\u2033]/g, '"');
  t = t.replace(/[\\u2013\\u2014\\u2015]/g, "-");
  t = t.replace(/[\\u2026]/g, "...");
  t = t.replace(/[\\u00A0\\u2007\\u202F]/g, " ");
  t = t.replace(/[\\u2022\\u00B7]/g, ",");
  // Drop combining marks left by NFKD, then anything non-ASCII.
  t = t.replace(/[\\u0300-\\u036f]/g, "");
  t = t.replace(/[^\\x20-\\x7e\\n]/g, " ");
  t = t.replace(/\\s+/g, " ").trim();
  return t;
}

function setDownloadPct(pct) {
  if (pct == null) {
    $bubble.classList.remove("downloading");
    $bubble.style.removeProperty("--tts-pct");
    $bubble.removeAttribute("data-pct");
    return;
  }
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  $bubble.classList.add("downloading");
  $bubble.style.setProperty("--tts-pct", p + "%");
  $bubble.setAttribute("data-pct", p + "%");
}

async function synth(text) {
  const tts = await loadTTS();
  setStatus("Synthesizing…");
  const totals = new Map(); // url -> total bytes
  const loaded = new Map(); // url -> bytes loaded
  const onProgress = (ev) => {
    if (!ev || !ev.url) return;
    if (typeof ev.total === "number" && ev.total > 0) totals.set(ev.url, ev.total);
    if (typeof ev.loaded === "number") loaded.set(ev.url, ev.loaded);
    let t = 0, l = 0;
    for (const v of totals.values()) t += v;
    for (const [u, v] of loaded) l += Math.min(v, totals.get(u) || v);
    if (t > 0) {
      const pct = (l / t) * 100;
      setDownloadPct(pct);
      setStatus("Downloading voice… " + Math.round(pct) + "%");
    }
  };
  let wav;
  try {
    wav = await tts.predict({ text, voiceId: state.voice }, onProgress);
  } finally {
    setDownloadPct(null);
  }
  return wav instanceof Blob ? wav : new Blob([wav], { type: "audio/wav" });
}

// We want seamless playback, so the next chunk starts synthesizing while
// the current one plays. Each queued item carries a lazily-kicked-off
// blobPromise. `primeAhead` ensures the next N items have started.
const LOOKAHEAD = 2;  // prime this many chunks ahead so playback flows

function stopAll({ keepSession = false, keepSource = false } = {}) {
  console.log("[TTS] stopAll keepSession=", keepSession, "keepSource=", keepSource,
              "hadAudio=", !!state.audio, "audioTime=", state.audio?.currentTime);
  if (state.trackerTeardown) { try { state.trackerTeardown(); } catch {} state.trackerTeardown = null; }
  state.stopToken++;  // invalidate in-flight synths
  if (state.audio) {
    // Detach listeners BEFORE we mutate src/pause — otherwise the async
    // error/ended events those mutations trigger will fire our handlers
    // and clobber freshly-rebuilt state (e.g., during Restart).
    state.audio.onended = null;
    state.audio.onerror = null;
    try { state.audio.pause(); } catch {}
    try { state.audio.removeAttribute("src"); state.audio.load(); } catch {}
    state.audio = null;
  }
  if (state.current?.button) state.current.button.classList.remove("playing", "loading");
  for (const q of state.queue) q.button?.classList.remove("playing", "loading");
  state.queue = []; state.current = null; state.paused = false;
  if (!keepSession) state.session = null;
  if (!keepSource) state.sourceButton = null;
  $bubble.classList.remove("playing");
  setBubbleIcon();
  setStatus("");
}

function togglePause() {
  if (!state.current || !state.audio) return;
  if (state.paused) {
    state.paused = false;
    state.audio.play().catch(e => console.error("[TTS] resume failed", e));
    $bubble.classList.add("playing");
    setStatus("Playing…");
  } else {
    state.paused = true;
    state.audio.pause();
    $bubble.classList.remove("playing");
    setStatus("Paused");
  }
  setBubbleIcon();
}

function restartSession() {
  console.log("[TTS] restartSession session?=", !!state.session, "chunks=", state.session?.length,
              "paused=", state.paused);
  if (!state.session) return;
  const wasPaused = state.paused;
  // Fast path — a single live clip: just rewind in place. Preserves the
  // paused/playing state exactly (if paused, stays paused at 0; if playing,
  // continues playing from 0).
  if (state.audio && state.session.length === 1 && state.queue.length === 0) {
    try { state.audio.currentTime = 0; } catch {}
    setStatus(wasPaused ? "Paused" : "Playing…");
    return;
  }
  // Slow path — multi-chunk sessions: rebuild the queue from the session
  // snapshot, then honor wasPaused after playback starts.
  const snapshot = state.session.map(it => ({
    text: it.text, button: it.button, blobUrl: it.blobUrl,
  }));
  const src = state.sourceButton;
  stopAll({ keepSession: true, keepSource: true });
  state.sourceButton = src;
  state.session = snapshot.map(it => ({ ...it }));
  state.pendingPause = wasPaused;
  for (const it of snapshot) if (it.button) it.button.classList.add("loading");
  state.queue.push(...snapshot);
  playNext();
}

function primeAhead() {
  const n = Math.min(LOOKAHEAD, state.queue.length);
  for (let i = 0; i < n; i++) {
    const item = state.queue[i];
    if (item.blobPromise) continue;
    if (item.blobUrl) continue;  // static URL — no synth needed
    const token = state.stopToken;
    item.blobPromise = (async () => {
      const blob = await synth(item.text);
      if (token !== state.stopToken) throw new Error("cancelled");
      return blob;
    })();
    // Swallow unhandled rejections; playNext will see the rejection when it awaits.
    item.blobPromise.catch(() => {});
  }
}

async function playNext() {
  if (!state.queue.length) { state.current = null; $bubble.classList.remove("playing"); setStatus(""); return; }
  // Make sure the head (and the one after it, for seamless flow) is synthesizing.
  primeAhead();
  state.current = state.queue.shift();
  const { text, button, blobPromise } = state.current;
  if (button) { button.classList.remove("loading"); button.classList.add("playing"); }
  $bubble.classList.add("playing");
  setBubbleIcon();
  // Kick off lookahead for the *new* head of the queue now that we shifted.
  primeAhead();
  const token = state.stopToken;
  try {
    let url, revoke = false;
    if (state.current.blobUrl) {
      url = state.current.blobUrl;
    } else {
      const blob = await blobPromise;
      if (token !== state.stopToken) return;
      url = URL.createObjectURL(blob);
      revoke = true;
    }
    const audio = new Audio(url);
    audio.preload = "auto";
    // Defensive: guarantee we start at the top of this clip. A fresh Audio
    // element is already at 0, but if ANYTHING upstream reuses an element
    // or seeks, this prevents mystery resume-points.
    try { audio.currentTime = 0; } catch {}
    state.audio = audio;
    const ttsSessionId = ++state.audioGen;
    audio.dataset.ttsGen = String(ttsSessionId);
    const _preview = state.current.blobUrl ? `[static] ${state.current.blobUrl}` : (text || "").slice(0, 60);
    console.log("[TTS] play gen=", ttsSessionId, "preview=", _preview);
    audio.onended = () => {
      if (audio !== state.audio) { console.log("[TTS] stale onended gen=", ttsSessionId); return; }
      if (revoke) URL.revokeObjectURL(url);
      if (button) button.classList.remove("playing");
      playNext();
    };
    audio.onerror = (e) => {
      if (audio !== state.audio) { console.log("[TTS] stale onerror gen=", ttsSessionId); return; }
      console.error("[TTS] audio error", e);
      setStatus("Playback error"); stopAll();
    };
    setStatus("Playing…");
    // Tear down any previous scrub/highlight UI before mounting a new one.
    if (state.trackerTeardown) { try { state.trackerTeardown(); } catch {} state.trackerTeardown = null; }
    state.trackerTeardown = mountTracker(state.sourceButton, audio);
    try { audio.currentTime = 0; } catch {}
    await audio.play();
    // If Restart was invoked while we were paused, respect that: pause
    // immediately so the user sees a clean rewind without audio.
    if (state.pendingPause) {
      state.pendingPause = false;
      state.paused = true;
      try { audio.pause(); } catch {}
      $bubble.classList.remove("playing");
      setStatus("Paused");
      setBubbleIcon();
    }
  } catch (e) {
    if (token !== state.stopToken) return;  // stopped — silent
    console.error("[TTS]", e, "text=", (text || "").slice(0, 120));
    setStatus("Skipped a chunk: " + (e.message || e).toString().slice(0, 120));
    if (button) button.classList.remove("playing", "loading");
    state.current = null;
    setDownloadPct(null);
    playNext();
  }
}

function enqueue(items, { sourceButton = null } = {}) {
  const fresh = !state.current && !state.queue.length;
  if (fresh) {
    state.session = items.map(it => ({ text: it.text, button: it.button, blobUrl: it.blobUrl }));
    state.sourceButton = sourceButton;
  }
  for (const it of items) {
    if (it.button) it.button.classList.add("loading");
    state.queue.push(it);
  }
  if (fresh) { playNext(); setBubbleIcon(); }
  else primeAhead();
}

function makePlayButton(label) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tts-play";
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.textContent = "▶";
  return btn;
}

function wireTTSButton(btn, getSourceEl, opts = {}) {
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    if (state.sourceButton === btn && state.current) { togglePause(); return; }
    // Prefer a pre-rendered WAV when available — works over plain HTTP
    // (no SharedArrayBuffer / OPFS needed).
    if (opts.audioUrl) {
      try {
        const head = await fetch(opts.audioUrl, { method: "HEAD" });
        if (head.ok) {
          stopAll();
          enqueue([{ blobUrl: opts.audioUrl, button: null }], { sourceButton: btn });
          return;
        }
      } catch (e) { /* fall through to live synth */ }
    }
    const src = getSourceEl();
    if (!src) return;
    const text = cleanText(src);
    if (!text) return;
    stopAll();
    const chunks = splitForTTS(text);
    enqueue(chunks.map(c => ({ text: c, button: null })), { sourceButton: btn });
  });
}

// Pre-rendered audio lives at <audio_base>/daily-<id>.wav and weekly-<wk>.wav.
// `__ANCHOR_BASE__` is the relative path to the site root from this page.
const AUDIO_BASE = (window.__ANCHOR_BASE__ || "./") + "audio";

function injectEntryButtons() {
  document.querySelectorAll("article.entry .entry-head h2").forEach(h2 => {
    if (h2.querySelector(".tts-play")) return;
    const entry = h2.closest("article.entry");
    const id = entry?.id;
    const audioUrl = id ? `${AUDIO_BASE}/daily-${id}.wav` : null;
    const btn = makePlayButton("Read this entry");
    wireTTSButton(btn,
      () => entry?.querySelector(".entry-body") || entry,
      { audioUrl });
    h2.appendChild(btn);
  });
  document.querySelectorAll(".week-break").forEach(wb => {
    if (wb.querySelector(".tts-play")) return;
    const wrap = wb.nextElementSibling;
    if (!wrap || !wrap.classList.contains("week-rollup-wrap")) return;
    if (!wrap.querySelector(".week-rollup")) return;
    const week = wb.dataset.week;
    const audioUrl = week ? `${AUDIO_BASE}/weekly-${week}.wav` : null;
    const btn = makePlayButton("Read this weekly rollup");
    wireTTSButton(btn,
      () => wrap.querySelector(".week-rollup") || wrap,
      { audioUrl });
    wb.appendChild(btn);
  });
  document.querySelectorAll(".month-break").forEach(mb => {
    if (mb.querySelector(".tts-play")) return;
    const wrap = mb.nextElementSibling;
    if (!wrap || !wrap.classList.contains("month-rollup-wrap")) return;
    if (!wrap.querySelector(".month-rollup")) return;
    const ym = mb.dataset.month;
    const audioUrl = ym ? `${AUDIO_BASE}/monthly-${ym}.wav` : null;
    const btn = makePlayButton("Read this monthly retrospective");
    wireTTSButton(btn,
      () => wrap.querySelector(".month-rollup") || wrap,
      { audioUrl });
    mb.appendChild(btn);
  });
}

// Big speaker bubble always opens the voice/options panel.
$bubble.addEventListener("click", (ev) => {
  ev.stopPropagation();
  $panel.classList.toggle("open");
});

document.addEventListener("click", (ev) => {
  if (!$panel.contains(ev.target) && ev.target !== $bubble) $panel.classList.remove("open");
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && state.current) stopAll();
});

document.getElementById("tts-stop").addEventListener("click", () => stopAll());
document.getElementById("tts-read-visible").addEventListener("click", () => {
  const vh = window.innerHeight;
  const entries = [...document.querySelectorAll("article.entry, .week-rollup, .entry-body, main p")];
  const visible = entries.filter(el => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < vh && r.height > 0;
  });
  // Prefer full entries when visible; else fall back to paragraphs.
  const targets = visible.filter(el => el.matches("article.entry, .week-rollup"));
  const pool = targets.length ? targets : visible;
  const items = [];
  for (const el of pool) {
    const t = cleanText(el.querySelector(".entry-body") || el);
    if (!t) continue;
    for (const c of splitForTTS(t)) items.push({ text: c, button: null });
  }
  if (!items.length) { setStatus("Nothing visible to read."); return; }
  stopAll();
  enqueue(items);
  $panel.classList.remove("open");
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", injectEntryButtons);
} else {
  injectEntryButtons();
}
// Re-inject when new entries appear (filter/search reveals, dynamic loads)
new MutationObserver(injectEntryButtons).observe(document.body, { childList: true, subtree: true });
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
<script>window.__ANCHOR_BASE__ = {html.escape(repr(anchor_base))};</script>
{ASK_BUTTON}
<div class="wrap">{body}</div>
{FILTER_WIDGET}
{INSPECT_WIDGET}
{REFRESH_WIDGET}
{CHAT_WIDGET}
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


def _count_meta(row: dict, mood: str = "") -> str:
    # Counts live only in the inspect chips at the bottom. The header
    # carries the mood only — tone without duplication.
    if mood:
        return f'<span class="mood">{esc(mood)}</span>'
    return ""


def _render_activity_disclosure(row: dict, prompts: list[dict], snippets: list[dict],
                                 files: list[dict], briefs: list[dict] | None,
                                 entry_id: str,
                                 narration_generated_at: str = "") -> str:
    """Per-category inspect chips. Each chip toggles its own panel. Multiple
    can be open at once. Order: briefs → prompts → moments → files → updated."""
    n_files = len(files); n_prompts = len(prompts); n_snips = len(snippets)
    n_briefs = len(briefs) if briefs else 0
    # Show "Updated" chip whenever the day has *any* content to timestamp.
    if not (n_files or n_prompts or n_snips or n_briefs or narration_generated_at):
        return ""

    chips: list[str] = []
    panels: list[str] = []

    def _add(kind: str, label: str, body: str, searchable: bool = False,
             search_placeholder: str = "filter...") -> None:
        if not body:
            return
        panel_id = f"insp-{esc(entry_id)}-{kind}"
        chips.append(
            f'<button class="inspect-chip" type="button" data-panel="{panel_id}">'
            f'{esc(label)}</button>'
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
        panels.append(f'<div class="inspect-panel" id="{panel_id}" hidden>{inner}</div>')

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
             f"{n_briefs} brief{'s' if n_briefs != 1 else ''}",
             "".join(body_parts),
             searchable=True, search_placeholder="filter briefs...")

    # --- prompts ---
    if prompts:
        items = []
        for p in prompts:
            cls = "filterable"
            if p.get("kind") == "correction": cls += " correction"
            elif p.get("kind") == "appreciation": cls += " appreciation"
            items.append(f'<blockquote class="{cls}">{esc(p["summary"])}</blockquote>')
        _add("prompts", f"{n_prompts} prompts", "".join(items),
             searchable=True, search_placeholder="filter prompts...")

    # --- snippets (notable moments) ---
    if snippets:
        items = [f'<div class="snippet filterable">{esc(s["text"])}</div>' for s in snippets]
        _add("moments", f"{n_snips} moments", "".join(items),
             searchable=True, search_placeholder="filter moments...")

    # --- files ---
    if files:
        items = "".join(
            f'<li class="filterable">{esc(f["path"])} '
            f'<span class="meta">· {f["touch_count"]}×</span></li>'
            for f in files
        )
        _add("files", f"{n_files} files", f"<ul class='files'>{items}</ul>",
             searchable=True, search_placeholder="filter files...")

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
        chip_label = _fmt_generated_at(narration_generated_at) if narration_generated_at else "timestamps"
        _add("updated", f"Updated {chip_label}", narration_line + brief_rows)

    return (
        f'<div class="inspect-row">{"".join(chips)}</div>'
        f'{"".join(panels)}'
    )


def _iso_week_of(date: str) -> str:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return ""


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
    return (f'<div class="interlude">'
            f'<span class="tag">a quiet day · {esc(form.replace("_", " "))}</span>'
            f'{body}</div>')


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
                     narration_generated_at: str = "") -> str:
    """Single day entry for the feed. Narration is hero; activity is disclosed."""
    pretty = _pretty_date_safe(date)
    meta = _count_meta(counts_row, mood)

    if narration:
        paragraphs = "".join(
            f"<p>{link_anchors(p.strip(), base_path=anchor_base)}</p>"
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

    activity = _render_activity_disclosure(counts_row, prompts, snippets, files, briefs,
                                           entry_id=date,
                                           narration_generated_at=narration_generated_at)

    projects_attr = ",".join(projects_in_day or [])
    week_attr = _iso_week_of(date)
    year = _pretty_date_year(date)
    year_html = f'<span class="year">{esc(year)}</span>' if year else ""
    return (
        f'<article class="entry" id="{esc(date)}" '
        f'data-projects="{esc(projects_attr)}" data-week="{esc(week_attr)}" '
        f'data-month="{esc(month)}" data-year="{esc(year)}" '
        f'data-mood="{esc(mood_label)}" '
        f'data-learning="{"yes" if has_learning else "no"}" '
        f'data-tags="{esc(",".join(tags or []))}">'
        f'  <header class="entry-head">'
        f'    <h2><a href="#{esc(date)}" style="color:inherit;text-decoration:none;">{esc(pretty)} {year_html}</a></h2>'
        f'    <span class="meta">{meta}</span>'
        f'  </header>'
        f'  {body}'
        f'  {activity}'
        f'</article>'
    )


def render_week_break(iso_week: str, rollup_prose: str, anchor_base: str = "./") -> str:
    if rollup_prose:
        paragraphs = "".join(
            f"<p>{link_anchors(p.strip(), base_path=anchor_base)}</p>"
            for p in rollup_prose.split("\n\n") if p.strip()
        )
        rollup_html = f'<div class="week-rollup">{paragraphs}</div>'
    else:
        rollup_html = ""
    return (
        f'<div class="week-break" data-week="{esc(iso_week)}">'
        f'  — Week {esc(iso_week)} —'
        f'</div>'
        f'<div class="week-rollup-wrap" data-week="{esc(iso_week)}">{rollup_html}</div>'
        if rollup_html else
        f'<div class="week-break" data-week="{esc(iso_week)}">'
        f'  — Week {esc(iso_week)} —'
        f'</div>'
    )


def render_month_break(year_month: str, rollup_prose: str, anchor_base: str = "./") -> str:
    """Month divider + attached monthly retrospective, mirrors render_week_break."""
    try:
        pretty = datetime.strptime(year_month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        pretty = year_month
    if rollup_prose:
        paragraphs = "".join(
            f"<p>{link_anchors(p.strip(), base_path=anchor_base)}</p>"
            for p in rollup_prose.split("\n\n") if p.strip()
        )
        rollup_html = f'<div class="month-rollup">{paragraphs}</div>'
        return (
            f'<div class="month-break" data-month="{esc(year_month)}">'
            f'  ― {esc(pretty)} ―'
            f'</div>'
            f'<div class="month-rollup-wrap" data-month="{esc(year_month)}">{rollup_html}</div>'
        )
    return (
        f'<div class="month-break" data-month="{esc(year_month)}">'
        f'  ― {esc(pretty)} ―'
        f'</div>'
    )


def render_feed(entries_html: list[str], *, site_title: str, subtitle: str,
                projects: list[str] | None = None,
                weeks: list[dict] | None = None,
                months: list[dict] | None = None,
                moods: list[dict] | None = None,
                learnings: list[dict] | None = None,
                years: list[dict] | None = None,
                tags: list[dict] | None = None,
                crumb_html: str = "") -> str:
    """Compose the feed page. Filtering is client-side via a breadcrumb
    chip bar — see FILTER_WIDGET for the runtime behavior."""
    import json as _json
    has_any = bool(projects or weeks or months or moods or learnings or years or tags)
    if has_any:
        filter_bar = (
            '<div class="filter-bar">'
            '  <div class="filter-row filter-modes" id="filter-modes"></div>'
            '  <div class="filter-row filter-axes" id="filter-axes"></div>'
            '  <div class="filter-row filter-options" id="filter-options"></div>'
            '</div>'
        )
    data_script = (
        f'<script>\n'
        f'window.__FILTERS__ = {_json.dumps({"projects": projects or [], "weeks": weeks or [], "months": months or [], "moods": moods or [], "learnings": learnings or [], "years": years or [], "tags": tags or []})};\n'
        f'</script>'
    )
    head = (
        f'<header class="site-head">'
        f'  {crumb_html}'
        f'  <h1>{esc(site_title)}</h1>'
        f'  <div class="sub">{esc(subtitle)}</div>'
        f'  {filter_bar}'
        f'</header>'
    )
    body_entries = "\n".join(entries_html) if entries_html else '<p style="text-align:center;color:var(--muted);">No entries yet.</p>'
    return data_script + head + '<main id="feed">' + body_entries + '</main><div class="filter-empty" id="filter-empty" style="display:none;">No entries match this filter.</div><footer>claudejournal</footer>'


def render_chat_page() -> str:
    """Kept for deep link, but main chat is the floating bubble."""
    return (
        '<header class="site-head"><h1>Ask the journal</h1>'
        '<div class="sub">The chat is also available on every page via the bubble in the corner.</div></header>'
        '<p style="text-align:center; color:var(--muted);">'
        'Click the <b>?</b> bubble in the bottom-right corner to start.</p>'
    )
