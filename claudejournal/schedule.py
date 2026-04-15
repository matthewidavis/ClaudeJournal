"""Schedule-installer helpers. `hint_for_platform` prints a copy/paste
command. `install`, `uninstall`, `status` actually invoke the OS scheduler
— used by the /api/schedule endpoints so the user can set it up from the
site without opening a terminal."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


TASK_NAME = "ClaudeJournalNightly"


def _python_cmd() -> tuple[str, str]:
    python = Path(sys.executable).as_posix()
    return python, f'"{python}" -m claudejournal run --quiet'


def hint_for_platform(hour: int = 23, minute: int = 30) -> str:
    """Return a ready-to-copy install command for the user's OS.

    We print rather than install to avoid surprise changes to the system
    scheduler — user approves by running the printed command themselves.
    """
    python = Path(sys.executable).as_posix()
    repo = Path(__file__).resolve().parent.parent
    cmd = f'"{python}" -m claudejournal run --quiet'

    lines = [
        f"# nightly at {hour:02d}:{minute:02d} local time",
        f"# working dir: {repo}",
        "",
    ]
    sysname = platform.system()

    if sysname == "Windows":
        task = "ClaudeJournalNightly"
        # schtasks.exe — one-liner install
        lines.append("### Windows — install via Task Scheduler:")
        lines.append(
            f'schtasks /Create /SC DAILY /TN {task} /ST {hour:02d}:{minute:02d} '
            f'/TR "{python} -m claudejournal run --quiet" /F'
        )
        lines.append("")
        lines.append("### verify:  schtasks /Query /TN " + task)
        lines.append("### remove:  schtasks /Delete /TN " + task + " /F")
    else:
        cron_line = f"{minute} {hour} * * *  cd {repo.as_posix()} && {cmd} >> {repo.as_posix()}/db/cron.log 2>&1"
        lines.append("### Linux/macOS — add to crontab:")
        lines.append(f"(crontab -l 2>/dev/null; echo '{cron_line}') | crontab -")
        lines.append("")
        lines.append("### verify:  crontab -l | grep claudejournal")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Programmatic install / status / uninstall — invoked by /api/schedule
# endpoints. Windows uses schtasks.exe; POSIX uses crontab.
# ---------------------------------------------------------------------------


def status() -> dict:
    """Return {installed: bool, time: 'HH:MM' | None, raw: str}."""
    sysname = platform.system()
    if sysname == "Windows":
        try:
            out = subprocess.run(
                ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"installed": False, "time": None, "raw": f"error: {e}"}
        if out.returncode != 0:
            return {"installed": False, "time": None, "raw": out.stderr.strip()[:200]}
        raw = out.stdout
        t = None
        for line in raw.splitlines():
            if "Start Time:" in line:
                t = line.split(":", 1)[1].strip()
                break
        return {"installed": True, "time": t, "raw": raw[:400]}
    else:
        try:
            out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"installed": False, "time": None, "raw": f"error: {e}"}
        lines = [l for l in out.stdout.splitlines() if "claudejournal" in l]
        if not lines:
            return {"installed": False, "time": None, "raw": ""}
        # cron format: "MIN HOUR * * * cmd"
        parts = lines[0].split(None, 5)
        t = None
        if len(parts) >= 2:
            try:
                t = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
            except ValueError:
                pass
        return {"installed": True, "time": t, "raw": "\n".join(lines)}


def install(hour: int = 23, minute: int = 30) -> dict:
    """Install / replace the nightly schedule. Returns {ok, raw}."""
    hour = max(0, min(23, int(hour)))
    minute = max(0, min(59, int(minute)))
    python, _ = _python_cmd()
    sysname = platform.system()
    if sysname == "Windows":
        tr = f'"{python}" -m claudejournal run --quiet'
        try:
            out = subprocess.run(
                ["schtasks", "/Create", "/SC", "DAILY", "/TN", TASK_NAME,
                 "/ST", f"{hour:02d}:{minute:02d}", "/TR", tr, "/F"],
                capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "raw": f"error: {e}"}
        return {"ok": out.returncode == 0,
                "raw": (out.stdout + out.stderr).strip()[:400]}
    else:
        repo = Path(__file__).resolve().parent.parent
        cron_line = (f"{minute} {hour} * * *  cd {repo.as_posix()} && "
                     f'{python} -m claudejournal run --quiet '
                     f">> {repo.as_posix()}/db/cron.log 2>&1")
        try:
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
            kept = [l for l in existing.stdout.splitlines() if "claudejournal" not in l]
            new_crontab = "\n".join(kept + [cron_line]) + "\n"
            p = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True,
                               text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "raw": f"error: {e}"}
        return {"ok": p.returncode == 0, "raw": (p.stdout + p.stderr).strip()[:400]}


def uninstall() -> dict:
    sysname = platform.system()
    if sysname == "Windows":
        try:
            out = subprocess.run(
                ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "raw": f"error: {e}"}
        return {"ok": out.returncode == 0,
                "raw": (out.stdout + out.stderr).strip()[:400]}
    else:
        try:
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
            kept = [l for l in existing.stdout.splitlines() if "claudejournal" not in l]
            new_crontab = "\n".join(kept) + ("\n" if kept else "")
            p = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True,
                               text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "raw": f"error: {e}"}
        return {"ok": p.returncode == 0, "raw": (p.stdout + p.stderr).strip()[:400]}
