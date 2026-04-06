from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _read_optional_int(name: str, default: int | None = None) -> int | None:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return int(raw_value.strip())


def _read_int(name: str, default: int) -> int:
    value = _read_optional_int(name, default)
    if value is None:
        raise ValueError(f"Environment variable {name} must be set.")
    return value


def _read_positive_int(name: str, default: int) -> int:
    value = _read_int(name, default)
    if value <= 0:
        raise ValueError(f"Environment variable {name} must be greater than 0.")
    return value


def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"Environment variable {name} must be a boolean value like true/false."
    )


@dataclass(slots=True)
class BotConfig:
    discord_bot_token: str
    pollinations_api_key: str
    pollinations_model: str
    pollinations_selector_model: str
    pollinations_batch_model: str
    pollinations_base_url: str
    command_guild_id: int | None
    allowed_guild_id: int
    allowed_role_id: int
    owner_user_id: int
    docs_base_url: str
    docs_search_index_url: str
    docs_search_index_file: Path | None
    app_version_url: str
    channel_rate_limit_count: int
    channel_rate_limit_window_seconds: int
    global_rate_limit_count: int
    global_rate_limit_window_seconds: int
    conversation_max_messages: int
    conversation_inactivity_seconds: int
    auto_reply_enabled: bool
    auto_reply_batch_interval_seconds: int
    cache_refresh_interval_seconds: int
    status_rotation_interval_seconds: int
    docs_fetch_concurrency: int
    docs_selector_page_limit: int
    http_timeout_seconds: int
    log_level: str
    data_dir: Path

    @property
    def cache_file_path(self) -> Path:
        return self.data_dir / "docs_cache.json"

    @property
    def database_file_path(self) -> Path:
        return self.data_dir / "reader.sqlite3"

    @classmethod
    def from_env(cls) -> "BotConfig":
        from dotenv import load_dotenv

        load_dotenv()

        data_dir = Path(os.getenv("BOT_DATA_DIR", "data")).resolve()

        return cls(
            discord_bot_token=_read_required_env("DISCORD_BOT_TOKEN"),
            pollinations_api_key=_read_required_env("POLLINATIONS_API_KEY"),
            pollinations_model=os.getenv("POLLINATIONS_MODEL", "kimi").strip() or "kimi",
            pollinations_selector_model=(
                os.getenv("POLLINATIONS_SELECTOR_MODEL", "openai").strip() or "openai"
            ),
            pollinations_batch_model=(
                os.getenv("POLLINATIONS_BATCH_MODEL", "gemini-fast").strip() or "gemini-fast"
            ),
            pollinations_base_url=(
                os.getenv("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai/v1").rstrip("/")
            ),
            command_guild_id=_read_optional_int("COMMAND_GUILD_ID"),
            allowed_guild_id=_read_int("ALLOWED_GUILD_ID", 1480820197236674714),
            allowed_role_id=_read_int("ALLOWED_ROLE_ID", 1480853263745155162),
            owner_user_id=_read_int("BOT_OWNER_USER_ID", 861620168370683924),
            docs_base_url=os.getenv(
                "DOCS_BASE_URL",
                "https://intense-rp-next.readthedocs.io/en/latest/",
            ).rstrip("/")
            + "/",
            docs_search_index_url=os.getenv(
                "DOCS_SEARCH_INDEX_URL",
                "https://intense-rp-next.readthedocs.io/en/latest/search.json",
            ).strip(),
            docs_search_index_file=(
                Path(path_value).resolve()
                if (path_value := os.getenv("DOCS_SEARCH_INDEX_FILE", "").strip())
                else None
            ),
            app_version_url=os.getenv(
                "APP_VERSION_URL",
                "https://raw.githubusercontent.com/LyubomirT/intense-rp-next/refs/heads/v2-rewrite/version.json",
            ).strip(),
            channel_rate_limit_count=_read_int("CHANNEL_RATE_LIMIT_COUNT", 2),
            channel_rate_limit_window_seconds=_read_int("CHANNEL_RATE_LIMIT_WINDOW_SECONDS", 60),
            global_rate_limit_count=_read_int("GLOBAL_RATE_LIMIT_COUNT", 48),
            global_rate_limit_window_seconds=_read_int("GLOBAL_RATE_LIMIT_WINDOW_SECONDS", 3600),
            conversation_max_messages=_read_int("CONVERSATION_MAX_MESSAGES", 10),
            conversation_inactivity_seconds=_read_int("CONVERSATION_INACTIVITY_SECONDS", 3600),
            auto_reply_enabled=_read_bool("AUTO_REPLY_ENABLED", False),
            auto_reply_batch_interval_seconds=_read_positive_int(
                "AUTO_REPLY_BATCH_INTERVAL_SECONDS",
                30,
            ),
            cache_refresh_interval_seconds=_read_int("CACHE_REFRESH_INTERVAL_SECONDS", 21600),
            status_rotation_interval_seconds=_read_positive_int(
                "STATUS_ROTATION_INTERVAL_SECONDS",
                3600,
            ),
            docs_fetch_concurrency=_read_int("DOCS_FETCH_CONCURRENCY", 5),
            docs_selector_page_limit=_read_int("DOCS_SELECTOR_PAGE_LIMIT", 4),
            http_timeout_seconds=_read_int("HTTP_TIMEOUT_SECONDS", 30),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            data_dir=data_dir,
        )
