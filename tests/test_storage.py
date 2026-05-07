from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from rtfm_bot.storage import BAN_SOURCE_AI, ConversationStore


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
        with closing(sqlite3.connect(self.database_path)) as connection:
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

    def test_ban_user_persists_until_unbanned(self) -> None:
        self.assertFalse(self.store._is_user_banned_sync(404))

        ban = self.store._ban_user_sync(
            user_id=404,
            source=BAN_SOURCE_AI,
            banned_by_user_id=99,
            banned_by_name="Reader of the Manual",
        )

        self.assertEqual(ban.user_id, 404)
        self.assertEqual(ban.source, BAN_SOURCE_AI)
        self.assertTrue(self.store._is_user_banned_sync(404))

        loaded_ban = self.store._get_user_ban_sync(404)
        self.assertIsNotNone(loaded_ban)
        assert loaded_ban is not None
        self.assertEqual(loaded_ban.user_id, 404)
        self.assertEqual(loaded_ban.source, BAN_SOURCE_AI)
        self.assertEqual(loaded_ban.banned_by_user_id, 99)
        self.assertEqual(loaded_ban.banned_by_name, "Reader of the Manual")

        self.assertTrue(self.store._unban_user_sync(404))
        self.assertFalse(self.store._is_user_banned_sync(404))
        self.assertIsNone(self.store._get_user_ban_sync(404))

    def test_user_model_preference_persists(self) -> None:
        self.assertIsNone(self.store._get_user_model_preference_sync(101))

        preference = self.store._set_user_model_preference_sync(
            user_id=101,
            model_id="deepseek",
        )

        self.assertEqual(preference.user_id, 101)
        self.assertEqual(preference.model_id, "deepseek")

        loaded_preference = self.store._get_user_model_preference_sync(101)
        self.assertIsNotNone(loaded_preference)
        assert loaded_preference is not None
        self.assertEqual(loaded_preference.user_id, 101)
        self.assertEqual(loaded_preference.model_id, "deepseek")


if __name__ == "__main__":
    unittest.main()
