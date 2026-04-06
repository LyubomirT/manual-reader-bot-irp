from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from rtfm_bot.config import BotConfig


class BotConfigTests(unittest.TestCase):
    def _base_env(self) -> dict[str, str]:
        return {
            "DISCORD_BOT_TOKEN": "discord-token",
            "POLLINATIONS_API_KEY": "pollinations-key",
            "ALLOWED_GUILD_ID": "1480820197236674714",
            "ALLOWED_ROLE_ID": "1480853263745155162",
            "BOT_OWNER_USER_ID": "861620168370683924",
        }

    def _mock_dotenv(self):
        module = types.ModuleType("dotenv")
        module.load_dotenv = lambda: None
        return patch.dict(sys.modules, {"dotenv": module})

    def test_auto_reply_enabled_defaults_to_false(self) -> None:
        with patch.dict(os.environ, self._base_env(), clear=True), self._mock_dotenv():
            config = BotConfig.from_env()

        self.assertFalse(config.auto_reply_enabled)

    def test_auto_reply_enabled_accepts_true(self) -> None:
        env = self._base_env()
        env["AUTO_REPLY_ENABLED"] = "true"

        with patch.dict(os.environ, env, clear=True), self._mock_dotenv():
            config = BotConfig.from_env()

        self.assertTrue(config.auto_reply_enabled)

    def test_auto_reply_enabled_rejects_invalid_values(self) -> None:
        env = self._base_env()
        env["AUTO_REPLY_ENABLED"] = "maybe"

        with patch.dict(os.environ, env, clear=True), self._mock_dotenv():
            with self.assertRaises(ValueError):
                BotConfig.from_env()


if __name__ == "__main__":
    unittest.main()
