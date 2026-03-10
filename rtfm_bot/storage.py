from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(slots=True)
class ConversationMessage:
    role: str
    content: str
    author_id: int | None
    author_name: str | None
    created_at: datetime


class ConversationStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def get_recent_messages(
        self,
        *,
        scope_id: int,
        guild_id: int,
        limit: int,
        inactivity_seconds: int,
    ) -> list[ConversationMessage]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_recent_messages_sync,
                scope_id,
                guild_id,
                limit,
                inactivity_seconds,
            )

    async def append_message(
        self,
        *,
        scope_id: int,
        guild_id: int,
        role: str,
        content: str,
        author_id: int | None,
        author_name: str | None,
        max_messages: int,
        inactivity_seconds: int,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._append_message_sync,
                scope_id,
                guild_id,
                role,
                content,
                author_id,
                author_name,
                max_messages,
                inactivity_seconds,
            )

    async def clear_scope(self, *, scope_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._clear_scope_sync, scope_id)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_scopes (
                    scope_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    last_activity_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    author_id INTEGER,
                    author_name TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(scope_id) REFERENCES conversation_scopes(scope_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_scope_id
                ON conversation_messages(scope_id, id DESC)
                """
            )
            connection.commit()

    def _get_recent_messages_sync(
        self,
        scope_id: int,
        guild_id: int,
        limit: int,
        inactivity_seconds: int,
    ) -> list[ConversationMessage]:
        now = datetime.now(UTC)

        with self._connect() as connection:
            self._purge_scope_if_inactive_sync(
                connection,
                scope_id=scope_id,
                inactivity_seconds=inactivity_seconds,
                now=now,
            )

            rows = connection.execute(
                """
                SELECT role, content, author_id, author_name, created_at
                FROM conversation_messages
                WHERE scope_id = ? AND guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (scope_id, guild_id, limit),
            ).fetchall()

        messages = [
            ConversationMessage(
                role=row["role"],
                content=row["content"],
                author_id=row["author_id"],
                author_name=row["author_name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in reversed(rows)
        ]
        return messages

    def _append_message_sync(
        self,
        scope_id: int,
        guild_id: int,
        role: str,
        content: str,
        author_id: int | None,
        author_name: str | None,
        max_messages: int,
        inactivity_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        now_iso = now.isoformat()

        with self._connect() as connection:
            self._purge_scope_if_inactive_sync(
                connection,
                scope_id=scope_id,
                inactivity_seconds=inactivity_seconds,
                now=now,
            )

            connection.execute(
                """
                INSERT INTO conversation_messages (
                    scope_id,
                    guild_id,
                    role,
                    content,
                    author_id,
                    author_name,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scope_id, guild_id, role, content, author_id, author_name, now_iso),
            )
            connection.execute(
                """
                INSERT INTO conversation_scopes (scope_id, guild_id, last_activity_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope_id)
                DO UPDATE SET
                    guild_id = excluded.guild_id,
                    last_activity_at = excluded.last_activity_at
                """,
                (scope_id, guild_id, now_iso),
            )
            connection.execute(
                """
                DELETE FROM conversation_messages
                WHERE scope_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM conversation_messages
                      WHERE scope_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (scope_id, scope_id, max_messages),
            )
            connection.commit()

    def _clear_scope_sync(self, scope_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM conversation_messages WHERE scope_id = ?",
                (scope_id,),
            )
            connection.execute(
                "DELETE FROM conversation_scopes WHERE scope_id = ?",
                (scope_id,),
            )
            connection.commit()

    def _purge_scope_if_inactive_sync(
        self,
        connection: sqlite3.Connection,
        *,
        scope_id: int,
        inactivity_seconds: int,
        now: datetime,
    ) -> None:
        row = connection.execute(
            """
            SELECT last_activity_at
            FROM conversation_scopes
            WHERE scope_id = ?
            """,
            (scope_id,),
        ).fetchone()

        if row is None:
            return

        last_activity = datetime.fromisoformat(row["last_activity_at"])
        if now - last_activity <= timedelta(seconds=inactivity_seconds):
            return

        connection.execute(
            "DELETE FROM conversation_messages WHERE scope_id = ?",
            (scope_id,),
        )
        connection.execute(
            "DELETE FROM conversation_scopes WHERE scope_id = ?",
            (scope_id,),
        )
        connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

