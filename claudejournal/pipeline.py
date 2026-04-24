"""End-to-end pipeline orchestration: scan -> brief -> narrate -> index -> render.

Used by `claudejournal run` and by scheduled task invocations. Idempotent —
every stage is incremental; unchanged inputs are skipped by hash.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from claudejournal import brief as briefmod
from claudejournal import narrate as narratemod
from claudejournal import rag
from claudejournal.config import Config
from claudejournal.db import connect
from claudejournal.narrator import ClaudeCodeNarrator
from claudejournal.render import render_site
from claudejournal.scan import scan


def run_all(cfg: Config, *,
            brief_model: str = "haiku",
            narration_model: str = "sonnet",
            min_events: int = 20,
            force: bool = False,
            skip_narration: bool = False,
            verbose: bool = True,
            progress=None) -> dict:
    """Run the full ingestion → narration → indexing → rendering pipeline.

    `progress(stage, done, total, label="")` is called on each step of the
    long stages (scan, brief, narrate). Short stages (index, render) report
    a single step.
    """
    def _tick(stage: str, done: int, total: int, label: str = "") -> None:
        if progress:
            try: progress(stage, done, total, label)
            except Exception: pass

    t0 = datetime.now(timezone.utc)
    stats: dict = {"started_at": t0.isoformat()}

    if verbose: print("[1/5] scan")
    _tick("scan", 0, 1, "starting")
    stats["scan"] = scan(cfg, force=force, verbose=verbose,
                         progress=lambda d, t, l="": _tick("scan", d, t, l))

    narrator = ClaudeCodeNarrator(model=brief_model, narration_model=narration_model)

    if verbose: print("[2/5] brief")
    _tick("brief", 0, 1, "starting")
    stats["brief"] = briefmod.run(
        cfg, narrator=narrator, all_=True,
        min_events=min_events, force=force, verbose=verbose,
        max_workers=getattr(cfg, "max_workers", 4),
        progress=lambda d, t, l="": _tick("brief", d, t, l),
    )

    # Documents get their own summarization stage. Idempotent — the
    # summary input hash includes DOC_PROMPT_VERSION + title + note +
    # extracted text, so existing docs regenerate only when something
    # genuinely changes. New docs added via `claudejournal doc add` run
    # their own summarize immediately, so this stage is a safety net for
    # prompt-version bumps and force-refreshes.
    if verbose: print("[2b] doc-summaries")
    _tick("doc_summary", 0, 1, "starting")
    from claudejournal import docs as docsmod
    from claudejournal.db import connect as _connect
    doc_stats = {"generated": 0, "skipped": 0, "errors": 0}
    _conn = _connect(cfg.db_path)
    try:
        doc_ids = [r["id"] for r in _conn.execute(
            "SELECT id FROM documents ORDER BY added_at"
        ).fetchall()]
        total_docs = len(doc_ids)
        for idx, did in enumerate(doc_ids, 1):
            _tick("doc_summary", idx, max(total_docs, 1), did)
            try:
                s = docsmod.summarize_document(
                    _conn, did, model=cfg.brief_model, force=force, verbose=verbose,
                )
                doc_stats["generated"] += s.get("generated", 0)
                doc_stats["skipped"] += s.get("skipped", 0)
            except Exception as exc:
                doc_stats["errors"] += 1
                if verbose: print(f"  ! doc {did}: {exc}")
    finally:
        _conn.close()
    stats["doc_summary"] = doc_stats
    _tick("doc_summary", 1, 1, "done")

    if verbose: print("[2c] topic-summaries")
    _tick("topic_summary", 0, 1, "starting")
    from claudejournal import topics as topicsmod
    topic_stats = topicsmod.run(
        cfg, all_=True, model=cfg.topic_model, force=force,
        verbose=verbose,
        progress=lambda d, t, l="": _tick("topic_summary", d, t, l),
    )
    stats["topic_summary"] = topic_stats
    _tick("topic_summary", 1, 1, "done")

    if not skip_narration:
        if verbose: print("[3/5] narrate")
        _tick("narrate", 0, 1, "starting")
        stats["narrate"] = narratemod.run(
            cfg, narrator=narrator, all_=True, force=force, verbose=verbose,
            progress=lambda d, t, l="": _tick("narrate", d, t, l),
        )
    else:
        stats["narrate"] = {"skipped": True}

    if cfg.interludes_enabled:
        if verbose: print("[3b] interludes")
        _tick("interludes", 0, 1, "starting")
        from claudejournal import interludes as interludemod
        stats["interludes"] = interludemod.run(
            cfg, all_=True, force=force, verbose=verbose,
            progress=lambda d, t, l="": _tick("interludes", d, t, l),
        )

    if not skip_narration:
        if verbose: print("[3c] weekly rollups")
        _tick("rollup", 0, 1, "starting")
        from claudejournal import rollup as rollupmod
        stats["rollup"] = rollupmod.run(
            cfg, all_=True, model=cfg.rollup_model, force=False,
            verbose=verbose,
        )
        _tick("rollup", 1, 1, "done")

        if verbose: print("[3d] monthly rollups")
        _tick("monthly", 0, 1, "starting")
        from claudejournal import monthly as monthlymod
        stats["monthly"] = monthlymod.run(
            cfg, all_=True, model=cfg.rollup_model, force=False,
            verbose=verbose,
        )
        _tick("monthly", 1, 1, "done")

    if verbose: print("[4/5] index")
    _tick("index", 0, 1, "rebuilding")
    conn = connect(cfg.db_path)
    try:
        stats["index"] = rag.reindex(conn, cfg.claude_home, verbose=verbose)
    finally:
        conn.close()
    _tick("index", 1, 1, "done")

    if verbose: print("[5/5] render")
    _tick("render", 0, 1, "rendering")
    out = Path(__file__).resolve().parent.parent / "out"
    stats["render"] = render_site(cfg.db_path, out, cfg.claude_home)
    _tick("render", 1, 1, "done")

    # Optional final stage — pre-render WAVs for new/changed narrations so
    # the static site can play audio without secure-context APIs. Skipped
    # silently if piper CLI isn't installed.
    if getattr(cfg, "audio_enabled", True):
        from claudejournal import audio as audiomod
        if audiomod.resolve_piper(cfg):
            if verbose: print("[6/6] audio")
            _tick("audio", 0, 1, "synthesizing")
            try:
                stats["audio"] = audiomod.generate_for_site(
                    cfg, out, voice=cfg.audio_voice, verbose=verbose
                )
            except Exception as exc:
                stats["audio"] = {"error": str(exc)}
                if verbose: print(f"  audio stage failed: {exc}")
            _tick("audio", 1, 1, "done")
        else:
            stats["audio"] = {"skipped": "piper not found (pip install piper-tts or set config.piper_binary)"}

    t1 = datetime.now(timezone.utc)
    stats["finished_at"] = t1.isoformat()
    stats["duration_sec"] = (t1 - t0).total_seconds()

    report_path = cfg.db_path.parent / "last_run.json"
    report_path.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    return stats
