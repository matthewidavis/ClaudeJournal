"""Parse a session JSONL into normalized events.

Filter rules (by design — see memory/claudejournal_design.md):
  KEEP:   user prompts (external, non tool_result), tool_use name+path,
          corrections, errors, appreciations
  DROP:   assistant text/thinking, tool_result payloads, file-history
          snapshots, permission modes, queue ops, attachments, system msgs
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from claudejournal.redact import Redactor


@dataclass
class Event:
    ts: str
    date: str
    kind: str                     # user_prompt | tool_use | file_edit | correction | appreciation | error
    tool_name: str | None = None
    path: str | None = None
    summary: str = ""
    sentiment: float = 0.0
    raw_uuid: str | None = None
    source: str = "main"          # 'main' | 'subagent'


@dataclass
class Snippet:
    """Short assistant text block. Narrator-food for 'things learned'."""
    ts: str
    date: str
    text: str
    raw_uuid: str | None = None
    source: str = "main"


SNIPPET_MAX_CHARS = 400


FILE_EDITING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
FILE_READING_TOOLS = {"Read", "Glob", "Grep"}


def _extract_path(tool_name: str, tool_input: dict) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "notebook_path", "filePath"):
        v = tool_input.get(key)
        if isinstance(v, str):
            return v
    return None


def _iso_date(ts: str) -> str:
    # ISO timestamps like "2026-04-08T17:31:52.456Z"
    return ts[:10] if ts else ""


def _match_any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _parse_jsonl_file(
    jsonl_path: Path,
    source: str,
    correction_res, appreciation_res,
    redactor: Redactor, max_prompt_chars: int,
) -> Iterator[Event | Snippet]:
    # Preserve atime/mtime so `claude --resume` (which orders by file times)
    # stays reflective of what the user actually did — not what we scanned.
    try:
        _st = jsonl_path.stat()
        _orig_times = (_st.st_atime_ns, _st.st_mtime_ns)
    except OSError:
        _orig_times = None
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            yield from _iter_events(f, source, correction_res, appreciation_res, redactor, max_prompt_chars)
    finally:
        if _orig_times is not None:
            try:
                import os as _os
                _os.utime(jsonl_path, ns=_orig_times)
            except OSError:
                pass


def _iter_events(f, source, correction_res, appreciation_res, redactor, max_prompt_chars):
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rtype = rec.get("type")
        ts = rec.get("timestamp") or (rec.get("message") or {}).get("timestamp") or ""
        date = _iso_date(ts)
        uuid = rec.get("uuid")

        if rtype == "user":
            for item in _handle_user(
                rec, ts, date, uuid, correction_res, appreciation_res,
                redactor, max_prompt_chars,
            ):
                item.source = source
                yield item
        elif rtype == "assistant":
            for item in _handle_assistant(rec, ts, date, uuid, redactor):
                item.source = source
                yield item


def parse_session(
    inputs,  # SessionInputs — typed loosely to avoid circular import
    correction_patterns: list[str],
    appreciation_patterns: list[str],
    redactor: Redactor,
    max_prompt_chars: int = 500,
) -> Iterator[Event | Snippet]:
    """Parse all evidence files for one session UUID; emit time-ordered events.

    `inputs` is a SessionInputs with main_jsonl (optional) + subagent_jsonls.
    """
    correction_res = [re.compile(p) for p in correction_patterns]
    appreciation_res = [re.compile(p) for p in appreciation_patterns]

    buf: list[Event | Snippet] = []
    if getattr(inputs, "main_jsonl", None):
        buf.extend(_parse_jsonl_file(
            inputs.main_jsonl, "main",
            correction_res, appreciation_res, redactor, max_prompt_chars,
        ))
    for sub in getattr(inputs, "subagent_jsonls", []):
        buf.extend(_parse_jsonl_file(
            sub, "subagent",
            correction_res, appreciation_res, redactor, max_prompt_chars,
        ))

    # Sort by timestamp so combined stream is chronological.
    buf.sort(key=lambda x: x.ts or "")
    yield from buf


def _handle_user(
    rec: dict, ts: str, date: str, uuid: str | None,
    correction_res, appreciation_res,
    redactor: Redactor, max_prompt_chars: int,
) -> Iterator[Event]:
    msg = rec.get("message") or {}
    content = msg.get("content")

    # External user prompt: content is a string
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return
        scrubbed = redactor.scrub(text)
        summary = scrubbed if len(scrubbed) <= max_prompt_chars else scrubbed[:max_prompt_chars] + "..."

        sentiment = 0.0
        kind = "user_prompt"
        if _match_any(correction_res, text):
            kind = "correction"
            sentiment = -0.6
        elif _match_any(appreciation_res, text):
            kind = "appreciation"
            sentiment = 0.6

        yield Event(ts=ts, date=date, kind=kind, summary=summary,
                    sentiment=sentiment, raw_uuid=uuid)
        return

    # tool_result messages — content is a list. Skip payload; optionally flag errors.
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                if item.get("is_error"):
                    yield Event(ts=ts, date=date, kind="error",
                                summary="tool_result error", raw_uuid=uuid)


def _handle_assistant(
    rec: dict, ts: str, date: str, uuid: str | None, redactor: Redactor,
) -> Iterator[Event]:
    msg = rec.get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return

    # API error synthetic messages
    if rec.get("isApiErrorMessage"):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                yield Event(ts=ts, date=date, kind="error",
                            summary=redactor.scrub((item.get("text") or "")[:200]),
                            raw_uuid=uuid)
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "text":
            text = (item.get("text") or "").strip()
            if text and len(text) <= SNIPPET_MAX_CHARS:
                yield Snippet(ts=ts, date=date,
                              text=redactor.scrub(text), raw_uuid=uuid)
            continue
        if itype != "tool_use":
            continue  # drop thinking
        name = item.get("name") or ""
        tool_input = item.get("input") or {}
        path = _extract_path(name, tool_input)

        if name in FILE_EDITING_TOOLS and path:
            yield Event(ts=ts, date=date, kind="file_edit",
                        tool_name=name, path=path,
                        summary=f"{name} {path}", raw_uuid=uuid)
        else:
            summary_bits = [name]
            if path:
                summary_bits.append(path)
            elif isinstance(tool_input, dict):
                desc = tool_input.get("description") or tool_input.get("command") or tool_input.get("pattern")
                if isinstance(desc, str):
                    summary_bits.append(desc[:100])
            yield Event(ts=ts, date=date, kind="tool_use",
                        tool_name=name, path=path,
                        summary=redactor.scrub(" ".join(summary_bits)),
                        raw_uuid=uuid)


def session_time_bounds(items) -> tuple[str | None, str | None]:
    stamps = [x.ts for x in items if getattr(x, "ts", None)]
    if not stamps:
        return None, None
    return min(stamps), max(stamps)
