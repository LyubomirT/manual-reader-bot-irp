# Reader of the Manual

Discord bot for the IntenseRP Next server. It answers docs questions when users mention or reply to it, keeps a short per-channel/thread memory in SQLite, and refreshes a local docs cache from the Read the Docs search index.

## Features

- Mention/reply driven chat in public channels
- Slash commands for `/update_cache` and `/clear_memory`
- Disk-backed docs cache refreshed on startup and every 6 hours
- Chunked local RAG over cached docs pages with SQLite FTS5 search
- Per-channel/thread and global rate limiting
- Per-channel/thread conversation memory with 1 hour inactivity expiry
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
- `COMMAND_GUILD_ID`
- `ALLOWED_GUILD_ID`
- `ALLOWED_ROLE_ID`
- `BOT_OWNER_USER_ID`
- rate limit settings
- conversation memory settings
- retrieval tuning settings

## Behavior Notes

- The bot only answers in the configured guild.
- Users must have the configured role to use it.
- DMs are rejected.
- Replies are plain text; system slash command responses use embeds.
- Docs retrieval now uses chunked local RAG with SQLite FTS5.
- The LLM is instructed not to reveal reasoning traces.

## Data Files

Runtime data is stored under `data/` by default:

- `data/docs_cache.json`
- `data/reader.sqlite3`

Both are ignored by git.
