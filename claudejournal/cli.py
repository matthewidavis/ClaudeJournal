import argparse
import sys
from pathlib import Path

# Windows console defaults to cp1252; force UTF-8 so em-dashes etc. don't crash.
for stream in (sys.stdout, sys.stderr):
    reconfig = getattr(stream, "reconfigure", None)
    if reconfig:
        try:
            reconfig(encoding="utf-8", errors="replace")
        except Exception:
            pass

from claudejournal import config as cfgmod
from claudejournal import brief as briefmod
from claudejournal import narrate as narratemod
from claudejournal.narrator import ClaudeCodeNarrator
from claudejournal.render import render_site
from claudejournal.scan import scan
from claudejournal.summary import overall_stats, summarize_day, summarize_range, today_iso


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claudejournal", description="Local Claude Code activity → diary")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json (default: repo config.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Discover + extract events into the local DB")
    p_scan.add_argument("--force", action="store_true", help="Re-process sessions even if unchanged")
    p_scan.add_argument("--quiet", action="store_true")

    p_sum = sub.add_parser("summary", help="Print activity summary")
    p_sum.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    p_sum.add_argument("--days", type=int, default=None, help="Print last N days instead of one date")

    sub.add_parser("stats", help="Print DB-wide stats")

    p_brief = sub.add_parser("brief", help="Generate structured session briefs via Claude Code CLI")
    g = p_brief.add_mutually_exclusive_group()
    g.add_argument("--session", help="Single session ID")
    g.add_argument("--date", help="All sessions on YYYY-MM-DD")
    g.add_argument("--all", action="store_true", help="All sessions (ignores default 7-day window)")
    p_brief.add_argument("--force", action="store_true", help="Re-generate even if cached")
    p_brief.add_argument("--dry-run", action="store_true", help="Print prompts, send nothing")
    p_brief.add_argument("--model", default="haiku", help="Claude model alias (haiku, sonnet, opus)")
    p_brief.add_argument("--min-events", type=int, default=5, help="Skip sessions with fewer events")
    p_brief.add_argument("--workers", type=int, default=None, help="Parallel briefs (default: config.max_workers)")

    p_narr = sub.add_parser("narrate", help="Generate diary-voice prose from session briefs")
    p_narr.add_argument("--date", help="YYYY-MM-DD (default: last 7 days)")
    p_narr.add_argument("--all", action="store_true", help="All dates with briefs")
    p_narr.add_argument("--daily-only", action="store_true", help="Only full-day narrations")
    p_narr.add_argument("--project-only", action="store_true", help="Only per-project-day narrations")
    p_narr.add_argument("--force", action="store_true")
    p_narr.add_argument("--dry-run", action="store_true")
    p_narr.add_argument("--model", default="sonnet", help="Narration model (default: sonnet)")

    p_render = sub.add_parser("render", help="Generate static HTML site under out/")
    p_render.add_argument("--out", type=Path, default=None, help="Output dir (default: repo out/)")

    p_serve = sub.add_parser("serve", help="Serve the generated site on localhost")
    p_serve.add_argument("--out", type=Path, default=None)
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--host", default="0.0.0.0",
                         help="Bind address (default 0.0.0.0 — all interfaces; use 127.0.0.1 for loopback only)")

    sub.add_parser("stop", help="Stop a running claudejournal serve process")

    p_run = sub.add_parser("run", help="Full pipeline: scan -> brief -> narrate -> index -> render")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--skip-narration", action="store_true", help="Skip the narration stage")
    p_run.add_argument("--quiet", action="store_true")

    p_inter = sub.add_parser("interludes", help="Generate creative interludes for empty days")
    p_inter.add_argument("--date", help="Just one date")
    p_inter.add_argument("--force", action="store_true")
    p_inter.add_argument("--model", default="haiku")

    p_monthly = sub.add_parser("monthly", help="Monthly retrospective narrations (fed weekly rollups)")
    p_monthly.add_argument("--month", default=None, help="YYYY-MM (default: current month)")
    p_monthly.add_argument("--all", action="store_true", help="All months with activity")
    p_monthly.add_argument("--force", action="store_true")
    p_monthly.add_argument("--model", default=None)

    p_rollup = sub.add_parser("rollup", help="Weekly retrospective narrations")
    grp = p_rollup.add_mutually_exclusive_group()
    grp.add_argument("--week", help="ISO week like 2026-W15")
    grp.add_argument("--all", action="store_true", help="All weeks with daily narrations")
    p_rollup.add_argument("--force", action="store_true")
    p_rollup.add_argument("--model", default=None)

    p_status = sub.add_parser("status", help="What would 'run' do right now? (dry-check)")
    p_status.add_argument("--json", action="store_true", help="JSON output")

    p_sched = sub.add_parser("schedule", help="Print the install command for your OS's scheduler")
    p_sched.add_argument("--hour", type=int, default=None)
    p_sched.add_argument("--minute", type=int, default=None)

    sub.add_parser("index", help="Build / rebuild the RAG search index from narrations + briefs + memory")

    sub.add_parser("mcp", help="Run as an MCP server over stdio (exposes journal to Claude Code + other MCP clients)")

    p_audio = sub.add_parser("audio", help="Pre-render daily + weekly narrations to WAV via Piper TTS")
    p_audio.add_argument("--voice", default="en_US-libritts-high",
                         help="Piper voice id (default en_US-libritts-high)")
    p_audio.add_argument("--out", type=Path, default=None, help="Output dir (default: repo out/)")

    p_ask = sub.add_parser("ask", help="Ask a question against the journal corpus")
    p_ask.add_argument("question", nargs="+", help="Question text")
    p_ask.add_argument("--model", default="sonnet")
    p_ask.add_argument("--k", type=int, default=8, help="Retrieval breadth")

    p_arc = sub.add_parser("arc", help="Manage project arc narration pages")
    arc_sub = p_arc.add_subparsers(dest="arc_cmd", required=True)

    p_arc_list = arc_sub.add_parser("list", help="List all projects and arc status")
    p_arc_list.add_argument("--json", action="store_true")

    p_arc_sum = arc_sub.add_parser("summarize", help="Regenerate one or all project arc pages")
    p_arc_sum.add_argument("project", nargs="?", default=None,
                           help="Project name or id (omit with --all for full sweep)")
    p_arc_sum.add_argument("--all", action="store_true", help="Regenerate all projects")
    p_arc_sum.add_argument("--force", action="store_true",
                           help="Re-generate even if hash matches")
    p_arc_sum.add_argument("--model", default=None,
                           help="Claude model (default: config.arc_model)")

    p_topic = sub.add_parser("topic", help="Manage topic narration pages")
    topic_sub = p_topic.add_subparsers(dest="topic_cmd", required=True)

    p_topic_list = topic_sub.add_parser("list", help="List all qualifying tags with page status")
    p_topic_list.add_argument("--json", action="store_true")

    topic_sub.add_parser("list-pending", help="List tags that qualify but have no current page")

    p_topic_sum = topic_sub.add_parser("summarize", help="Regenerate one or all topic pages")
    p_topic_sum.add_argument("tag", nargs="?", default=None,
                             help="Tag name (omit to run all with --all)")
    p_topic_sum.add_argument("--all", action="store_true", help="Regenerate all qualifying tags")
    p_topic_sum.add_argument("--force", action="store_true",
                             help="Re-generate even if hash matches")
    p_topic_sum.add_argument("--model", default=None,
                             help="Claude model (default: config.topic_model)")

    p_doc = sub.add_parser("doc", help="Manage curated external documents")
    doc_sub = p_doc.add_subparsers(dest="doc_cmd", required=True)

    p_doc_add = doc_sub.add_parser("add", help="Ingest a file into the library")
    p_doc_add.add_argument("path", type=Path, help="File to add (.pdf .md .txt .html)")
    p_doc_add.add_argument("--title", default=None,
                           help="Display title (default: filename stem)")
    p_doc_add.add_argument("--project", action="append", default=[],
                           help="Attach to project (display name or id). Repeat for multiple.")
    p_doc_add.add_argument("--tag", action="append", default=[],
                           help="Tag (lowercase). Repeat for multiple.")
    p_doc_add.add_argument("--note", default="",
                           help="Why you're adding it. Used verbatim by the narrator.")
    p_doc_add.add_argument("--model", default="haiku",
                           help="Claude model for the summary (default: haiku)")

    p_doc_list = doc_sub.add_parser("list", help="Show all documents in the library")
    p_doc_list.add_argument("--json", action="store_true")

    p_doc_rm = doc_sub.add_parser("remove", help="Hard-delete a document (cascade regenerates narrations)")
    p_doc_rm.add_argument("id", help="Document id (first 10 hex chars from `doc list`)")

    args = parser.parse_args(argv)
    cfg = cfgmod.load(args.config)

    if args.cmd == "scan":
        if not args.quiet:
            print(f"scanning {cfg.claude_home}")
        result = scan(cfg, force=args.force, verbose=not args.quiet)
        print(
            f"done: {result['projects']} projects, "
            f"{result['sessions_scanned']} scanned, "
            f"{result['sessions_skipped']} skipped, "
            f"{result['events_written']} events, "
            f"{result.get('snippets_written', 0)} snippets"
            + (f", {result['errors']} errors" if result["errors"] else "")
        )
        return 0

    if args.cmd == "summary":
        if args.days:
            print(summarize_range(cfg.db_path, args.days))
        else:
            print(summarize_day(cfg.db_path, args.date or today_iso()))
        return 0

    if args.cmd == "stats":
        print(overall_stats(cfg.db_path))
        return 0

    if args.cmd == "brief":
        narrator = ClaudeCodeNarrator(model=args.model)
        result = briefmod.run(
            cfg, narrator=narrator,
            session_id=args.session, date=args.date, all_=args.all,
            force=args.force, dry_run=args.dry_run,
            min_events=args.min_events,
            max_workers=args.workers,
        )
        print(f"done: {result['generated']} generated, {result['skipped']} skipped, "
              f"{result['errors']} errors")
        return 0

    if args.cmd == "narrate":
        narrator = ClaudeCodeNarrator(narration_model=args.model)
        result = narratemod.run(
            cfg, narrator=narrator,
            date=args.date, all_=args.all,
            daily_only=args.daily_only, project_only=args.project_only,
            force=args.force, dry_run=args.dry_run,
        )
        print(f"done: {result['daily_generated']} daily + "
              f"{result['project_day_generated']} project-day, "
              f"{result['skipped']} skipped, {result['errors']} errors")
        return 0

    if args.cmd == "render":
        out = args.out or (Path(__file__).resolve().parent.parent / "out")
        stats = render_site(cfg.db_path, out, cfg.claude_home)
        print(f"rendered: feed + {stats['project_index']} project feeds + "
              f"{stats['project_day']} project-day pages + {stats.get('weekly',0)} weekly + "
              f"{stats.get('monthly',0)} monthly + {stats.get('docs',0)} documents + "
              f"{stats.get('daily_redirect',0)} redirect stubs -> {out}")
        return 0

    if args.cmd == "serve":
        # Lazy-imported here so quick CLI commands (status, summary, etc) don't
        # pay the cost of pulling in the http stack on every invocation.
        import http.server, json as _json, os, socketserver, subprocess, sys, threading, time
        from claudejournal import chat as chatmod
        from claudejournal import status as statusmod
        from claudejournal.db import connect
        out = args.out or (Path(__file__).resolve().parent.parent / "out")
        if not out.exists():
            print(f"no site at {out} — run 'claudejournal render' first")
            return 1
        pid_file = cfg.db_path.parent / "serve.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{os.getpid()}:{args.port}", encoding="utf-8")

        # Shared refresh state — at most one pipeline runs at a time.
        refresh_state = {
            "running": False, "started_at": None, "finished_at": None,
            "error": None, "result": None,
            "stage": None, "done": 0, "total": 0, "label": "",
        }
        refresh_lock = threading.Lock()

        def _progress(stage, done, total, label=""):
            with refresh_lock:
                refresh_state["stage"] = stage
                refresh_state["done"] = done
                refresh_state["total"] = total
                refresh_state["label"] = label

        def _run_pipeline_bg():
            with refresh_lock:
                refresh_state.update({
                    "running": True, "started_at": time.time(),
                    "finished_at": None, "error": None, "result": None,
                    "stage": "starting", "done": 0, "total": 0, "label": "",
                })
            result_msg: str | None = None
            error_msg: str | None = None
            try:
                from claudejournal import pipeline
                stats = pipeline.run_all(
                    cfg,
                    brief_model=cfg.brief_model,
                    narration_model=cfg.narration_model,
                    min_events=cfg.min_events_for_brief,
                    verbose=False, progress=_progress,
                )
                result_msg = f"done in {stats['duration_sec']:.0f}s"
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"[:500]
            finally:
                # Single locked update so a concurrent /api/refresh GET can't
                # observe an "in-between" state where running is still True
                # but result/error have already been written.
                with refresh_lock:
                    refresh_state["result"] = result_msg
                    refresh_state["error"] = error_msg
                    refresh_state["running"] = False
                    refresh_state["finished_at"] = time.time()
                    refresh_state["stage"] = None

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, fmt, *a):  # quieter logs
                pass

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass  # client went away — not actionable

            def end_headers(self):
                # Cross-origin isolation so SharedArrayBuffer is available —
                # required by onnxruntime-web multi-threaded wasm used by the
                # in-browser TTS (vits-web).
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
                self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
                super().end_headers()

            def do_GET(self):
                if self.path == "/api/status":
                    try:
                        self._reply(statusmod.check(cfg))
                    except Exception as exc:
                        self._reply({"error": str(exc)}, 500)
                    return
                if self.path == "/api/refresh":
                    with refresh_lock:
                        snapshot = dict(refresh_state)
                    self._reply(snapshot)
                    return
                if self.path == "/api/schedule":
                    try:
                        from claudejournal import schedule as sch
                        self._reply(sch.status())
                    except Exception as exc:
                        self._reply({"installed": False, "error": str(exc)}, 500)
                    return
                if self.path == "/api/docs":
                    try:
                        from claudejournal import docs as docsmod
                        from claudejournal.db import connect as _c
                        _conn = _c(cfg.db_path)
                        try:
                            # Also return the project list so the modal can
                            # populate its picker without a second round-trip.
                            project_rows = _conn.execute(
                                "SELECT id, display_name FROM projects ORDER BY display_name"
                            ).fetchall()
                            projects = [
                                {"id": r["id"], "name": r["display_name"]}
                                for r in project_rows
                            ]
                        finally:
                            _conn.close()
                        self._reply({
                            "documents": docsmod.list_documents(cfg),
                            "projects": projects,
                        })
                    except Exception as exc:
                        self._reply({"error": str(exc)}, 500)
                    return
                # /api/docs/<id>/file — stream the original file as a
                # download. Routed through the API (not a static mount)
                # so we can set the proper Content-Disposition filename
                # and never expose the db/docs/ directory directly.
                if self.path.startswith("/api/docs/") and self.path.endswith("/file"):
                    doc_id = self.path[len("/api/docs/"):-len("/file")]
                    if not doc_id or "/" in doc_id:
                        self.send_error(400); return
                    try:
                        from claudejournal.db import connect as _c
                        _conn = _c(cfg.db_path)
                        try:
                            row = _conn.execute(
                                "SELECT path, original_filename, ext FROM documents WHERE id = ?",
                                (doc_id,),
                            ).fetchone()
                        finally:
                            _conn.close()
                        if not row:
                            self.send_error(404); return
                        from pathlib import Path as _Path
                        fp = _Path(row["path"])
                        if not fp.exists():
                            self.send_error(404); return
                        # Pick a sensible Content-Type — don't guess too
                        # hard, the browser will do the right thing based
                        # on filename + magic for anything we don't cover.
                        ext = (row["ext"] or "").lower()
                        mime = {
                            ".pdf":  "application/pdf",
                            ".md":   "text/markdown; charset=utf-8",
                            ".txt":  "text/plain; charset=utf-8",
                            ".html": "text/html; charset=utf-8",
                        }.get(ext, "application/octet-stream")
                        data = fp.read_bytes()
                        # Force a literal ASCII filename for the header;
                        # browsers are lenient but belt-and-suspenders.
                        fname = (row["original_filename"] or f"{doc_id}{ext}").replace('"', "")
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header(
                            "Content-Disposition",
                            f'attachment; filename="{fname}"',
                        )
                        # Let the cross-origin headers from end_headers()
                        # still apply — keeps SharedArrayBuffer contract.
                        self.end_headers()
                        self.wfile.write(data)
                    except Exception as exc:
                        try: self.send_error(500, str(exc)[:200])
                        except Exception: pass
                    return
                return super().do_GET()

            def do_POST(self):
                if self.path == "/api/ask":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length).decode("utf-8", "replace")
                    try:
                        payload = _json.loads(raw)
                        question = (payload.get("question") or "").strip()
                        if not question:
                            self._reply({"error": "empty question"}, 400); return
                        conn = connect(cfg.db_path)
                        try:
                            result = chatmod.ask(conn, question)
                        finally:
                            conn.close()
                        self._reply({
                            "answer": result.answer,
                            "sources": [
                                {"kind": h.kind, "date": h.date,
                                 "project_name": h.project_name, "title": h.title}
                                for h in result.hits
                            ],
                        })
                    except Exception as exc:
                        self._reply({"error": str(exc)}, 500)
                    return

                if self.path == "/api/refresh":
                    with refresh_lock:
                        if refresh_state["running"]:
                            self._reply({"error": "already running", "running": True}, 409)
                            return
                    threading.Thread(target=_run_pipeline_bg, daemon=True).start()
                    self._reply({"running": True, "started_at": time.time()})
                    return
                if self.path == "/api/docs":
                    # Accept JSON body with a base64-encoded file payload.
                    # Multipart parsing in stdlib was deprecated in 3.12 and
                    # requires pulling in third-party parsers; base64 keeps
                    # the handler terse. 25 MB cap guards against accidents.
                    import base64 as _b64, tempfile as _tf
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 25 * 1024 * 1024:
                        self._reply({"error": "document too large (>25MB). Raise the cap in cli.py if this is legitimate."}, 413)
                        return
                    raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                    try:
                        payload = _json.loads(raw)
                        filename = (payload.get("filename") or "").strip()
                        content_b64 = payload.get("content_base64") or ""
                        if not filename or not content_b64:
                            self._reply({"error": "filename and content_base64 are required"}, 400)
                            return
                        content = _b64.b64decode(content_b64)
                        # Write to a temp file so add_document's path-based
                        # extraction can run unchanged. add_document then
                        # copies into db/docs/ under its own generated id.
                        suffix = Path(filename).suffix or ""
                        with _tf.NamedTemporaryFile(
                            delete=False, suffix=suffix, dir=str(cfg.db_path.parent)
                        ) as tmp:
                            tmp.write(content)
                            tmp_path = Path(tmp.name)
                        try:
                            from claudejournal import docs as docsmod
                            result = docsmod.add_document(
                                cfg, tmp_path,
                                title=(payload.get("title") or "").strip() or None,
                                projects=payload.get("projects") or [],
                                tags=payload.get("tags") or [],
                                note=(payload.get("note") or "").strip(),
                                original_filename=filename,
                                model=(payload.get("model") or "haiku"),
                                verbose=False,
                            )
                        finally:
                            # Source file was copied by add_document — its
                            # own copy lives under db/docs/. Clean up ours.
                            try: tmp_path.unlink()
                            except FileNotFoundError: pass
                        self._reply({"ok": True, "id": result["id"],
                                     "filename": filename,
                                     "chars": result["chars"],
                                     "projects": result["projects"],
                                     "tags": result["tags"],
                                     "added_date": result["added_date"]})
                    except (FileNotFoundError, ValueError, RuntimeError) as exc:
                        self._reply({"error": str(exc)[:500]}, 400)
                    except Exception as exc:
                        self._reply({"error": str(exc)[:500]}, 500)
                    return

                if self.path == "/api/schedule/install":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                    try:
                        payload = _json.loads(raw) if raw else {}
                        hour = int(payload.get("hour", cfg.schedule_hour))
                        minute = int(payload.get("minute", cfg.schedule_minute))
                        from claudejournal import schedule as sch
                        self._reply(sch.install(hour, minute))
                    except Exception as exc:
                        self._reply({"ok": False, "raw": str(exc)}, 500)
                    return
                if self.path == "/api/schedule/uninstall":
                    try:
                        from claudejournal import schedule as sch
                        self._reply(sch.uninstall())
                    except Exception as exc:
                        self._reply({"ok": False, "raw": str(exc)}, 500)
                    return
                self.send_error(404)

            def do_DELETE(self):
                # /api/docs/<id>  — hard-remove a document. Cascade
                # invalidation happens via the hash in narrate.py on
                # the next pipeline cycle; nothing to do here beyond
                # deleting the row + file.
                if self.path.startswith("/api/docs/"):
                    doc_id = self.path.rsplit("/", 1)[-1]
                    if not doc_id:
                        self._reply({"error": "missing document id"}, 400)
                        return
                    try:
                        from claudejournal import docs as docsmod
                        docsmod.remove_document(cfg, doc_id, verbose=False)
                        self._reply({"ok": True, "id": doc_id})
                    except ValueError as exc:
                        self._reply({"error": str(exc)}, 404)
                    except Exception as exc:
                        self._reply({"error": str(exc)[:500]}, 500)
                    return
                self.send_error(404)

            def do_PATCH(self):
                # /api/docs/<id>  — update title / projects / tags / note.
                # File and added_date are intentionally not editable; see
                # update_document() for the reasoning. Summary regenerates
                # in-line when title or note changed.
                if self.path.startswith("/api/docs/"):
                    doc_id = self.path.rsplit("/", 1)[-1]
                    if not doc_id:
                        self._reply({"error": "missing document id"}, 400)
                        return
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                    try:
                        payload = _json.loads(raw)
                        from claudejournal import docs as docsmod
                        result = docsmod.update_document(
                            cfg, doc_id,
                            title=payload.get("title"),
                            projects=payload.get("projects"),
                            tags=payload.get("tags"),
                            note=payload.get("note"),
                            model=(payload.get("model") or "haiku"),
                            verbose=False,
                        )
                        self._reply({"ok": True, **result})
                    except ValueError as exc:
                        self._reply({"error": str(exc)}, 404)
                    except Exception as exc:
                        self._reply({"error": str(exc)[:500]}, 500)
                    return
                self.send_error(404)

            def _reply(self, obj, code=200):
                body = _json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # Auto-register our MCP server with the local `claude` CLI. Self-
        # heals three environment shapes:
        #   - in a venv with the package pip-installed (common)
        #   - system python without venv (package must be installed)
        #   - running from repo dir only (cwd-on-sys.path accident; breaks
        #     when Claude spawns the MCP from another cwd)
        # We verify import from a foreign cwd and pip install -e . if needed.
        try:
            import shutil as _shutil, tempfile as _tempfile
            repo_root = Path(__file__).resolve().parent.parent
            py = sys.executable
            in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
            env_label = f"venv ({py})" if in_venv else f"system python ({py})"

            # 1) Can `py -m claudejournal --help` run from an unrelated cwd?
            probe = subprocess.run(
                [py, "-c", "import claudejournal"],
                cwd=_tempfile.gettempdir(),
                capture_output=True, text=True, timeout=15,
            )
            if probe.returncode != 0:
                print(f"mcp: claudejournal not importable from {env_label} — installing...")
                inst = subprocess.run(
                    [py, "-m", "pip", "install", "-e", str(repo_root)],
                    capture_output=True, text=True, timeout=180,
                )
                if inst.returncode != 0:
                    raise RuntimeError(f"pip install failed: {inst.stderr.strip()[:200]}")
                print("mcp: package installed into", env_label)

            # 2) Register (or re-register) with claude CLI.
            if _shutil.which("claude"):
                listing = subprocess.run(
                    ["claude", "mcp", "list"], capture_output=True, text=True, timeout=10,
                )
                current = listing.stdout or ""
                # Re-register if absent OR if previously registered command
                # doesn't match this python (catches moved venvs, switched
                # interpreters, previous bad registrations).
                needs_reg = ("claudejournal" not in current) or (py not in current)
                if needs_reg:
                    subprocess.run(
                        ["claude", "mcp", "remove", "claudejournal", "--scope", "user"],
                        capture_output=True, text=True, timeout=10,
                    )
                    reg = subprocess.run(
                        ["claude", "mcp", "add", "claudejournal",
                         "--scope", "user", "--", py, "-m", "claudejournal", "mcp"],
                        capture_output=True, text=True, timeout=15, cwd=str(repo_root),
                    )
                    if reg.returncode == 0:
                        print(f"mcp: registered with claude CLI ({env_label})")
                    else:
                        print(f"mcp: registration failed — {(reg.stderr or reg.stdout).strip()[:200]}")
                else:
                    print(f"mcp: already registered ({env_label})")
            else:
                print("mcp: 'claude' CLI not on PATH — skipping registration")
        except Exception as exc:
            print(f"mcp: skipped — {exc}")

        os.chdir(out)
        try:
            with socketserver.ThreadingTCPServer((args.host, args.port), Handler) as httpd:
                httpd.daemon_threads = True
                shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
                print(f"serving {out} at http://{shown}:{args.port}/ (bind={args.host})  (chat API at /api/ask)  (ctrl-c or 'claudejournal stop' to exit)")
                try:
                    httpd.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            try: pid_file.unlink()
            except FileNotFoundError: pass
        return 0

    if args.cmd == "run":
        from claudejournal import pipeline
        stats = pipeline.run_all(
            cfg,
            brief_model=cfg.brief_model,
            narration_model=cfg.narration_model,
            min_events=cfg.min_events_for_brief,
            force=args.force,
            skip_narration=args.skip_narration,
            verbose=not args.quiet,
        )
        print(f"pipeline done in {stats['duration_sec']:.1f}s")
        return 0

    if args.cmd == "interludes":
        from claudejournal import interludes as interludemod
        stats = interludemod.run(cfg, date=args.date, force=args.force, model=args.model)
        print(f"done: {stats['generated']} generated, {stats['skipped']} skipped, "
              f"{stats['rejected']} rejected, {stats['errors']} errors")
        return 0

    if args.cmd == "rollup":
        from claudejournal import rollup
        stats = rollup.run(
            cfg,
            iso_week=args.week, all_=args.all,
            model=args.model or cfg.rollup_model,
            force=args.force,
        )
        print(f"done: {stats['generated']} generated, {stats['skipped']} skipped, {stats['errors']} errors")
        return 0

    if args.cmd == "monthly":
        from claudejournal import monthly
        stats = monthly.run(
            cfg,
            year_month=args.month, all_=args.all,
            model=args.model or cfg.rollup_model,
            force=args.force,
        )
        print(f"done: {stats['generated']} generated, {stats['skipped']} skipped, {stats['errors']} errors")
        return 0

    if args.cmd == "status":
        from claudejournal import status as statusmod
        import json as _json
        result = statusmod.check(cfg)
        if args.json:
            print(_json.dumps(result, indent=2))
        else:
            print(statusmod.format_status(result))
        return 0

    if args.cmd == "schedule":
        from claudejournal.schedule import hint_for_platform
        print(hint_for_platform(
            hour=args.hour if args.hour is not None else cfg.schedule_hour,
            minute=args.minute if args.minute is not None else cfg.schedule_minute,
        ))
        return 0

    if args.cmd == "index":
        from claudejournal.db import connect
        from claudejournal import rag
        conn = connect(cfg.db_path)
        stats = rag.reindex(conn, cfg.claude_home, verbose=True)
        conn.close()
        print(f"indexed: {sum(stats.values())} chunks ({stats})")
        return 0

    if args.cmd == "mcp":
        from claudejournal import mcp_server
        mcp_server.run_stdio()
        return 0

    if args.cmd == "audio":
        from claudejournal import audio as audiomod
        out = args.out or (Path(__file__).resolve().parent.parent / "out")
        out.mkdir(parents=True, exist_ok=True)
        try:
            stats = audiomod.generate_for_site(cfg, out, voice=args.voice, verbose=True)
        except RuntimeError as exc:
            print(str(exc)); return 1
        print(f"audio: made={stats['made']} skipped={stats['skipped']} "
              f"failed={stats['failed']} voice={stats['voice']} -> {stats['audio_dir']}")
        return 0

    if args.cmd == "ask":
        from claudejournal.db import connect
        from claudejournal import chat as chatmod
        conn = connect(cfg.db_path)
        question = " ".join(args.question)
        try:
            result = chatmod.ask(conn, question, model=args.model, k=args.k)
        finally:
            conn.close()
        print(result.answer)
        if result.hits:
            print()
            print(f"[{len(result.hits)} sources: " +
                  ", ".join(f"{h.kind}@{h.date or '-'}" for h in result.hits) + "]")
        return 0

    if args.cmd == "arc":
        from claudejournal import arcs as arcsmod
        from claudejournal.db import connect as _connect
        import json as _json
        conn = _connect(cfg.db_path)
        try:
            if args.arc_cmd == "list":
                items = arcsmod.list_arcs(conn)
                if args.json:
                    print(_json.dumps(items, indent=2))
                else:
                    if not items:
                        print("(no projects with project_day narrations yet)")
                    else:
                        print(f"{'project':45s}  {'status':8s}  {'days':4s}  generated_at")
                        for it in items:
                            ga = (it["generated_at"] or "-")[:19]
                            pname = it["project_name"][:43]
                            print(f"  {pname:43s}  {it['status']:8s}  {it['days']:4d}  {ga}")
                        print(f"\n{len(items)} projects")
            elif args.arc_cmd == "summarize":
                model = args.model or cfg.arc_model
                if args.all or args.project is None:
                    stats = arcsmod.run(cfg, all_=True, force=args.force,
                                        model=model, verbose=True)
                    print(f"done: {stats['generated']} generated, {stats['skipped']} skipped, "
                          f"{stats['errors']} errors")
                else:
                    # Single project — lookup by display name or project_id
                    hint = args.project.strip()
                    prow = conn.execute(
                        "SELECT id, display_name FROM projects WHERE display_name = ? OR id = ?",
                        (hint, hint),
                    ).fetchone()
                    if not prow:
                        # Try case-insensitive
                        prow = conn.execute(
                            "SELECT id, display_name FROM projects WHERE lower(display_name) = ?",
                            (hint.lower(),),
                        ).fetchone()
                    if not prow:
                        print(f"error: no project found matching {hint!r}")
                        return 1
                    s = arcsmod.summarize_arc(conn, prow["id"], prow["display_name"],
                                              model=model, force=args.force, verbose=True)
                    print(f"done: {s['generated']} generated, {s['skipped']} skipped")
        finally:
            conn.close()
        return 0

    if args.cmd == "topic":
        from claudejournal import topics as topicsmod
        from claudejournal.db import connect as _connect
        import json as _json
        conn = _connect(cfg.db_path)
        try:
            if args.topic_cmd == "list":
                items = topicsmod.list_topics(conn, cfg.min_days_for_topic)
                if args.json:
                    print(_json.dumps(items, indent=2))
                else:
                    if not items:
                        print("(no tags qualify yet — need >= %d days each)" % cfg.min_days_for_topic)
                    else:
                        print(f"{'tag':40s}  {'status':8s}  {'days':4s}  generated_at")
                        for it in items:
                            ga = (it["generated_at"] or "-")[:19]
                            print(f"  {it['tag']:38s}  {it['status']:8s}  {it['days']:4d}  {ga}")
                        print(f"\n{len(items)} qualifying tags")
            elif args.topic_cmd == "list-pending":
                pending = topicsmod.list_pending(conn, cfg.min_days_for_topic)
                if not pending:
                    print("(all qualifying tags have current pages)")
                else:
                    for t in pending:
                        print(f"  {t}")
                    print(f"\n{len(pending)} pending")
            elif args.topic_cmd == "summarize":
                model = args.model or cfg.topic_model
                if args.all or args.tag is None:
                    stats = topicsmod.run(cfg, all_=True, force=args.force,
                                          model=model, verbose=True)
                    print(f"done: {stats['generated']} generated, {stats['skipped']} skipped, "
                          f"{stats['errors']} errors")
                else:
                    # Single tag — doesn't need to pass threshold check here
                    s = topicsmod.summarize_topic(conn, args.tag.strip().lower(),
                                                  model=model, force=args.force, verbose=True)
                    print(f"done: {s['generated']} generated, {s['skipped']} skipped")
        finally:
            conn.close()
        return 0

    if args.cmd == "doc":
        from claudejournal import docs as docsmod
        import json as _json
        if args.doc_cmd == "add":
            try:
                result = docsmod.add_document(
                    cfg, args.path,
                    title=args.title,
                    projects=args.project,
                    tags=args.tag,
                    note=args.note,
                    model=args.model,
                    verbose=True,
                )
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                print(f"error: {exc}")
                return 1
            # Extra line so the two-stage output (add + summarize) lands cleanly.
            print(f"done: {result['id']}  summary={result['summary']}")
            return 0
        if args.doc_cmd == "list":
            items = docsmod.list_documents(cfg)
            if args.json:
                print(_json.dumps(items, indent=2))
                return 0
            if not items:
                print("(no documents yet — add one with `claudejournal doc add <file>`)")
                return 0
            # Fixed-width columns keep the list scannable at a glance.
            for it in items:
                projs = ",".join(it["projects"]) or "-"
                tags = ",".join(it["tags"]) or "-"
                print(f"  {it['id']}  {it['added_date']}  "
                      f"{(it['title'] or '')[:40]:40s}  "
                      f"projects={projs}  tags={tags}  ({it['chars']:,}c)")
            return 0
        if args.doc_cmd == "remove":
            try:
                docsmod.remove_document(cfg, args.id, verbose=True)
            except ValueError as exc:
                print(f"error: {exc}")
                return 1
            return 0
        return 1

    if args.cmd == "stop":
        import os, signal
        pid_file = cfg.db_path.parent / "serve.pid"
        if not pid_file.exists():
            print("no serve process recorded (pid file missing)")
            return 1
        raw = pid_file.read_text(encoding="utf-8").strip()
        pid_str = raw.split(":", 1)[0]
        try:
            pid = int(pid_str)
        except ValueError:
            print(f"corrupt pid file: {raw!r}")
            pid_file.unlink(missing_ok=True)
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"stopped pid {pid}")
        except ProcessLookupError:
            print(f"no process {pid} (already stopped)")
        except PermissionError as e:
            print(f"couldn't stop pid {pid}: {e}")
            return 1
        pid_file.unlink(missing_ok=True)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
