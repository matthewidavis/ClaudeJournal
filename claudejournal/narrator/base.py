"""Narrator protocol — backends plug in here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class NarratorError(RuntimeError):
    pass


@dataclass
class BriefInput:
    session_id: str
    project_name: str
    project_id: str
    date: str
    started_at: str | None
    ended_at: str | None
    user_prompts: list[dict] = field(default_factory=list)     # [{ts, kind, summary}]
    assistant_snippets: list[dict] = field(default_factory=list)  # [{ts, text}]
    files_touched: list[dict] = field(default_factory=list)    # [{path, touch_count}]
    memory_text: str = ""                                      # concatenated memory/*.md
    prior_brief_hint: str = ""                                 # optional continuity hint


@dataclass
class BriefResult:
    brief: dict                 # matches schema
    raw: str                    # exact text returned by backend
    cost_usd: float = 0.0
    model: str = ""


@dataclass
class NarrationInput:
    """Input to narrate_day — a day across projects, or one project's day."""
    date: str
    scope: str                                     # 'daily' or 'project_day'
    project_name: str | None = None                # for project_day
    project_id: str | None = None
    briefs: list[dict] = field(default_factory=list)   # list of per-session briefs (JSON dicts)
    prior_entry: str = ""                          # yesterday's narration for continuity
    threads: list[dict] = field(default_factory=list)     # deterministic — see threads.py
    anchors: list[dict] = field(default_factory=list)     # allowed [YYYY-MM-DD] citations


@dataclass
class NarrationResult:
    prose: str
    cost_usd: float = 0.0
    model: str = ""


class Narrator(Protocol):
    name: str
    def narrate_session(self, inp: BriefInput, *, dry_run: bool = False) -> BriefResult: ...
    def narrate_day(self, inp: NarrationInput, *, dry_run: bool = False) -> NarrationResult: ...
