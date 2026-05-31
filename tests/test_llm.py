from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from rtfm_bot.llm import (
    ADVISOR_NO_USEFUL_CONTEXT,
    BAN_USER_SIGNAL,
    ChatRequest,
    DocSelection,
    PollinationsClient,
    is_ban_user_signal,
)
from rtfm_bot.storage import ConversationMessage


class LlmModerationTests(unittest.TestCase):
    def _make_client(self, *, ai_triggered_bans_enabled: bool = False) -> PollinationsClient:
        config = SimpleNamespace(
            docs_base_url="https://example.com/docs/",
            docs_selector_page_limit=4,
            pollinations_model="glm",
            pollinations_selector_model="gpt-5.4-mini",
            pollinations_advisor_model="openai",
            pollinations_api_key="secret",
            pollinations_base_url="https://example.com",
            ai_triggered_bans_enabled=ai_triggered_bans_enabled,
        )
        return PollinationsClient(config, session=object())

    def test_is_ban_user_signal_only_matches_exact_sentinel(self) -> None:
        self.assertTrue(is_ban_user_signal(BAN_USER_SIGNAL))
        self.assertTrue(is_ban_user_signal(f"  {BAN_USER_SIGNAL}\n"))
        self.assertFalse(is_ban_user_signal("[BAN_USER]"))
        self.assertFalse(is_ban_user_signal(f"{BAN_USER_SIGNAL} please"))

    def test_answer_prompt_disables_auto_ban_instruction_by_default(self) -> None:
        client = self._make_client()
        request = ChatRequest(
            question="Hello there",
            user_display_name="Alice",
            history=[],
            docs=[],
            docs_available=False,
            model_id="glm",
            docs_page_limit=4,
        )

        messages = client._build_answer_messages(
            request,
            DocSelection(needs_docs=False, selected_pages=[]),
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(BAN_USER_SIGNAL, messages[0]["content"])
        self.assertIn("AI-triggered bans are disabled", messages[0]["content"])
        self.assertNotIn("wastes the owner's tokens", messages[0]["content"])
        self.assertIn("Discord-compatible Markdown", messages[0]["content"])
        self.assertIn("Do not use tables", messages[0]["content"])
        self.assertEqual(
            messages[2]["content"],
            "Advisor model reported no additional useful context from the chat.",
        )

    def test_answer_prompt_includes_auto_ban_instruction_when_enabled(self) -> None:
        client = self._make_client(ai_triggered_bans_enabled=True)
        request = ChatRequest(
            question="Hello there",
            user_display_name="Alice",
            history=[],
            docs=[],
            docs_available=False,
            model_id="glm",
            docs_page_limit=4,
        )

        messages = client._build_answer_messages(
            request,
            DocSelection(needs_docs=False, selected_pages=[]),
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(BAN_USER_SIGNAL, messages[0]["content"])
        self.assertIn("wastes the owner's tokens", messages[0]["content"])
        self.assertIn("Never use [ban_user] just because the user asked", messages[0]["content"])

    def test_answer_messages_include_useful_advisor_report(self) -> None:
        client = self._make_client()
        request = ChatRequest(
            question="Can you summarize that setting again?",
            user_display_name="Alice",
            history=[],
            docs=[],
            docs_available=False,
            model_id="glm",
            docs_page_limit=4,
        )

        messages = client._build_answer_messages(
            request,
            DocSelection(needs_docs=False, selected_pages=[]),
            advisor_report="The user previously asked about voice chat setup.",
        )

        self.assertIn(
            "Advisor model reported useful context from the chat",
            messages[2]["content"],
        )
        self.assertIn("voice chat setup", messages[2]["content"])

    def test_advisor_transcript_uses_latest_ten_history_messages(self) -> None:
        client = self._make_client()
        now = datetime.now(UTC)
        history = [
            ConversationMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
                author_id=index,
                author_name=f"User {index}",
                created_at=now,
            )
            for index in range(12)
        ]
        request = ChatRequest(
            question="What did we decide?",
            user_display_name="Alice",
            history=history,
            docs=[],
            docs_available=False,
            model_id="glm",
            docs_page_limit=4,
        )

        transcript = client._build_advisor_transcript(request)

        self.assertNotIn("User 0): message 0", transcript)
        self.assertNotIn("User 1): message 1", transcript)
        self.assertIn("message 2", transcript)
        self.assertIn("message 11", transcript)
        self.assertIn("Latest user message to answer now", transcript)


class FakePollinationsResponse:
    def __init__(self, content: str = "ok") -> None:
        self.status = 200
        self.content = content

    async def __aenter__(self) -> "FakePollinationsResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def text(self) -> str:
        return ""

    async def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self.content}}]}


class FakePollinationsSession:
    def __init__(self, response_content: str = "ok") -> None:
        self.calls: list[dict[str, object]] = []
        self.response_content = response_content

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
    ) -> FakePollinationsResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakePollinationsResponse(self.response_content)


class PollinationsRequestTests(unittest.IsolatedAsyncioTestCase):
    def _make_config(self) -> SimpleNamespace:
        return SimpleNamespace(
            pollinations_api_key="secret",
            pollinations_base_url="https://example.com/v1",
            pollinations_advisor_model="openai",
        )

    async def test_chat_completion_payload_disables_non_text_features(self) -> None:
        session = FakePollinationsSession()
        client = PollinationsClient(self._make_config(), session=session)

        await client._request_chat_completion(
            model="glm",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            max_tokens=10,
            response_format={"type": "text"},
        )

        payload = session.calls[0]["json"]
        self.assertEqual(payload["model"], "glm")
        self.assertEqual(payload["modalities"], ["text"])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertEqual(payload["function_call"], "none")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["thinking_budget"], 0)
        self.assertEqual(payload["response_format"], {"type": "text"})

    async def test_chat_completion_payload_passes_openai_advisor_id_through(self) -> None:
        session = FakePollinationsSession()
        client = PollinationsClient(self._make_config(), session=session)

        await client._request_chat_completion(
            model="openai",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            max_tokens=10,
        )

        payload = session.calls[0]["json"]
        self.assertEqual(payload["model"], "openai")

    async def test_advisor_no_useful_context_response_is_ignored(self) -> None:
        now = datetime.now(UTC)
        request = ChatRequest(
            question="What did we decide?",
            user_display_name="Alice",
            history=[
                ConversationMessage(
                    role="user",
                    content="I was asking about setup earlier.",
                    author_id=1,
                    author_name="Alice",
                    created_at=now,
                )
            ],
            docs=[],
            docs_available=False,
            model_id="glm",
            docs_page_limit=4,
        )
        session = FakePollinationsSession(response_content=ADVISOR_NO_USEFUL_CONTEXT)
        client = PollinationsClient(self._make_config(), session=session)

        advisor_context = await client._extract_advisor_context(request)

        self.assertIsNone(advisor_context)


if __name__ == "__main__":
    unittest.main()
