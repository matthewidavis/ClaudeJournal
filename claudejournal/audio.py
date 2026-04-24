"""Pre-render diary entries to WAV files via Piper TTS so the static site
can play audio over plain HTTP — no SharedArrayBuffer / OPFS required on
the client. Uses the same Piper voices vits-web wraps in the browser.

Naming convention written under out/audio/:
  daily-<YYYY-MM-DD>.wav
  weekly-<ISO-week>.wav

Cache: a side-by-side .json with sha1(prose|voice). Re-runs skip unchanged.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_VOICE = "en_US-libritts-high"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# voice id -> relative model path on huggingface
VOICE_PATHS = {
    "en_US-libritts-high":     "en/en_US/libritts/high/en_US-libritts-high.onnx",
    "en_US-libritts_r-medium": "en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx",
    "en_US-hfc_female-medium": "en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx",
    "en_US-hfc_male-medium":   "en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx",
    "en_US-amy-medium":        "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_US-ryan-high":         "en/en_US/ryan/high/en_US-ryan-high.onnx",
    "en_US-lessac-high":       "en/en_US/lessac/high/en_US-lessac-high.onnx",
    "en_GB-alan-medium":       "en/en_GB/alan/medium/en_GB-alan-medium.onnx",
    "en_GB-jenny_dioco-medium":"en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx",
}


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  fetching {url}")
    try:
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(dest)
    except BaseException:
        # On any failure (network, KeyboardInterrupt, etc), drop the
        # half-written .part so a retry starts cleanly.
        try: tmp.unlink()
        except FileNotFoundError: pass
        raise


def ensure_model(voice: str, models_dir: Path) -> Path:
    """Download the .onnx + .onnx.json for `voice` if missing. Returns the
    .onnx path to pass to piper."""
    if voice not in VOICE_PATHS:
        raise ValueError(f"Unknown voice {voice!r}. Known: {sorted(VOICE_PATHS)}")
    rel = VOICE_PATHS[voice]
    onnx = models_dir / Path(rel).name
    meta = onnx.with_suffix(onnx.suffix + ".json")
    if not onnx.exists():
        _download(f"{HF_BASE}/{rel}", onnx)
    if not meta.exists():
        _download(f"{HF_BASE}/{rel}.json", meta)
    return onnx


def resolve_piper(cfg=None) -> str | None:
    """Find the piper executable. Config wins (full path supported), then
    PATH lookup, then a handful of known Windows install locations for the
    `pip install piper-tts` case where Scripts/ isn't on PATH."""
    explicit = getattr(cfg, "piper_binary", None) if cfg else None
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
        # Bare name like "piper.exe" — let shutil.which handle it.
        return shutil.which(explicit)
    which = shutil.which("piper") or shutil.which("piper.exe")
    if which:
        return which
    # Last-resort probe: pip installs piper-tts into per-user Scripts dirs
    # that aren't on PATH by default on Windows. Matches the common layouts.
    import os, sys, site
    candidates: list[Path] = []
    for base in [sys.prefix, sys.base_prefix, *(site.getsitepackages() or []),
                 site.getusersitepackages()]:
        if not base:
            continue
        bp = Path(base)
        candidates.append(bp / "Scripts" / "piper.exe")
        # Windows Store python layout: .../site-packages next to .../Scripts.
        candidates.append(bp.parent / "Scripts" / "piper.exe")
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue
    return None


def synthesize(text: str, out_wav: Path, model_path: Path, piper_bin: str = "piper") -> None:
    """Shell out to piper CLI. Writes atomically: synth to a sibling .new.wav
    then rename — so an existing old WAV stays serveable until the new one
    is ready (important when re-rendering in a different voice)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_wav.with_suffix(".new.wav")
    proc = subprocess.run(
        [piper_bin, "--model", str(model_path), "--output_file", str(tmp)],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        try: tmp.unlink()
        except FileNotFoundError: pass
        raise RuntimeError(f"piper failed: {proc.stderr.decode('utf-8','replace')[:500]}")
    tmp.replace(out_wav)  # atomic on same filesystem


def _hash(text: str, voice: str) -> str:
    h = hashlib.sha1()
    h.update(voice.encode("utf-8")); h.update(b"\0"); h.update(text.encode("utf-8"))
    return h.hexdigest()


def _normalize_text(text: str) -> str:
    """Match the in-browser text cleanup so audio matches what users see/hear
    via the live TTS path: drop [YYYY-MM-DD] anchors, collapse whitespace."""
    import re
    t = re.sub(r"\[\d{4}-\d{2}-\d{2}\]", "", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _document_spoken_text(title: str, summary: dict, user_note: str) -> str:
    """Build a natural spoken rendering of a document summary. The stored
    summary is a JSON object (hook / takeaway / key_points / tags); Piper
    wants flowing prose. We string the fields together with connective
    language instead of reading them as labeled sections — labels like
    "Takeaway colon" sound robotic aloud."""
    hook = (summary.get("hook") or "").strip()
    takeaway = (summary.get("takeaway") or "").strip()
    points = [p.strip() for p in (summary.get("key_points") or []) if isinstance(p, str) and p.strip()]
    note = (user_note or "").strip()
    parts: list[str] = []
    parts.append(f"{title}.")
    if note:
        parts.append(f"Why I added this. {note}")
    if hook:
        parts.append(hook)
    if takeaway:
        parts.append(takeaway)
    if points:
        # Join key points with "Then," / "Also,"-style connectors so they
        # flow better than a bare list. First point gets no prefix.
        parts.append("Key points. " + " ".join(
            (p if p.endswith((".", "!", "?")) else p + ".") for p in points
        ))
    return " ".join(parts)


def generate_for_site(cfg, out_dir: Path, voice: str = DEFAULT_VOICE,
                      verbose: bool = True) -> dict:
    """Walk daily + weekly narrations in the DB and render each to a WAV.

    Returns stats dict. Skips entries whose (text, voice) hash matches the
    cache manifest, so re-runs are cheap.
    """
    from claudejournal.db import connect

    piper_bin = resolve_piper(cfg)
    if piper_bin is None:
        raise RuntimeError(
            "piper CLI not found. Install with: pip install piper-tts, "
            "or set config.piper_binary to the full path."
        )

    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = audio_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}

    models_dir = cfg.db_path.parent / "piper_models"
    model_path = ensure_model(voice, models_dir)

    conn = connect(cfg.db_path)
    # Sort key is the date (daily/project_day/document/interlude) or ISO-week /
    # YYYY-MM for rollups — all lexicographically chronological so reverse=True
    # gives newest-first. Users hear recent content while older renders.
    rows: list[tuple[str, str, str, str]] = []  # (sortkey, base, kind, prose)
    for r in conn.execute(
        "SELECT date, prose FROM narrations WHERE scope='daily' AND prose IS NOT NULL AND prose != ''"
    ):
        rows.append((r["date"], f"daily-{r['date']}", "daily", r["prose"]))
    for r in conn.execute(
        "SELECT key, prose FROM narrations WHERE scope='weekly' AND prose IS NOT NULL AND prose != ''"
    ):
        rows.append((r["key"], f"weekly-{r['key']}", "weekly", r["prose"]))
    for r in conn.execute(
        "SELECT key, prose FROM narrations WHERE scope='monthly' AND prose IS NOT NULL AND prose != ''"
    ):
        # Month key "YYYY-MM" — suffix z so it sorts after weekly/daily for same date
        rows.append((f"{r['key']}-ZZ", f"monthly-{r['key']}", "monthly", r["prose"]))
    # Document summaries — the JSON that lives under narrations.scope='document'
    # isn't readable prose; flatten it into a natural spoken form.
    for r in conn.execute(
        """SELECT d.id, d.title, d.added_date, d.user_note, n.prose AS summary_json
           FROM documents d
           JOIN narrations n ON n.scope='document' AND n.key = d.id
           WHERE n.prose IS NOT NULL AND n.prose != ''"""
    ):
        try:
            summary = json.loads(r["summary_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        spoken = _document_spoken_text(
            r["title"] or r["id"], summary, r["user_note"] or ""
        )
        if spoken:
            rows.append((r["added_date"] or "", f"doc-{r['id']}", "document", spoken))
    # Interludes live in their own table (scope wasn't extended into
    # narrations for them) but are also short readable prose.
    for r in conn.execute(
        "SELECT date, prose FROM interludes WHERE prose IS NOT NULL AND prose != ''"
    ):
        rows.append((r["date"], f"interlude-{r['date']}", "interlude", r["prose"]))
    conn.close()
    rows.sort(key=lambda x: x[0], reverse=True)

    n_skipped = n_made = n_failed = 0
    for _sortkey, base, kind, prose in rows:
        text = _normalize_text(prose)
        if not text:
            continue
        h = _hash(text, voice)
        wav = audio_dir / f"{base}.wav"
        if wav.exists() and manifest.get(base) == h:
            n_skipped += 1
            continue
        if verbose:
            print(f"  {kind:<7} {base}  ({len(text)} chars)")
        try:
            synthesize(text, wav, model_path, piper_bin=piper_bin)
            manifest[base] = h
            n_made += 1
        except Exception as exc:
            n_failed += 1
            if verbose:
                print(f"    failed: {exc}", file=sys.stderr)

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), "utf-8")
    return {"made": n_made, "skipped": n_skipped, "failed": n_failed,
            "voice": voice, "audio_dir": str(audio_dir)}
