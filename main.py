from __future__ import annotations

import logging

from rtfm_bot.bot import ReaderBot
from rtfm_bot.config import BotConfig


def main() -> None:
    config = BotConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = ReaderBot(config)
    bot.run(config.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
