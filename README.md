# Reader of the Manual

Discord bot for the IntenseRP Next server. It answers docs questions when users mention or reply to it, keeps a short per-channel/thread memory in SQLite, and refreshes a local docs cache from the Read the Docs search index.

## Features

- Mention/reply driven chat in public channels
- Slash commands for `/help`, `/rate_limit_status`, `/update_cache`, and `/clear_memory`
- Admin memory inspection via `/inspect_channel_memory` and `/inspect_memory_global`
- Admin user blocking via `/ban_user` and `/unban_user`
- Disk-backed docs cache refreshed on startup and every 6 hours
- Two-stage docs retrieval: `openai` selects relevant cached pages, then `kimi` answers with full page context
- Full normalized docs pages cached locally in SQLite
- Per-channel/thread and global rate limiting
- Per-channel/thread conversation memory with 1 hour inactivity expiry
- Persistent user bans, including AI-triggered auto-blocks on obvious abuse/spam
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

For a fresh server deployment, see [docs/ubuntu-24.04-vps-setup.md](docs/ubuntu-24.04-vps-setup.md).

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
- rate limit settings
- conversation memory settings
- docs cache / selector settings

## Commands

- `/help` shows the usage summary and command list.
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
- Replies are plain text; system slash command responses use embeds.
- Docs retrieval now uses a model-assisted page selector over the full cached docs corpus.
- The LLM is instructed not to reveal reasoning traces.
- The LLM may auto-block users by returning the internal `[ban_user]` sentinel for obvious abuse, spam, or token-wasting nonsense.

## Data Files

Runtime data is stored under `data/` by default:

- `data/docs_cache.json`
- `data/reader.sqlite3`

Both are ignored by git.

## Extra Docs

- Deployment guide: [docs/ubuntu-24.04-vps-setup.md](docs/ubuntu-24.04-vps-setup.md)
- Feature ideas: [docs/feature-ideas.md](docs/feature-ideas.md)
