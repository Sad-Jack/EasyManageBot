from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from posting_assistant_bot.logging_utils import log_event

PendingPostStatus = Literal["pending", "published"]
PostFormat = Literal["standard", "interview_question"]
ChatRole = Literal["user", "assistant"]
SuggestedTopicStatus = Literal["suggested", "post_generated", "deleted"]
SuggestedTopicSource = Literal["manual_command", "panel_button", "regenerate"]
CommentStatus = Literal["new", "forwarded_to_review", "generating", "reply_generated", "sending", "sent", "ignored", "failed"]
TopicMemoryStatus = Literal["ACTIVE", "ARCHIVED"]


@dataclass(frozen=True)
class ChatHistoryItem:
    role: ChatRole
    content: str
    created_at: str


@dataclass(frozen=True)
class PendingPostRecord:
    id: int
    owner_chat_id: str
    status: PendingPostStatus
    post_format: PostFormat
    post_type_id: str
    source_request: str
    generation_input: str
    source_message_id: int | None
    post_text: str
    preview_message_id: int | None
    target_chat_id: str
    published_chat_id: str | None
    published_message_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SuggestedTopicRecord:
    id: int
    owner_chat_id: str
    owner_id: str
    topic_title: str
    topic_angle: str
    why_write: str
    post_format: PostFormat
    post_type_id: str
    source: SuggestedTopicSource
    status: SuggestedTopicStatus
    topic_message_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CommentRecord:
    id: int
    source_chat_id: str
    source_message_id: int
    source_post_id: int | None
    review_chat_id: str | None
    review_message_id: int | None
    comment_author_id: str
    comment_author_username: str | None
    comment_author_name: str
    comment_text: str
    comment_link: str | None
    generated_reply: str | None
    status: CommentStatus
    reply_mode: str
    sent_message_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TopicMemoryRecord:
    id: int
    source_topic_text: str | None
    topic_hash: str
    topic_summary_compact: str
    topic_keywords_compact: str
    semantic_fingerprint: str | None
    post_id: int | None
    message_id: int | None
    status: TopicMemoryStatus
    created_at: str
    updated_at: str
    last_seen_at: str | None


class AppDatabase:
    def __init__(self, file_path: str) -> None:
        absolute_path = Path(file_path).expanduser().resolve()
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(absolute_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def get_chat_history(self, chat_id: str, limit: int = 12) -> list[ChatHistoryItem]:
        rows = self._connection.execute(
            """
            SELECT role, content, created_at
            FROM chat_messages
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()

        return [
            ChatHistoryItem(
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in reversed(rows)
        ]

    def append_chat_message(self, chat_id: str, role: ChatRole, content: str) -> None:
        self._connection.execute(
            """
            INSERT INTO chat_messages (chat_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, role, content, _now_iso()),
        )
        self._connection.commit()

    def clear_chat_history(self, chat_id: str) -> None:
        self._connection.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
        self._connection.commit()

    def get_setting(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        now = _now_iso()
        self._connection.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        self._connection.commit()

    def set_chat_state(self, chat_id: str, state: str | None) -> None:
        if state is None:
            self._connection.execute("DELETE FROM chat_states WHERE chat_id = ?", (chat_id,))
            self._connection.commit()
            return
        self._connection.execute(
            """
            INSERT INTO chat_states (chat_id, state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at
            """,
            (chat_id, state, _now_iso()),
        )
        self._connection.commit()

    def get_chat_state(self, chat_id: str) -> str | None:
        row = self._connection.execute("SELECT state FROM chat_states WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            return None
        return str(row["state"])

    def create_pending_post(
        self,
        *,
        owner_chat_id: str,
        post_format: PostFormat,
        post_type_id: str,
        source_request: str,
        generation_input: str,
        source_message_id: int | None,
        post_text: str,
        target_chat_id: str,
    ) -> PendingPostRecord:
        try:
            created_at = _now_iso()
            cursor = self._connection.execute(
                """
                INSERT INTO pending_posts (
                  owner_chat_id,
                  status,
                  post_format,
                  post_type_id,
                  source_request,
                  generation_input,
                  source_message_id,
                  post_text,
                  preview_message_id,
                  target_chat_id,
                  published_chat_id,
                  published_message_id,
                  created_at,
                  updated_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
                """,
                (
                    owner_chat_id,
                    post_format,
                    post_type_id,
                    source_request,
                    generation_input,
                    source_message_id,
                    post_text,
                    target_chat_id,
                    created_at,
                    created_at,
                ),
            )
            self._connection.commit()
            created = self.get_pending_post_by_id(int(cursor.lastrowid))
            if created is None:
                raise RuntimeError("Failed to read created pending post back from SQLite")
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="pending_post_created",
                message="Pending post created",
                context={"pending_post_id": created.id, "owner_chat_id": owner_chat_id},
            )
            return created
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to create pending post",
                context={"owner_chat_id": owner_chat_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def get_pending_post_by_id(self, pending_post_id: int) -> PendingPostRecord | None:
        try:
            row = self._connection.execute(
                "SELECT * FROM pending_posts WHERE id = ?",
                (pending_post_id,),
            ).fetchone()
            if row is None:
                return None
            record = _map_pending_post_row(row)
            log_event(
                logging.getLogger(__name__),
                level=logging.DEBUG,
                component="database.sqlite",
                event="pending_post_loaded",
                message="Pending post loaded",
                context={"pending_post_id": pending_post_id, "status": record.status},
            )
            return record
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to load pending post",
                context={"pending_post_id": pending_post_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def list_pending_posts(self, owner_chat_id: str, limit: int = 10) -> list[PendingPostRecord]:
        rows = self._connection.execute(
            """
            SELECT * FROM pending_posts
            WHERE owner_chat_id = ? AND status = 'pending'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (owner_chat_id, limit),
        ).fetchall()
        return [_map_pending_post_row(row) for row in rows]

    def attach_preview_message(self, pending_post_id: int, preview_message_id: int) -> PendingPostRecord | None:
        self._connection.execute(
            """
            UPDATE pending_posts
            SET preview_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (preview_message_id, _now_iso(), pending_post_id),
        )
        self._connection.commit()
        return self.get_pending_post_by_id(pending_post_id)

    def mark_pending_post_published(
        self,
        pending_post_id: int,
        *,
        published_chat_id: str,
        published_message_id: str,
    ) -> PendingPostRecord | None:
        try:
            self._connection.execute(
                """
                UPDATE pending_posts
                SET status = 'published',
                    published_chat_id = ?,
                    published_message_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (published_chat_id, published_message_id, _now_iso(), pending_post_id),
            )
            self._connection.commit()
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="pending_post_published",
                message="Pending post marked as published",
                context={"pending_post_id": pending_post_id},
            )
            return self.get_pending_post_by_id(pending_post_id)
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to mark pending post as published",
                context={"pending_post_id": pending_post_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def update_pending_post_text(self, pending_post_id: int, post_text: str) -> PendingPostRecord | None:
        try:
            self._connection.execute(
                """
                UPDATE pending_posts
                SET post_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (post_text, _now_iso(), pending_post_id),
            )
            self._connection.commit()
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="pending_post_reopened",
                message="Pending post text updated",
                context={"pending_post_id": pending_post_id},
            )
            return self.get_pending_post_by_id(pending_post_id)
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to update pending post text",
                context={"pending_post_id": pending_post_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def close(self) -> None:
        self._connection.close()

    def create_suggested_topic(
        self,
        *,
        owner_chat_id: str,
        owner_id: str,
        topic_title: str,
        topic_angle: str,
        why_write: str,
        source: SuggestedTopicSource,
        post_format: PostFormat = "standard",
        post_type_id: str = "educational_short",
        topic_message_id: int | None = None,
    ) -> SuggestedTopicRecord:
        try:
            created_at = _now_iso()
            cursor = self._connection.execute(
                """
                INSERT INTO suggested_topics (
                  owner_chat_id,
                  owner_id,
                  topic_title,
                  topic_angle,
                  why_write,
                  post_format,
                  post_type_id,
                  source,
                  status,
                  topic_message_id,
                  created_at,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'suggested', ?, ?, ?)
                """,
                (
                    owner_chat_id,
                    owner_id,
                    topic_title,
                    topic_angle,
                    why_write,
                    post_format,
                    post_type_id,
                    source,
                    topic_message_id,
                    created_at,
                    created_at,
                ),
            )
            self._connection.commit()
            record = self.get_suggested_topic_by_id(int(cursor.lastrowid))
            if record is None:
                raise RuntimeError("Failed to read created suggested topic back from SQLite")
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="suggested_topic_created",
                message="Suggested topic created",
                context={"topic_id": record.id, "owner_chat_id": owner_chat_id, "source": source},
            )
            return record
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to create suggested topic",
                context={"owner_chat_id": owner_chat_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def get_suggested_topic_by_id(self, topic_id: int) -> SuggestedTopicRecord | None:
        try:
            row = self._connection.execute("SELECT * FROM suggested_topics WHERE id = ?", (topic_id,)).fetchone()
            if row is None:
                return None
            return _map_suggested_topic_row(row)
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="database.sqlite",
                event="database_error",
                message="Failed to load suggested topic",
                context={"topic_id": topic_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def update_suggested_topic_content(
        self,
        topic_id: int,
        *,
        topic_title: str,
        topic_angle: str,
        why_write: str,
        source: SuggestedTopicSource,
        post_format: PostFormat = "standard",
        post_type_id: str = "educational_short",
    ) -> SuggestedTopicRecord | None:
        self._connection.execute(
            """
            UPDATE suggested_topics
            SET topic_title = ?,
                topic_angle = ?,
                why_write = ?,
                post_format = ?,
                post_type_id = ?,
                source = ?,
                status = 'suggested',
                updated_at = ?
            WHERE id = ?
            """,
            (topic_title, topic_angle, why_write, post_format, post_type_id, source, _now_iso(), topic_id),
        )
        self._connection.commit()
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="database.sqlite",
            event="suggested_topic_updated",
            message="Suggested topic updated",
            context={"topic_id": topic_id, "source": source},
        )
        return self.get_suggested_topic_by_id(topic_id)

    def attach_topic_message(self, topic_id: int, topic_message_id: int) -> SuggestedTopicRecord | None:
        self._connection.execute(
            """
            UPDATE suggested_topics
            SET topic_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (topic_message_id, _now_iso(), topic_id),
        )
        self._connection.commit()
        return self.get_suggested_topic_by_id(topic_id)

    def mark_suggested_topic_status(self, topic_id: int, status: SuggestedTopicStatus) -> SuggestedTopicRecord | None:
        self._connection.execute(
            "UPDATE suggested_topics SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), topic_id),
        )
        self._connection.commit()
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="database.sqlite",
            event="suggested_topic_status_updated",
            message="Suggested topic status updated",
            context={"topic_id": topic_id, "status": status},
        )
        return self.get_suggested_topic_by_id(topic_id)

    def create_or_touch_topic_memory(
        self,
        *,
        source_topic_text: str | None,
        topic_hash: str,
        topic_summary_compact: str,
        topic_keywords_compact: str,
        semantic_fingerprint: str | None,
        post_id: int | None,
        message_id: int | None,
        status: TopicMemoryStatus = "ACTIVE",
    ) -> tuple[TopicMemoryRecord, bool]:
        now = _now_iso()
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO topic_memory (
                  source_topic_text,
                  topic_hash,
                  topic_summary_compact,
                  topic_keywords_compact,
                  semantic_fingerprint,
                  post_id,
                  message_id,
                  status,
                  created_at,
                  updated_at,
                  last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_topic_text,
                    topic_hash,
                    topic_summary_compact,
                    topic_keywords_compact,
                    semantic_fingerprint,
                    post_id,
                    message_id,
                    status,
                    now,
                    now,
                    now,
                ),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM topic_memory WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to read created topic memory row")
            record = _map_topic_memory_row(row)
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="topic_memory_created",
                message="Topic memory created",
                context={"topic_memory_id": record.id, "post_id": post_id},
            )
            return record, True
        except sqlite3.IntegrityError:
            self._connection.execute(
                """
                UPDATE topic_memory
                SET source_topic_text = COALESCE(?, source_topic_text),
                    post_id = COALESCE(?, post_id),
                    message_id = COALESCE(?, message_id),
                    status = ?,
                    updated_at = ?,
                    last_seen_at = ?
                WHERE topic_hash = ?
                """,
                (
                    source_topic_text,
                    post_id,
                    message_id,
                    status,
                    now,
                    now,
                    topic_hash,
                ),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM topic_memory WHERE topic_hash = ?",
                (topic_hash,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to read existing topic memory row")
            record = _map_topic_memory_row(row)
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="database.sqlite",
                event="topic_memory_touched",
                message="Existing topic memory updated as duplicate",
                context={"topic_memory_id": record.id, "post_id": post_id},
            )
            return record, False

    def list_recent_topic_memory(
        self,
        *,
        limit: int = 100,
        status: TopicMemoryStatus = "ACTIVE",
    ) -> list[TopicMemoryRecord]:
        rows = self._connection.execute(
            """
            SELECT *
            FROM topic_memory
            WHERE status = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        return [_map_topic_memory_row(row) for row in rows]

    def create_comment(
        self,
        *,
        source_chat_id: str,
        source_message_id: int,
        source_post_id: int | None,
        comment_author_id: str,
        comment_author_username: str | None,
        comment_author_name: str,
        comment_text: str,
        comment_link: str | None,
        reply_mode: str,
    ) -> CommentRecord:
        created_at = _now_iso()
        cursor = self._connection.execute(
            """
            INSERT INTO comments (
              source_chat_id,
              source_message_id,
              source_post_id,
              review_chat_id,
              review_message_id,
              comment_author_id,
              comment_author_username,
              comment_author_name,
              comment_text,
              comment_link,
              generated_reply,
              status,
              reply_mode,
              sent_message_id,
              created_at,
              updated_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, 'new', ?, NULL, ?, ?)
            """,
            (
                source_chat_id,
                source_message_id,
                source_post_id,
                comment_author_id,
                comment_author_username,
                comment_author_name,
                comment_text,
                comment_link,
                reply_mode,
                created_at,
                created_at,
            ),
        )
        self._connection.commit()
        record = self.get_comment_by_id(int(cursor.lastrowid))
        if record is None:
            raise RuntimeError("Failed to read created comment back from SQLite")
        return record

    def get_comment_by_id(self, comment_id: int) -> CommentRecord | None:
        row = self._connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        if row is None:
            return None
        return _map_comment_row(row)

    def update_comment_review_message(
        self,
        comment_id: int,
        *,
        review_chat_id: str,
        review_message_id: int,
    ) -> CommentRecord | None:
        self._connection.execute(
            """
            UPDATE comments
            SET review_chat_id = ?,
                review_message_id = ?,
                status = 'forwarded_to_review',
                updated_at = ?
            WHERE id = ?
            """,
            (review_chat_id, review_message_id, _now_iso(), comment_id),
        )
        self._connection.commit()
        return self.get_comment_by_id(comment_id)

    def update_comment_generated_reply(self, comment_id: int, reply_text: str) -> CommentRecord | None:
        self._connection.execute(
            """
            UPDATE comments
            SET generated_reply = ?,
                status = 'reply_generated',
                updated_at = ?
            WHERE id = ?
            """,
            (reply_text, _now_iso(), comment_id),
        )
        self._connection.commit()
        return self.get_comment_by_id(comment_id)

    def mark_comment_sent(self, comment_id: int, sent_message_id: int) -> CommentRecord | None:
        self._connection.execute(
            """
            UPDATE comments
            SET status = 'sent',
                sent_message_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (sent_message_id, _now_iso(), comment_id),
        )
        self._connection.commit()
        return self.get_comment_by_id(comment_id)

    def acquire_comment_send_lock(self, comment_id: int) -> CommentRecord | None:
        cursor = self._connection.execute(
            """
            UPDATE comments
            SET status = 'sending',
                updated_at = ?
            WHERE id = ?
              AND generated_reply IS NOT NULL
              AND status IN ('reply_generated', 'failed')
            """,
            (_now_iso(), comment_id),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_comment_by_id(comment_id)

    def mark_comment_status(self, comment_id: int, status: CommentStatus) -> CommentRecord | None:
        self._connection.execute(
            "UPDATE comments SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), comment_id),
        )
        self._connection.commit()
        return self.get_comment_by_id(comment_id)

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              chat_id TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_posts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_chat_id TEXT NOT NULL,
              status TEXT NOT NULL,
              post_format TEXT NOT NULL DEFAULT 'standard',
              post_type_id TEXT NOT NULL DEFAULT 'educational_short',
              source_request TEXT NOT NULL,
              generation_input TEXT NOT NULL DEFAULT '',
              source_message_id INTEGER,
              post_text TEXT NOT NULL,
              preview_message_id INTEGER,
              target_chat_id TEXT NOT NULL,
              published_chat_id TEXT,
              published_message_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suggested_topics (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_chat_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              topic_title TEXT NOT NULL,
              topic_angle TEXT NOT NULL,
              why_write TEXT NOT NULL,
              post_format TEXT NOT NULL DEFAULT 'standard',
              post_type_id TEXT NOT NULL DEFAULT 'educational_short',
              source TEXT NOT NULL,
              status TEXT NOT NULL,
              topic_message_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topic_memory (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_topic_text TEXT,
              topic_hash TEXT NOT NULL UNIQUE,
              topic_summary_compact TEXT NOT NULL,
              topic_keywords_compact TEXT NOT NULL,
              semantic_fingerprint TEXT,
              post_id INTEGER,
              message_id INTEGER,
              status TEXT NOT NULL DEFAULT 'ACTIVE',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_seen_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_topic_memory_status_updated
            ON topic_memory(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS comments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_chat_id TEXT NOT NULL,
              source_message_id INTEGER NOT NULL,
              source_post_id INTEGER,
              review_chat_id TEXT,
              review_message_id INTEGER,
              comment_author_id TEXT NOT NULL,
              comment_author_username TEXT,
              comment_author_name TEXT NOT NULL,
              comment_text TEXT NOT NULL,
              comment_link TEXT,
              generated_reply TEXT,
              status TEXT NOT NULL,
              reply_mode TEXT NOT NULL DEFAULT 'bot',
              sent_message_id INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_states (
              chat_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        pending_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(pending_posts)").fetchall()
        }
        if "source_message_id" not in pending_columns:
            self._connection.execute("ALTER TABLE pending_posts ADD COLUMN source_message_id INTEGER")
        if "generation_input" not in pending_columns:
            self._connection.execute("ALTER TABLE pending_posts ADD COLUMN generation_input TEXT NOT NULL DEFAULT ''")
            self._connection.execute(
                "UPDATE pending_posts SET generation_input = source_request WHERE generation_input = ''"
            )
        if "post_format" not in pending_columns:
            self._connection.execute("ALTER TABLE pending_posts ADD COLUMN post_format TEXT NOT NULL DEFAULT 'standard'")
        if "post_type_id" not in pending_columns:
            self._connection.execute(
                "ALTER TABLE pending_posts ADD COLUMN post_type_id TEXT NOT NULL DEFAULT 'educational_short'"
            )

        comment_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(comments)").fetchall()
        }
        if "review_chat_id" not in comment_columns:
            self._connection.execute("ALTER TABLE comments ADD COLUMN review_chat_id TEXT")
        if "review_message_id" not in comment_columns:
            self._connection.execute("ALTER TABLE comments ADD COLUMN review_message_id INTEGER")
        if "comment_link" not in comment_columns:
            self._connection.execute("ALTER TABLE comments ADD COLUMN comment_link TEXT")
        if "reply_mode" not in comment_columns:
            self._connection.execute("ALTER TABLE comments ADD COLUMN reply_mode TEXT NOT NULL DEFAULT 'bot'")

        topic_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(suggested_topics)").fetchall()
        }
        if "post_format" not in topic_columns:
            self._connection.execute("ALTER TABLE suggested_topics ADD COLUMN post_format TEXT NOT NULL DEFAULT 'standard'")
        if "post_type_id" not in topic_columns:
            self._connection.execute(
                "ALTER TABLE suggested_topics ADD COLUMN post_type_id TEXT NOT NULL DEFAULT 'educational_short'"
            )

        topic_memory_columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(topic_memory)").fetchall()
        }
        if topic_memory_columns:
            if "last_seen_at" not in topic_memory_columns:
                self._connection.execute("ALTER TABLE topic_memory ADD COLUMN last_seen_at TEXT")
            if "status" not in topic_memory_columns:
                self._connection.execute("ALTER TABLE topic_memory ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'")
        self._connection.commit()


def _map_pending_post_row(row: sqlite3.Row) -> PendingPostRecord:
    return PendingPostRecord(
        id=int(row["id"]),
        owner_chat_id=row["owner_chat_id"],
        status=row["status"],
        post_format=row["post_format"] if row["post_format"] else "standard",
        post_type_id=row["post_type_id"] if row["post_type_id"] else "educational_short",
        source_request=row["source_request"],
        generation_input=row["generation_input"],
        source_message_id=row["source_message_id"],
        post_text=row["post_text"],
        preview_message_id=row["preview_message_id"],
        target_chat_id=row["target_chat_id"],
        published_chat_id=row["published_chat_id"],
        published_message_id=row["published_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _map_suggested_topic_row(row: sqlite3.Row) -> SuggestedTopicRecord:
    return SuggestedTopicRecord(
        id=int(row["id"]),
        owner_chat_id=row["owner_chat_id"],
        owner_id=row["owner_id"],
        topic_title=row["topic_title"],
        topic_angle=row["topic_angle"],
        why_write=row["why_write"],
        post_format=row["post_format"] if row["post_format"] else "standard",
        post_type_id=row["post_type_id"] if row["post_type_id"] else "educational_short",
        source=row["source"],
        status=row["status"],
        topic_message_id=row["topic_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_topic_memory_row(row: sqlite3.Row) -> TopicMemoryRecord:
    return TopicMemoryRecord(
        id=int(row["id"]),
        source_topic_text=row["source_topic_text"],
        topic_hash=row["topic_hash"],
        topic_summary_compact=row["topic_summary_compact"],
        topic_keywords_compact=row["topic_keywords_compact"],
        semantic_fingerprint=row["semantic_fingerprint"],
        post_id=row["post_id"],
        message_id=row["message_id"],
        status=row["status"] if row["status"] else "ACTIVE",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
    )


def _map_comment_row(row: sqlite3.Row) -> CommentRecord:
    return CommentRecord(
        id=int(row["id"]),
        source_chat_id=row["source_chat_id"],
        source_message_id=int(row["source_message_id"]),
        source_post_id=row["source_post_id"],
        review_chat_id=row["review_chat_id"],
        review_message_id=row["review_message_id"],
        comment_author_id=row["comment_author_id"],
        comment_author_username=row["comment_author_username"],
        comment_author_name=row["comment_author_name"],
        comment_text=row["comment_text"],
        comment_link=row["comment_link"],
        generated_reply=row["generated_reply"],
        status=row["status"],
        reply_mode=row["reply_mode"] or "bot",
        sent_message_id=row["sent_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
