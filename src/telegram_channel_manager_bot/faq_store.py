import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

STATUS_NEW = "new"
STATUS_SKIPPED = "skipped"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVING = "approving"
STATUS_POSTED = "posted"
STATUS_DECLINED = "declined"
STATUS_COMMENT_DELETED = "comment_deleted"
STATUS_COMMENT_EDITED = "comment_edited"
STATUS_FAILED = "failed"

MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    author_id INTEGER,
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    faq_ids TEXT,
    partial INTEGER,
    answer TEXT,
    card_message_id INTEGER,
    reply_message_id INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (chat_id, message_id)
)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FaqStore:
    """SQLite-backed comment queue. Every status change is one atomic `UPDATE ... WHERE id=? AND status=?`."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, autocommit=True)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SCHEMA)
        logger.info("FAQ store opened at {}", path)

    def insert_new(
        self, chat_id: int, message_id: int, thread_id: int, author_id: int | None, author_name: str, text: str
    ) -> int | None:
        """Returns the new row id, or None if this comment is already stored."""
        now = _now()
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO comments"
            " (chat_id, message_id, thread_id, author_id, author_name, text, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, message_id, thread_id, author_id, author_name, text, STATUS_NEW, now, now),
        )
        return cursor.lastrowid if cursor.rowcount else None

    def get(self, row_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM comments WHERE id = ?", (row_id,)).fetchone()

    def get_by_message(self, chat_id: int, message_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM comments WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        ).fetchone()

    def list_by_status(self, status: str, limit: int = -1) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM comments WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
        ).fetchall()

    def transition(self, row_id: int, from_status: str, to_status: str, **fields: Any) -> bool:
        """Moves the row from `from_status` to `to_status`, setting `fields` too.

        Returns False if the row was not in `from_status`, i.e. someone else already moved it.
        """
        assignments = "".join(f", {column} = ?" for column in fields)
        cursor = self._conn.execute(
            f"UPDATE comments SET status = ?, updated_at = ?{assignments} WHERE id = ? AND status = ?",
            (to_status, _now(), *fields.values(), row_id, from_status),
        )
        return cursor.rowcount == 1

    def update_new_text(self, row_id: int, text: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE comments SET text = ?, updated_at = ? WHERE id = ? AND status = ?",
            (text, _now(), row_id, STATUS_NEW),
        )
        return cursor.rowcount == 1

    def record_failure(self, row_id: int, error: str) -> str | None:
        """Counts a failed worker attempt on a `new` row. Returns the resulting status, or None if not `new`."""
        row = self._conn.execute(
            "UPDATE comments SET attempts = attempts + 1, last_error = ?, updated_at = ?,"
            " status = CASE WHEN attempts + 1 >= ? THEN ? ELSE status END"
            " WHERE id = ? AND status = ? RETURNING status",
            (error, _now(), MAX_ATTEMPTS, STATUS_FAILED, row_id, STATUS_NEW),
        ).fetchone()
        return row["status"] if row else None
