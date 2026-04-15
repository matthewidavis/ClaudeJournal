

<img width="764" height="320" alt="image" src="https://github.com/user-attachments/assets/377563c3-7f1d-475d-91d2-aa1d8675679c" />


> Your Claude Code sessions, told back to you as a diary you can read, listen to, search, and ask questions of — entirely on your own machine.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Local-first](https://img.shields.io/badge/data-100%25%20local-success.svg)](#privacy)

## Why this exists

You spend hours every day in Claude Code and almost none of it sticks. Sessions scroll past, lessons get re-learned, decisions get re-made because nobody wrote them down — least of all you.

ClaudeJournal reads the local logs Claude Code already keeps and turns them into a **first-person diary**: daily entries written as if *you* wrote them, weekly and monthly retrospectives, a searchable archive, audio narration you can play on a walk, and an MCP-exposed memory layer your future Claude sessions can query unprompted.

https://github.com/user-attachments/assets/67881737-edaf-419e-a840-77288868f165

Nothing leaves your machine. The "AI" that writes the journal is the same `claude` CLI you're already using.

## Features

- 📖 **Daily diary in a human voice.** Structured per-session briefs distilled into flowing first-person prose. No bullet dumps, no third-person reports.
- 🗓 **Weekly + monthly retrospectives.** Map-reduce over the daily entries. Step back and see arcs without writing them.
- 🎧 **Pre-rendered audio.** Every entry, weekly, and monthly is synthesized to WAV via Piper TTS (LibriTTS-high by default). Play in the browser with a scrub bar and live sentence highlighting. Works on any LAN device, no HTTPS required.
- 🔍 **Hybrid search + chat.** BM25 + vector retrieval over the corpus. Ask "*what did I learn about ONNX?*" via the floating chat bubble.
- 🏷 **Cross-cut by topic.** Briefs auto-tag themselves; the filter bar lets you slice by Project, Topic, Year, Month, Week, Mood, "Aha moments," or full-text Find.
- 🔌 **MCP memory for any Claude session.** Other `claude` sessions on your machine can call `journal_search`, `journal_recent`, `journal_topic`, `journal_learned` — turning the journal into long-term memory the model can reach into without being asked.
- ⏰ **Set-and-forget scheduling.** Install a nightly auto-refresh from the UI; Windows Task Scheduler / cron is configured for you.
- 🧱 **Idempotent pipeline.** Every stage skips work it's already done. A daily run takes seconds after the first.
- 🛡 **Local by design.** All content lives in `db/journal.sqlite`. Redaction patterns strip API keys / tokens / passwords from extracted events *before* anything is written.

## Quick start

```bash
git clone https://github.com/matthewidavis/ClaudeJournal.git
cd ClaudeJournal

python -m venv .venv
.venv\Scripts\activate              # Windows
# source .venv/bin/activate         # macOS/Linux

pip install -e .
pip install piper-tts mcp           # audio narration + MCP server

python -m claudejournal run         # full pipeline (first run takes a while)
python -m claudejournal serve       # http://localhost:8765
```

Visit the URL on the host machine, or any device on your LAN at `http://<host-ip>:8765/`. Audio works over plain HTTP — no certs needed.

## How it works

```
~/.claude/projects/**/*.jsonl
           ↓ scan + extract (no LLM)
       events table
           ↓ brief (claude -p, structured JSON per session)
       session_briefs
           ↓ narrate (claude -p, first-person diary prose)
        narrations (daily)
           ↓ rollup
       narrations (weekly → monthly)
           ↓ index (BM25 + vectors)
        rag_chunks
           ↓ render + audio
        out/*.html + out/audio/*.wav
           ↓
        serve  (chat /api/ask, schedule /api/schedule, MCP /…)
```

Each step is content-hashed so re-runs are cheap. Stop at any stage and you still have a usable artifact.

## Configuration

`config.json` at the repo root:

| field | default | purpose |
|---|---|---|
| `claude_home` | `~/.claude` | Where to find session JSONLs |
| `brief_model` | `haiku` | Per-session structured summaries |
| `narration_model` | `sonnet` | Daily diary prose |
| `rollup_model` | `sonnet` | Weekly + monthly retrospectives |
| `audio_enabled` | `true` | Pre-render WAVs during `run` |
| `audio_voice` | `en_US-libritts-high` | Piper voice ID |
| `interludes_enabled` | `true` | Whimsical fillers on empty days |
| `redact_patterns` | API-key regexes | Stripped from events at extraction time |

## Commands

| command | what it does |
|---|---|
| `scan` | Parse JSONLs → events in DB |
| `brief` | LLM session summaries |
| `narrate` | Daily diary prose |
| `rollup` | Weekly retrospectives |
| `monthly` | Monthly retrospectives |
| `interludes` | Empty-day creative fillers |
| `index` | (Re)build RAG index |
| `render` | Generate static HTML |
| `audio` | Pre-render WAVs via Piper |
| `run` | All of the above, incremental |
| `serve` | Host the generated site (auto-registers MCP) |
| `mcp` | Run as MCP server over stdio |
| `status` | Dry-check what `run` would do |
| `schedule` | Print OS scheduler install command |

## Privacy

- `db/journal.sqlite` — all content, **gitignored**.
- `out/` — rendered HTML + WAVs, **gitignored**.
- `db/piper_models/` — voice weights, gitignored (auto-downloaded).
- Server binds to all interfaces by default (`--host 127.0.0.1` for loopback only).
- Browser-side TTS would require HTTPS for SharedArrayBuffer; the pre-render path sidesteps that entirely so plain HTTP on a trusted LAN is safe.

## Requirements

- Python 3.11+
- [Claude Code CLI](https://docs.claude.com/claude-code) on `PATH`
- Optional: `piper-tts` for audio narration
- Optional: `sqlite-vec` + `sentence-transformers` for richer RAG

## License

[MIT](LICENSE) — fork it, run it, change it, share it. If it gives you a useful diary, that's enough.
