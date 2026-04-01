from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from rtfm_bot.bot import QueuedBatchMessage, ReaderBot


OWNER_USER_ID = 861620168370683924


class FakeChannel:
    def __init__(self, *, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name
        self._history_messages: list[SimpleNamespace] = []

    def set_history(self, messages: list[SimpleNamespace]) -> None:
        self._history_messages = list(messages)

    def history(self, *, limit: int):
        async def iterator():
            for message in self._history_messages[:limit]:
                yield message

        return iterator()


class FakeGuild:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int):
        if channel_id == self._channel.id:
            return self._channel
        return None

    def get_thread(self, channel_id: int):
        return None


def make_author(user_id: int, display_name: str, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        display_name=display_name,
        global_name=None,
        name=display_name,
        bot=bot,
    )


def make_message(
    *,
    message_id: int,
    author: SimpleNamespace,
    content: str,
    created_at: datetime,
    channel: FakeChannel,
    guild: FakeGuild,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        author=author,
        content=content,
        created_at=created_at,
        channel=channel,
        guild=guild,
        mentions=[],
        role_mentions=[],
    )


class BatchContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = object.__new__(ReaderBot)
        self.bot.config = SimpleNamespace(owner_user_id=OWNER_USER_ID)
        self.bot._batch_queue = []
        self.bot._batch_queue_lock = asyncio.Lock()

    async def test_build_batch_classifier_context_prefix_skips_queued_ids(self) -> None:
        channel = FakeChannel(channel_id=99, name="docs-help")
        guild = FakeGuild(channel)
        base_time = datetime(2026, 4, 1, 12, 30, tzinfo=UTC)

        alice = make_author(100, "Alice")
        maintainer = make_author(OWNER_USER_ID, "Lyu")
        helper_bot = make_author(200, "Helpful Bot", bot=True)

        queued_older = make_message(
            message_id=30,
            author=alice,
            content="can someone explain hotswap?",
            created_at=base_time,
            channel=channel,
            guild=guild,
        )
        queued_newer = make_message(
            message_id=31,
            author=alice,
            content="or where is that in the docs",
            created_at=base_time + timedelta(minutes=1),
            channel=channel,
            guild=guild,
        )
        recent_maintainer = make_message(
            message_id=29,
            author=maintainer,
            content="the docs mention migration too",
            created_at=base_time - timedelta(minutes=1),
            channel=channel,
            guild=guild,
        )
        recent_bot = make_message(
            message_id=28,
            author=helper_bot,
            content="Try reading the troubleshooting page",
            created_at=base_time - timedelta(minutes=2),
            channel=channel,
            guild=guild,
        )
        older_human = make_message(
            message_id=27,
            author=alice,
            content="moonshot search confused me earlier",
            created_at=base_time - timedelta(minutes=3),
            channel=channel,
            guild=guild,
        )

        channel.set_history(
            [
                queued_newer,
                queued_older,
                recent_maintainer,
                recent_bot,
                older_human,
            ]
        )

        prefix = await self.bot._build_batch_classifier_context_prefix(
            [
                QueuedBatchMessage(
                    message=queued_older,
                    content=queued_older.content,
                    author_display_name="Alice",
                    candidate=True,
                ),
                QueuedBatchMessage(
                    message=queued_newer,
                    content=queued_newer.content,
                    author_display_name="Alice",
                    candidate=True,
                ),
            ]
        )

        self.assertIn("#docs-help", prefix)
        self.assertIn("2026-04-01 12:27 UTC | Alice: moonshot search confused me earlier", prefix)
        self.assertIn("2026-04-01 12:29 UTC | Lyu [MAINTAINER]: the docs mention migration too", prefix)
        self.assertNotIn("can someone explain hotswap?", prefix)
        self.assertNotIn("or where is that in the docs", prefix)
        self.assertNotIn("Helpful Bot", prefix)

    async def test_enqueue_batch_message_marks_owner_as_maintainer(self) -> None:
        channel = FakeChannel(channel_id=50, name="general")
        guild = FakeGuild(channel)
        owner = make_author(OWNER_USER_ID, "Lyu")
        message = make_message(
            message_id=10,
            author=owner,
            content="hello there",
            created_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            channel=channel,
            guild=guild,
        )

        await self.bot._enqueue_batch_message(
            message,
            content="hello there",
            candidate=True,
        )

        self.assertEqual(len(self.bot._batch_queue), 1)
        self.assertEqual(
            self.bot._batch_queue[0].author_display_name,
            "Lyu [MAINTAINER]",
        )


if __name__ == "__main__":
    unittest.main()
