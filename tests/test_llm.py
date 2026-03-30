from __future__ import annotations

import unittest
from types import SimpleNamespace

from rtfm_bot.llm import (
    BAN_USER_SIGNAL,
    ChatRequest,
    DocSelection,
    PollinationsClient,
    is_ban_user_signal,
)


class LlmModerationTests(unittest.TestCase):
    def _make_client(self) -> PollinationsClient:
        config = SimpleNamespace(
            docs_base_url="https://example.com/docs/",
            docs_selector_page_limit=4,
            pollinations_model="kimi",
            pollinations_selector_model="openai",
            pollinations_batch_model="gemini-fast",
            pollinations_api_key="secret",
            pollinations_base_url="https://example.com",
        )
        return PollinationsClient(config, session=object())

    def test_is_ban_user_signal_only_matches_exact_sentinel(self) -> None:
        self.assertTrue(is_ban_user_signal(BAN_USER_SIGNAL))
        self.assertTrue(is_ban_user_signal(f"  {BAN_USER_SIGNAL}\n"))
        self.assertFalse(is_ban_user_signal("[BAN_USER]"))
        self.assertFalse(is_ban_user_signal(f"{BAN_USER_SIGNAL} please"))

    def test_answer_prompt_includes_auto_ban_instruction(self) -> None:
        client = self._make_client()
        request = ChatRequest(
            question="Hello there",
            user_display_name="Alice",
            history=[],
            docs=[],
            docs_available=False,
            model_id="kimi",
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

    def test_batch_classifier_parser_ignores_unknown_ids(self) -> None:
        client = self._make_client()
        parsed = client._parse_batch_classifier_response(
            "M01, M02, M99",
            [
                SimpleNamespace(batch_id="M01", is_bot=False),
                SimpleNamespace(batch_id="M02", is_bot=False),
                SimpleNamespace(batch_id="M03", is_bot=True),
            ],
        )

        self.assertEqual(parsed, ["M01", "M02"])


if __name__ == "__main__":
    unittest.main()
