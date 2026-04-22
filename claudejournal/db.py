import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    cwd TEXT,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    jsonl_path TEXT,
    jsonl_mtime REAL,
    jsonl_size INTEGER,
    inputs_signature TEXT,
    has_main_transcript INTEGER DEFAULT 0,
    subagent_count INTEGER DEFAULT 0,
    started_at TEXT,
    ended_at TEXT,
    event_count INTEGER DEFAULT 0,
    user_prompt_count INTEGER DEFAULT 0,
    tool_use_count INTEGER DEFAULT 0,
    correction_count INTEGER DEFAULT 0,
    extracted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    project_id TEXT,
    ts TEXT,
    date TEXT,
    kind TEXT,
    tool_name TEXT,
    path TEXT,
    summary TEXT,
    sentiment REAL,
    raw_uuid TEXT,
    source TEXT DEFAULT 'main'
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_project_date ON events(project_id, date);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS interludes (
    date TEXT PRIMARY KEY,
    form TEXT,
    prose TEXT,
    generated_at TEXT,
    model TEXT
);

CREATE TABLE IF NOT EXISTS narrations (
    scope TEXT,          -- 'daily' or 'project_day'
    key TEXT,            -- date for daily; 'project_id|date' for project_day
    date TEXT,
    project_id TEXT,
    prose TEXT,
    prompt_version TEXT,
    generated_at TEXT,
    model TEXT,
    PRIMARY KEY (scope, key)
);
CREATE INDEX IF NOT EXISTS idx_narr_date ON narrations(date);
CREATE INDEX IF NOT EXISTS idx_narr_project_date ON narrations(project_id, date);

CREATE TABLE IF NOT EXISTS session_briefs (
    session_id TEXT,
    date TEXT,
    project_id TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    brief_json TEXT,
    generated_at TEXT,
    cost_usd REAL,
    model TEXT,
    PRIMARY KEY (session_id, date)
);
CREATE INDEX IF NOT EXISTS idx_briefs_date ON session_briefs(date);
CREATE INDEX IF NOT EXISTS idx_briefs_project_date ON session_briefs(project_id, date);
CREATE INDEX IF NOT EXISTS idx_briefs_session ON session_briefs(session_id);

CREATE TABLE IF NOT EXISTS assistant_snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    project_id TEXT,
    ts TEXT,
    date TEXT,
    text TEXT,
    raw_uuid TEXT
);
CREATE INDEX IF NOT EXISTS idx_snippets_session ON assistant_snippets(session_id);
CREATE INDEX IF NOT EXISTS idx_snippets_date ON assistant_snippets(date);

CREATE TABLE IF NOT EXISTS files_touched (
    project_id TEXT,
    date TEXT,
    path TEXT,
    touch_count INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, date, path)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema. SQLite is permissive —
    adding a nullable column with a default is safe on populated tables."""
    def has_col(table: str, col: str) -> bool:
        return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))
    if not has_col("events", "source"):
        conn.execute("ALTER TABLE events ADD COLUMN source TEXT DEFAULT 'main'")
    if not has_col("sessions", "inputs_signature"):
        conn.execute("ALTER TABLE sessions ADD COLUMN inputs_signature TEXT")
    if not has_col("sessions", "has_main_transcript"):
        conn.execute("ALTER TABLE sessions ADD COLUMN has_main_transcript INTEGER DEFAULT 0")
    if not has_col("sessions", "subagent_count"):
        conn.execute("ALTER TABLE sessions ADD COLUMN subagent_count INTEGER DEFAULT 0")
    if not has_col("narrations", "input_hash"):
        conn.execute("ALTER TABLE narrations ADD COLUMN input_hash TEXT")

    # Promote session_briefs PK from session_id to (session_id, date). This
    # lets a long-running Claude session produce one brief per active day
    # instead of one per session. Old rows are preserved verbatim — their
    # original `date` field becomes the second half of the new composite
    # key. Detection is via PRAGMA index_list: the old PK shows up as a
    # sqlite_autoindex with a single column; the new one has two.
    def _briefs_pk_is_composite() -> bool:
        rows = list(conn.execute("PRAGMA table_info(session_briefs)"))
        pk_cols = [r["name"] for r in rows if r["pk"]]
        return len(pk_cols) == 2 and "session_id" in pk_cols and "date" in pk_cols
    if not _briefs_pk_is_composite():
        conn.executescript("""
            CREATE TABLE session_briefs_new (
                session_id TEXT,
                date TEXT,
                project_id TEXT,
                prompt_version TEXT,
                input_hash TEXT,
                brief_json TEXT,
                generated_at TEXT,
                cost_usd REAL,
                model TEXT,
                PRIMARY KEY (session_id, date)
            );
            INSERT INTO session_briefs_new
                (session_id, date, project_id, prompt_version, input_hash,
                 brief_json, generated_at, cost_usd, model)
            SELECT session_id, COALESCE(date, ''), project_id, prompt_version,
                   input_hash, brief_json, generated_at, cost_usd, model
            FROM session_briefs;
            DROP TABLE session_briefs;
            ALTER TABLE session_briefs_new RENAME TO session_briefs;
            CREATE INDEX IF NOT EXISTS idx_briefs_date
                ON session_briefs(date);
            CREATE INDEX IF NOT EXISTS idx_briefs_project_date
                ON session_briefs(project_id, date);
            CREATE INDEX IF NOT EXISTS idx_briefs_session
                ON session_briefs(session_id);
        """)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def session_is_current(conn: sqlite3.Connection, session_id: str, signature: str) -> bool:
    row = conn.execute(
        "SELECT inputs_signature FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return False
    return row["inputs_signature"] == signature


def clear_session_events(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM assistant_snippets WHERE session_id = ?", (session_id,))
