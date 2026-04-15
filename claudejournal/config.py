import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    claude_home: Path
    db_path: Path
    include_projects: list[str] = field(default_factory=list)
    exclude_projects: list[str] = field(default_factory=list)
    redact_patterns: list[str] = field(default_factory=list)
    correction_patterns: list[str] = field(default_factory=list)
    appreciation_patterns: list[str] = field(default_factory=list)
    max_prompt_chars: int = 500
    brief_model: str = "haiku"
    narration_model: str = "sonnet"
    rollup_model: str = "sonnet"
    min_events_for_brief: int = 20
    schedule_hour: int = 23
    schedule_minute: int = 30
    max_workers: int = 4
    interludes_enabled: bool = True
    interlude_seeds: list[str] = field(default_factory=list)
    audio_enabled: bool = True
    audio_voice: str = "en_US-libritts-high"


def default_claude_home() -> Path:
    env = os.environ.get("CLAUDE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".claude"


def load(config_path: Path | None = None) -> Config:
    root = Path(__file__).resolve().parent.parent
    path = config_path or (root / "config.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    claude_home = Path(data["claude_home"]) if data.get("claude_home") else default_claude_home()
    db_path = Path(data.get("db_path", "db/journal.sqlite"))
    if not db_path.is_absolute():
        db_path = root / db_path

    return Config(
        claude_home=claude_home,
        db_path=db_path,
        include_projects=data.get("include_projects", []),
        exclude_projects=data.get("exclude_projects", []),
        redact_patterns=data.get("redact_patterns", []),
        correction_patterns=data.get("correction_patterns", []),
        appreciation_patterns=data.get("appreciation_patterns", []),
        max_prompt_chars=data.get("max_prompt_chars", 500),
        brief_model=data.get("brief_model", "haiku"),
        narration_model=data.get("narration_model", "sonnet"),
        rollup_model=data.get("rollup_model", "sonnet"),
        min_events_for_brief=data.get("min_events_for_brief", 20),
        schedule_hour=data.get("schedule_hour", 23),
        schedule_minute=data.get("schedule_minute", 30),
        max_workers=data.get("max_workers", 4),
        interludes_enabled=data.get("interludes_enabled", True),
        interlude_seeds=data.get("interlude_seeds", []),
        audio_enabled=data.get("audio_enabled", True),
        audio_voice=data.get("audio_voice", "en_US-libritts-high"),
    )
