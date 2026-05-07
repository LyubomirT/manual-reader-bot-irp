# Reader of the Manual

Discord bot for the IntenseRP Next server. It answers docs questions when users mention or reply to it, keeps a short per-channel/thread memory in SQLite, and refreshes a local docs cache from the Read the Docs search index.

> [!NOTE]
> This is not meant to be a serious, general-purpose project. It's mostly centered around my other project [IntenseRP Next](https://github.com/LyubomirT/intense-rp-next) and is intentionally built with a lot of quick-and-dirty solutions, hardcoded assumptions, and hacky workarounds. Right now it's used in a single server (the official IntenseRP Next server) and is not designed for reuse or extensibility. That said, it does have a lot of features and is a fun example of a small, self-contained LLM-powered bot with some interesting implementation details.

> [!IMPORTANT]
> Huge thanks to [Pollinations.ai](https://pollinations.ai) for making it possible to have a powerful LLM backend without worrying about hosting, scaling, or severe API costs. The bot is built around the Pollinations API and wouldn't be feasible to run on something like OpenAI's API without significant optimizations and cost management.

## Features

- Mention/reply driven chat in public channels
- Per-user model preferences with a reply button picker and `/model`
- Slash commands for `/help`, `/model`, `/rate_limit_status`, `/update_cache`, and `/clear_memory`
- Admin memory inspection via `/inspect_channel_memory` and `/inspect_memory_global`
- Admin user blocking via `/ban_user` and `/unban_user`
- Disk-backed docs cache refreshed on startup and every 6 hours
- Two-stage docs retrieval: `openai` selects relevant cached pages, then `kimi` answers with full page context
- Full normalized docs pages cached locally in SQLite
- Per-channel/thread and global rate limiting
- Per-channel/thread conversation memory with 1 hour inactivity expiry
- Random rotating bot statuses with a configurable update interval and optional text file override
- Persistent user bans, with optional AI-triggered auto-blocks on obvious abuse/spam
- Role and guild gating so the bot only works where it should

## Requirements

- Python 3.12
- A Discord bot token
- A Pollinations API key

You also need `MESSAGE CONTENT INTENT` enabled for the bot in the Discord developer portal, because mention-driven questions are read from normal messages.

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in the secrets.

3. Run the bot:

```bash
python3 main.py
```

## Environment Variables

The main options live in `.env.example`. The important ones are:

- `DISCORD_BOT_TOKEN`
- `POLLINATIONS_API_KEY`
- `POLLINATIONS_MODEL`
- `POLLINATIONS_SELECTOR_MODEL`
- `COMMAND_GUILD_ID`
- `ALLOWED_GUILD_ID`
- `ALLOWED_ROLE_ID`
- `BOT_OWNER_USER_ID`
- `AI_TRIGGERED_BANS_ENABLED`
- `STATUS_ROTATION_INTERVAL_SECONDS`
- `STATUS_PHRASES_FILE`
- rate limit settings
- conversation memory settings
- docs cache / selector settings

## Commands

- `/help` shows the usage summary and command list.
- `/model` opens the per-user model picker.
- `/rate_limit_status` shows the current channel and global cooldown buckets.
- `/clear_memory` clears the saved conversation for the current channel/thread.
- `/inspect_channel_memory` is an admin-only snapshot of the current channel/thread memory.
- `/inspect_memory_global` is an admin-only browser for all active memory scopes.
- `/ban_user` manually blocks a user from using the bot.
- `/unban_user` manually removes a block. AI-triggered blocks can only be undone by the bot owner.
- `/update_cache` force-refreshes the docs cache and is owner-only.

## Behavior Notes

- The bot only answers in the configured guild.
- Users must have the configured role to use it.
- Blocked users cannot chat with the bot or use any slash commands.
- DMs are rejected.
- AI replies include a small model/status view with a model-picker button.
- Replies are plain text; system slash command responses use embeds.
- Admin inspection and cache-refresh command responses are ephemeral.
- Docs retrieval now uses a model-assisted page selector over the full cached docs corpus.
- The LLM is instructed not to reveal reasoning traces.
- If `AI_TRIGGERED_BANS_ENABLED=true`, the LLM may auto-block users by returning the internal `[ban_user]` sentinel for obvious abuse, spam, or token-wasting nonsense.
- Set `STATUS_PHRASES_FILE` to a text file path to override rotating statuses. Each non-comment line is one phrase, optionally prefixed with `[watching]`, `[playing]`, `[listening]`, or `[custom]`; see `statuses.example.txt`.

## Data Files

Runtime data is stored under `data/` by default:

- `data/docs_cache.json`
- `data/reader.sqlite3`

Both are ignored by git.