"""External document curation — add / list / remove.

A *document* is any external material the user wants the journal to be
aware of (papers, articles, PDFs, markdown notes). Each document is:

  1. Stored in `db/docs/<id>.<ext>` (original file preserved)
  2. Text-extracted and persisted in `documents.extracted_text`
  3. Summarized via `claude -p` into a narration row
     (scope='document', key=<id>) so it joins the existing narration
     pipeline — filterable, retrievable via RAG, hashed into the
     daily cascade for `added_date`.

Date model: `added_date` is the day you ran `doc add`. That IS when the
document entered your intellectual timeline, and it's what the daily
narration's hash cascade keys on. No override flag — lying about when
you added something would desync the cascade.

Removal: hard-delete. Cascade regenerates narrations that referenced
the document without it. If you might still value it, don't remove it.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from claudejournal.config import Config
from claudejournal.db import connect


# Bump when the summary prompt or schema meaningfully changes. Participates
# in the doc summary hash so an updated prompt re-generates all summaries.
DOC_PROMPT_VERSION = "v1"


SUMMARY_SYSTEM = """You produce structured, non-hallucinated summaries of external documents for a personal journal. The user reads these to remember what a paper / article / note was about — not to replace reading it.

Rules:
1. Never invent. If a field can't be faithfully extracted, leave it empty.
2. "hook" = one sentence on what this document is, in plain language. Not a sales pitch.
3. "takeaway" = the single most useful thing the user would tell a colleague about this document. 2 sentences max. The user's `note` field (if present) hints at why they cared — let it guide the emphasis, but don't quote it.
4. "key_points" = 3 to 6 short bullets of concrete claims, findings, or techniques the document makes. No padding. Skip the field if the text is too thin to honor it.
5. "tags" = 2 to 5 short lowercase labels (1-2 words each, hyphenated) for cross-referencing. Prefer technical terms ("quantization", "vit", "kv-cache") or domains ("ml-infra", "devops") over generic ("tech", "paper").
6. "voice" = "expository-neutral". The narrator will re-voice this. Don't try to be clever.

Output ONLY valid JSON matching the schema. No prose, no markdown, no backticks."""


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "hook":       {"type": "string"},
        "takeaway":   {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "tags":       {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "takeaway", "key_points", "tags"],
    "additionalProperties": False,
}


# ── id + file handling ─────────────────────────────────────────────────────

def new_id() -> str:
    """Short url-safe token used for both the DB PK and the on-disk filename.
    10 chars of base32 = ~50 bits of entropy, collision-free at our scale."""
    return secrets.token_hex(5)


def docs_dir(cfg: Config) -> Path:
    d = cfg.db_path.parent / "docs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_ext(path: Path) -> str:
    ext = (path.suffix or "").lower()
    # Map common aliases so downstream branches don't grow a long if-chain.
    if ext in (".markdown", ".mdown"):
        return ".md"
    if ext == ".htm":
        return ".html"
    return ext


# ── extraction ─────────────────────────────────────────────────────────────

def _extract_pdf(path: Path) -> str:
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is required for PDF ingestion. Reinstall with `pip install -e .`"
        ) from exc
    reader = pypdf.PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # Individual page failures shouldn't scuttle the whole doc.
            continue
    return "\n\n".join(p.strip() for p in parts if p.strip())


_HTML_TAG_RX = re.compile(r"<[^>]+>")
_HTML_SCRIPT_RX = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_WHITESPACE_RX = re.compile(r"[ \t]+")


def _extract_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Strip <script>/<style> blocks first, then all tags. Good-enough for
    # blog posts and simple articles; dedicated readability parsers can
    # come later if quality on news sites matters.
    stripped = _HTML_SCRIPT_RX.sub("", raw)
    stripped = _HTML_TAG_RX.sub("", stripped)
    stripped = _HTML_WHITESPACE_RX.sub(" ", stripped)
    # Collapse 3+ blank lines to 2 so paragraphs stay visible.
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def extract_text(path: Path, ext: str) -> str:
    """Extract plain text from the given file. Unsupported types return ""."""
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".html":
        return _extract_html(path)
    return ""


# ── summarization ──────────────────────────────────────────────────────────

def _summary_input_hash(extracted_text: str, user_note: str,
                        title: str) -> str:
    h = hashlib.sha256()
    h.update(DOC_PROMPT_VERSION.encode())
    h.update(title.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(user_note.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(extracted_text.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _build_summary_message(title: str, user_note: str,
                           extracted_text: str, max_chars: int = 14000) -> str:
    lines: list[str] = [f"TITLE: {title}"]
    if user_note:
        lines.append(f"USER NOTE (why they added it — guide your emphasis): {user_note}")
    lines.append("")
    lines.append("DOCUMENT TEXT:")
    body = extracted_text.strip()
    if len(body) > max_chars:
        # Prefer the head; most documents front-load the thesis.
        body = body[:max_chars] + "\n...[truncated for length]"
    lines.append(body)
    lines.append("")
    lines.append(
        "Return ONLY a JSON object matching the schema. Start with { and end with }."
    )
    return "\n".join(lines)


def _call_claude(user_msg: str, model: str, binary: str = "claude") -> dict:
    """Invoke the Claude CLI for a structured summary. Mirrors the pattern
    in narrator/claude_code.py — no tools, no session persistence."""
    from claudejournal.narrator.claude_code import _no_session_leak

    cmd = [
        binary, "-p",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", SUMMARY_SYSTEM,
        "--json-schema", json.dumps(SUMMARY_SCHEMA),
    ]
    with _no_session_leak():
        proc = subprocess.run(
            cmd, input=user_msg, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}"
        )
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"CLI error: {envelope.get('result', '')[:500]}")
    summary = envelope.get("structured_output")
    if not isinstance(summary, dict):
        # Some CLI configurations surface the JSON via `result` instead.
        try:
            summary = json.loads((envelope.get("result") or "").strip())
        except (json.JSONDecodeError, TypeError):
            summary = None
    if not isinstance(summary, dict):
        raise RuntimeError(
            f"no structured summary; envelope keys: {list(envelope.keys())}"
        )
    return summary


def _persist_summary(conn: sqlite3.Connection, doc_id: str, date: str,
                     project_id: str | None, summary: dict, input_hash: str,
                     model: str) -> None:
    """Store the doc summary as a narration row under scope='document'.
    The prose is a compact JSON string — render.py will parse and present
    it structurally. project_id is NULL for multi-project docs (the daily
    cascade doesn't need it; project-day cascades read documents by the
    project_ids column on the documents table)."""
    conn.execute(
        """
        INSERT INTO narrations (scope, key, date, project_id, prose,
            prompt_version, input_hash, generated_at, model)
        VALUES ('document', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, key) DO UPDATE SET
            date = excluded.date,
            project_id = excluded.project_id,
            prose = excluded.prose,
            prompt_version = excluded.prompt_version,
            input_hash = excluded.input_hash,
            generated_at = excluded.generated_at,
            model = excluded.model
        """,
        (
            doc_id, date, project_id,
            json.dumps(summary, ensure_ascii=False),
            DOC_PROMPT_VERSION, input_hash,
            datetime.now(timezone.utc).isoformat(), model,
        ),
    )


def summarize_document(conn: sqlite3.Connection, doc_id: str, *,
                       model: str = "haiku", force: bool = False,
                       verbose: bool = True) -> dict:
    """Generate (or re-generate) the summary narration for a single doc.
    Returns a small stats dict. Safe to call repeatedly — skips when the
    input hash already matches an existing narration row."""
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"no such document: {doc_id}")

    extracted = row["extracted_text"] or ""
    if not extracted.strip():
        if verbose:
            print(f"  skip {doc_id}  (no extracted text)")
        return {"generated": 0, "skipped": 1, "reason": "no text"}

    title = row["title"] or row["original_filename"] or doc_id
    note = row["user_note"] or ""
    ih = _summary_input_hash(extracted, note, title)

    if not force:
        existing = conn.execute(
            "SELECT input_hash, prompt_version FROM narrations WHERE scope='document' AND key=?",
            (doc_id,),
        ).fetchone()
        if existing and existing["input_hash"] == ih and existing["prompt_version"] == DOC_PROMPT_VERSION:
            return {"generated": 0, "skipped": 1, "reason": "cache"}

    user_msg = _build_summary_message(title, note, extracted)
    summary = _call_claude(user_msg, model=model)

    # First project wins for the narration row's project_id — keeps legacy
    # queries that filter by project_id useful. The documents.project_ids
    # JSON array remains the source of truth for multi-project attachments.
    pids = _parse_json_list(row["project_ids"])
    first_pid = pids[0] if pids else None

    _persist_summary(conn, doc_id, row["added_date"], first_pid, summary, ih, model)
    conn.commit()
    if verbose:
        print(f"  summarized {doc_id}  {title[:40]!r}")
    return {"generated": 1, "skipped": 0}


# ── CRUD ───────────────────────────────────────────────────────────────────

def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, str)]
    except json.JSONDecodeError:
        pass
    return []


def _resolve_projects(conn: sqlite3.Connection,
                      requested: list[str]) -> tuple[list[str], list[str]]:
    """Map user-supplied project hints (display name OR project_id) to
    actual project_ids. Returns (resolved_ids, unknown_hints)."""
    if not requested:
        return [], []
    known = list(conn.execute(
        "SELECT id, display_name FROM projects"
    ).fetchall())
    resolved: list[str] = []
    unknown: list[str] = []
    for hint in requested:
        h = hint.strip()
        if not h:
            continue
        # Exact id match wins; fall back to case-insensitive display-name.
        match = next((r for r in known if r["id"] == h), None)
        if not match:
            match = next(
                (r for r in known
                 if (r["display_name"] or "").lower() == h.lower()),
                None,
            )
        if match:
            resolved.append(match["id"])
        else:
            unknown.append(h)
    # De-dupe while preserving order.
    seen: set[str] = set()
    unique = [p for p in resolved if not (p in seen or seen.add(p))]
    return unique, unknown


def add_document(cfg: Config, source_path: Path, *,
                 title: str | None = None,
                 projects: list[str] | None = None,
                 tags: list[str] | None = None,
                 note: str = "",
                 original_filename: str | None = None,
                 model: str = "haiku",
                 verbose: bool = True) -> dict:
    """Add a document to the library. Copies the file into db/docs/,
    extracts text, summarizes, and links the resulting narration into the
    existing cascade. Returns a stats dict with the new doc id + summary
    status.

    `original_filename` lets the HTTP handler pass the upload's real name
    when `source_path` is a temp file — preserves the human-readable name
    for display and drives the extension / default-title selection."""
    source_path = source_path.expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"not a file: {source_path}")

    display_name = original_filename or source_path.name
    name_path = Path(display_name)
    # Prefer the original filename's extension over the temp file's (the
    # upload path usually has the right ext; temp files may not).
    ext = _normalize_ext(name_path)
    if ext not in (".pdf", ".md", ".txt", ".html"):
        raise ValueError(
            f"unsupported extension {ext!r}. Supported: .pdf .md .txt .html"
        )

    doc_id = new_id()
    dest_dir = docs_dir(cfg)
    dest_path = dest_dir / f"{doc_id}{ext}"
    shutil.copy2(source_path, dest_path)

    try:
        text = extract_text(dest_path, ext)
    except Exception as exc:
        # Don't leave an orphaned file if extraction crashes outright.
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f"extraction failed for {display_name}: {exc}") from exc

    content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    now_utc = datetime.now(timezone.utc)
    added_at = now_utc.isoformat()
    added_date = now_utc.strftime("%Y-%m-%d")

    conn = connect(cfg.db_path)
    try:
        resolved_pids, unknown_hints = _resolve_projects(conn, projects or [])
        if unknown_hints and verbose:
            print(f"  warning: unknown project hint(s) ignored: {unknown_hints}")
        clean_tags = _clean_tags(tags or [])

        conn.execute(
            """INSERT INTO documents
               (id, title, path, original_filename, ext, content_hash,
                extracted_text, user_note, project_ids, tags,
                added_at, added_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id, title or name_path.stem, str(dest_path),
                display_name, ext, content_hash,
                text, note,
                json.dumps(resolved_pids), json.dumps(clean_tags),
                added_at, added_date,
            ),
        )
        conn.commit()

        if verbose:
            print(f"added {doc_id}  {display_name}  "
                  f"({len(text):,} chars, projects={resolved_pids or '-'})")

        summary_stats = summarize_document(conn, doc_id, model=model, verbose=verbose)
    finally:
        conn.close()

    return {
        "id": doc_id, "path": str(dest_path), "chars": len(text),
        "projects": resolved_pids, "tags": clean_tags,
        "added_date": added_date, "summary": summary_stats,
    }


def _clean_tags(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if not isinstance(t, str):
            continue
        s = t.strip().lower()
        if not s or len(s) > 32 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def list_documents(cfg: Config) -> list[dict]:
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute(
            """SELECT id, title, original_filename, added_date, added_at,
                      project_ids, tags, ext, user_note,
                      length(extracted_text) AS chars
               FROM documents ORDER BY added_date DESC, added_at DESC"""
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "title": r["title"],
            "filename": r["original_filename"],
            "ext": r["ext"],
            "added_date": r["added_date"],
            "added_at": r["added_at"],
            "projects": _parse_json_list(r["project_ids"]),
            "tags": _parse_json_list(r["tags"]),
            "note": r["user_note"] or "",
            "chars": r["chars"] or 0,
        })
    return out


def update_document(cfg: Config, doc_id: str, *,
                    title: str | None = None,
                    projects: list[str] | None = None,
                    tags: list[str] | None = None,
                    note: str | None = None,
                    model: str = "haiku",
                    verbose: bool = True) -> dict:
    """Update metadata on an existing document. Only the four fields that
    are safe to change post-ingest — title, projects, tags, note — can be
    edited. The file and added_date are intentionally immutable: changing
    the file under the same id would silently invalidate everything that
    referenced the old content; changing the date would shift the cascade
    anchor without the user seeing why narrations suddenly moved.

    Passing None for any field leaves it unchanged. The summary is
    regenerated when title or note changes (both participate in the
    summary input hash). Narrations referencing this doc re-invalidate
    automatically on next pipeline cycle via the existing cascade.
    """
    conn = connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such document: {doc_id}")

        updates: list[str] = []
        params: list = []
        resummarize = False

        if title is not None and title != (row["title"] or ""):
            updates.append("title = ?"); params.append(title)
            resummarize = True

        if note is not None and note != (row["user_note"] or ""):
            updates.append("user_note = ?"); params.append(note)
            resummarize = True

        if projects is not None:
            resolved_pids, unknown_hints = _resolve_projects(conn, projects)
            if unknown_hints and verbose:
                print(f"  warning: unknown project hint(s) ignored: {unknown_hints}")
            new_json = json.dumps(resolved_pids)
            if new_json != (row["project_ids"] or "[]"):
                updates.append("project_ids = ?"); params.append(new_json)

        if tags is not None:
            clean = _clean_tags(tags)
            new_json = json.dumps(clean)
            if new_json != (row["tags"] or "[]"):
                updates.append("tags = ?"); params.append(new_json)

        if not updates:
            if verbose: print(f"  no changes for {doc_id}")
            return {"id": doc_id, "updated": False}

        params.append(doc_id)
        conn.execute(
            f"UPDATE documents SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

        summary_result = None
        if resummarize:
            # Summary hash depends on title + note + text, so changing
            # either field forces a regen. The narration cascade picks up
            # the new summary_hash on its next cycle automatically.
            summary_result = summarize_document(
                conn, doc_id, model=model, force=True, verbose=verbose,
            )

        if verbose:
            print(f"updated {doc_id}: {', '.join(u.split(' = ')[0] for u in updates)}")
        return {
            "id": doc_id, "updated": True,
            "fields_changed": [u.split(" = ")[0] for u in updates],
            "summary": summary_result,
        }
    finally:
        conn.close()


def remove_document(cfg: Config, doc_id: str, *, verbose: bool = True) -> dict:
    """Hard-remove a document and its summary narration. Narrations that
    referenced this doc (dailies on added_date, weeklies containing that
    day, the month's monthly) will invalidate on next pipeline run because
    their input hashes include the set of docs — their cascade handles it.
    """
    conn = connect(cfg.db_path)
    try:
        row = conn.execute(
            "SELECT path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such document: {doc_id}")
        path = Path(row["path"])
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute(
            "DELETE FROM narrations WHERE scope='document' AND key = ?",
            (doc_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            if verbose:
                print(f"  warning: couldn't delete {path}: {exc}")
    if verbose:
        print(f"removed {doc_id}")
    return {"id": doc_id, "path": str(path)}


# ── helpers used by downstream cascade (narrate, status) ───────────────────

def docs_added_on(conn: sqlite3.Connection, date: str,
                  project_id: str | None = None) -> list[dict]:
    """Docs with added_date = `date`. If project_id is given, filter to
    docs whose project_ids JSON array contains that id. Returns each doc
    with its parsed summary (from narrations) for use by the narrator.
    """
    rows = conn.execute(
        """SELECT d.id, d.title, d.user_note, d.project_ids, d.tags,
                  n.prose AS summary_json, n.input_hash AS summary_hash
           FROM documents d
           LEFT JOIN narrations n
             ON n.scope='document' AND n.key=d.id
           WHERE d.added_date = ?
           ORDER BY d.added_at""",
        (date,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        pids = _parse_json_list(r["project_ids"])
        if project_id is not None and project_id not in pids:
            continue
        summary: dict = {}
        try:
            if r["summary_json"]:
                summary = json.loads(r["summary_json"])
        except json.JSONDecodeError:
            summary = {}
        out.append({
            "id": r["id"],
            "title": r["title"] or r["id"],
            "user_note": r["user_note"] or "",
            "project_ids": pids,
            "tags": _parse_json_list(r["tags"]),
            "summary": summary,
            "summary_hash": r["summary_hash"] or "",
        })
    return out


def docs_summary_hash_contribution(docs: list[dict]) -> bytes:
    """Bytes to feed into the daily narration's input hash. Sorted by id
    so order within a day can't change the hash."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda x: x["id"]):
        h.update(d["id"].encode())
        h.update(b"\x00")
        h.update((d.get("summary_hash") or "").encode("utf-8", errors="replace"))
        h.update(b"\x01")
    return h.digest()
