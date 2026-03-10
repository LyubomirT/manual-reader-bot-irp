from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from rtfm_bot.storage import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.database_path = Path(self._temp_dir.name) / "reader.sqlite3"
        self.store = ConversationStore(self.database_path)
        self.store.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.store._initialize_sync()

    def test_get_scope_summaries_returns_message_counts_and_preview(self) -> None:
        self.store._append_message_sync(
            scope_id=101,
            guild_id=1,
            role="user",
            content="First question",
            author_id=10,
            author_name="Alice",
            max_messages=10,
            inactivity_seconds=3600,
        )
        self.store._append_message_sync(
            scope_id=101,
            guild_id=1,
            role="assistant",
            content="Latest answer from the bot",
            author_id=20,
            author_name="Reader of the Manual",
            max_messages=10,
            inactivity_seconds=3600,
        )

        summaries = self.store._get_scope_summaries_sync(
            guild_id=1,
            inactivity_seconds=3600,
        )

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].scope_id, 101)
        self.assertEqual(summaries[0].message_count, 2)
        self.assertEqual(summaries[0].last_message_preview, "Latest answer from the bot")

    def test_get_scope_summaries_purges_inactive_scopes(self) -> None:
        self.store._append_message_sync(
            scope_id=101,
            guild_id=1,
            role="user",
            content="Still fresh",
            author_id=10,
            author_name="Alice",
            max_messages=10,
            inactivity_seconds=3600,
        )
        self.store._append_message_sync(
            scope_id=202,
            guild_id=1,
            role="user",
            content="Too old",
            author_id=11,
            author_name="Bob",
            max_messages=10,
            inactivity_seconds=3600,
        )

        stale_time = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE conversation_scopes SET last_activity_at = ? WHERE scope_id = ?",
                (stale_time, 202),
            )
            connection.commit()

        summaries = self.store._get_scope_summaries_sync(
            guild_id=1,
            inactivity_seconds=3600,
        )

        self.assertEqual([summary.scope_id for summary in summaries], [101])


if __name__ == "__main__":
    unittest.main()
