# Feature Ideas

Ideas that would fit this bot well without changing its core "docs helper" identity too much.

## Good Next Commands

- `/cache_status`
  Shows docs cache version, page count, last refresh time, and whether the last refresh had errors.

- `/docs_search <query>`
  Returns the best matching docs pages and links without spending an LLM call.

- `/sources`
  Shows which docs pages were used for the last answer in the current channel.

- `/memory_stats`
  Admin-only overview with active memory scopes, busiest channels, and last activity times.

- `/health`
  Admin-only quick diagnostics for Discord connectivity, Pollinations reachability, cache availability, and DB status.

- `/feedback`
  Lets users flag an answer as helpful, wrong, or outdated so you can spot bad retrieval or stale docs.

## Other Useful Features

- Optional answer footers with source links when the reply is clearly documentation-backed.
- An FAQ shortcut layer for very common questions so the bot can answer instantly without hitting the LLM.
- Automatic stale-cache warnings if the docs refresh has been failing for a while.
- A small admin audit log channel for cache refreshes, command usage spikes, and repeated API failures.
- Per-user cooldowns in addition to channel/global cooldowns if abuse ever becomes a problem.
