"""Walk ~/.claude/projects/ to find sessions as *units of evidence*.

A session UUID is a session whether or not its top-level transcript was
pruned. Evidence comes from:
  - <project>/<uuid>.jsonl              — main transcript (may be gone)
  - <project>/<uuid>/subagents/*.jsonl  — subagent transcripts
Both feed the same session_id. A session with only subagent files is still
a legitimate session — just with narrower (and differently-flavored) signal.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


_UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass
class SessionInputs:
    """All jsonl files that feed a single session UUID across its evidence tree."""
    session_id: str
    project_id: str
    main_jsonl: Path | None = None            # top-level <uuid>.jsonl if it exists
    subagent_jsonls: list[Path] = field(default_factory=list)

    @property
    def all_files(self) -> list[Path]:
        return ([self.main_jsonl] if self.main_jsonl else []) + list(self.subagent_jsonls)

    @property
    def total_size(self) -> int:
        return sum(p.stat().st_size for p in self.all_files)

    @property
    def max_mtime(self) -> float:
        times = [p.stat().st_mtime for p in self.all_files]
        return max(times) if times else 0.0

    @property
    def signature(self) -> str:
        """Stable hash over input paths+mtimes+sizes. Changes only when inputs change."""
        h = hashlib.sha256()
        for p in sorted(self.all_files):
            try:
                stat = p.stat()
            except OSError:
                continue
            h.update(f"{p}|{stat.st_mtime}|{stat.st_size}\n".encode("utf-8", "replace"))
        return h.hexdigest()[:16]


@dataclass
class ProjectDir:
    project_id: str
    display_name: str
    path: Path
    memory_dir: Path | None
    sessions: list[SessionInputs]


def _display_name(project_id: str) -> str:
    parts = [p for p in project_id.split("-") if p]
    return parts[-1] if parts else project_id


def _uuid_subdirs(proj_path: Path) -> list[Path]:
    """Session-UUID subdirectories within a project (for subagents)."""
    out = []
    for child in proj_path.iterdir():
        if child.is_dir() and _UUID_RX.match(child.name):
            out.append(child)
    return out


def _subagent_jsonls(uuid_dir: Path) -> list[Path]:
    sub = uuid_dir / "subagents"
    if not sub.is_dir():
        return []
    return sorted(sub.glob("*.jsonl"))


def discover(
    claude_home: Path,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[ProjectDir]:
    projects_root = claude_home / "projects"
    if not projects_root.exists():
        return []

    include = include or []
    exclude = exclude or []

    results: list[ProjectDir] = []
    for proj_path in sorted(projects_root.iterdir()):
        if not proj_path.is_dir():
            continue
        pid = proj_path.name
        if include and pid not in include and _display_name(pid) not in include:
            continue
        if pid in exclude or _display_name(pid) in exclude:
            continue

        # Collect UUIDs from top-level jsonls and from subdirs.
        uuids: dict[str, SessionInputs] = {}
        for jsonl in proj_path.glob("*.jsonl"):
            sid = jsonl.stem
            if not _UUID_RX.match(sid):
                continue
            uuids.setdefault(sid, SessionInputs(session_id=sid, project_id=pid)).main_jsonl = jsonl
        for uuid_dir in _uuid_subdirs(proj_path):
            sid = uuid_dir.name
            entry = uuids.setdefault(sid, SessionInputs(session_id=sid, project_id=pid))
            entry.subagent_jsonls = _subagent_jsonls(uuid_dir)

        # Drop empty shells (UUID dir with neither main nor subagents).
        sessions = [s for s in uuids.values() if s.all_files]

        memory_dir = proj_path / "memory"
        results.append(ProjectDir(
            project_id=pid,
            display_name=_display_name(pid),
            path=proj_path,
            memory_dir=memory_dir if memory_dir.exists() else None,
            sessions=sessions,
        ))
    return results
