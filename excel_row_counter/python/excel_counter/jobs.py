import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class JobStore:
    """Persistent single-app queue; each processing stage is leased atomically."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                    message_id TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
                    file_key TEXT NOT NULL, filename TEXT NOT NULL,
                    phase TEXT NOT NULL, result_text TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    not_before REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(phase, not_before, lease_until);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def enqueue(self, event_id, message_id, chat_id, file_key, filename, result_text=None):
        now = time.time()
        with self.connection() as conn:
            cursor = conn.execute("""INSERT OR IGNORE INTO jobs
                (id,event_id,message_id,chat_id,file_key,filename,phase,result_text,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), event_id, message_id, chat_id, file_key, filename,
                 "result" if result_text else "receipt", result_text, now, now))
            return cursor.rowcount == 1

    def claim(self, phases, *, now=None, lease_seconds=300):
        now = time.time() if now is None else now
        placeholders = ",".join("?" for _ in phases)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"""SELECT * FROM jobs WHERE phase IN ({placeholders})
                AND not_before<=? AND lease_until<=? ORDER BY created_at LIMIT 1""",
                (*phases, now, now)).fetchone()
            if row is None:
                return None
            lease_token = str(uuid.uuid4())
            conn.execute("""UPDATE jobs SET lease_token=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE id=?""",
                (lease_token, now + lease_seconds, now, row["id"]))
            return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def advance(self, item, phase, result_text=None):
        with self.connection() as conn:
            cursor = conn.execute("""UPDATE jobs SET phase=?, result_text=COALESCE(?,result_text),
                attempts=0, not_before=0, lease_until=0, lease_token=NULL,
                last_error=NULL, updated_at=? WHERE id=? AND lease_token=?""",
                (phase, result_text, time.time(), item["id"], item["lease_token"]))
            return cursor.rowcount == 1

    def retry(self, item, error_type, *, now=None):
        now = time.time() if now is None else now
        phase = "notification_failed" if item["attempts"] >= 5 else item["phase"]
        with self.connection() as conn:
            conn.execute("""UPDATE jobs SET phase=?, not_before=?, lease_until=0,
                lease_token=NULL,last_error=?,updated_at=? WHERE id=? AND lease_token=?""",
                (phase, now + min(2 ** item["attempts"], 60), error_type[:80], now,
                 item["id"], item["lease_token"]))
