# Bot Questionnaire

Please fill this out before implementation starts. Short answers are totally fine.

## 1. Interaction Style

- Should users talk to the bot only by mentioning it in a normal message, or do you also want slash commands for asking questions?
Answer: Only via @mentions and replies for now. NOTE: something like @everyone should not trigger it, so it should only respond to direct mentions.

- Besides `/update_cache`, do you want any extra slash commands? If yes, list them.
Answer: Perhaps a slash command to clear the conversation memory per channel/thread, if we implement that.

- Should the bot reply in the same channel publicly, or prefer ephemeral responses when possible?
Answer: Same channel publicly, so that others can see the answers and benefit from them too.

## 2. UI / Discord Features

- Do you want plain text replies, embeds, or a mix of both?
Answer: Plain text repliess is fine for responses, but for system (slash commands etc) embeds are good.

- Should the bot use buttons or select menus anywhere? If yes, where?
Answer: Not yet, I can't think of a good use for them in the initial version.

- Do you want modals anywhere? For example, a modal for longer questions or admin config.
Answer: Nope, not now.

- Do you specifically want Discord Components V2, or should I stick to standard `discord.py` views/buttons/selects unless there is a strong reason not to?
Answer: Use the normal `discord.py` components, no need for V2 right now.

## 3. Bot Behavior

- Should the bot answer only documentation questions, or also do light small talk when mentioned?
Answer: It can also do light small talk, as long as it tries to answer documentation questions first when they are asked.

- If the docs do not clearly answer a question, should the bot say "I don't know", make its best guess, or answer with a warning that it may be incomplete?
Answer: We should pivot to I don't know, but generally it'll try to be best effort.

- How short or detailed should answers usually be?
Answer: Mostly short, but it can be a bit more detailed if the question is complex and requires it.

- Should the bot include direct documentation links in replies when possible?
Answer: Yes, that would be great if it can do that when relevant. We should give it the main docs URL and let it figure out the links itself based on the search index.

## 4. Memory / Conversation

- Do you want the bot to remember recent conversation context per channel/thread, or treat each mention as a mostly standalone question?
Answer: Per channel/thread would be good, so that it can maintain some context in ongoing discussions.

- If conversation memory is wanted, how much should it remember? Example: last 5 messages, last 20 messages, or time-based.
Answer: Last 10 messages, and also clears every an hour of inactivity.

## 5. Safety / Limits

- What rate limit do you want per user? Example: `3 questions / 30 seconds`.
Answer: Instead we'll have a rate limit per channel/thread (1 msg/1 minute) to avoid abuse, and also refuse DMs to avoid spam.

- Do you want a separate global rate limit for the whole bot?
Answer: Yes, perhaps something like 40 messages for hour globally.

- Should the bot ignore users without a certain role, or should everyone in the server be able to use it?
Answer: Everyone with the 1480853263745155162 role on the server (ID of the server: 1480820197236674714) should be able to use it, but not those without it.

## 6. Cache / Docs

- Is the cache allowed to live on disk as JSON files, or do you want it stored in SQLite instead?
Answer: Allowed to live on disk as JSON files, as it's simpler for this use case and we don't have a lot of data to store.

- Should the bot auto-refresh the docs cache only when the upstream version changes, or also on a schedule?
Answer: Also on a schedule of every 6 hours, just to be safe.

- When `/update_cache` is used by the owner, should the bot post a visible success/failure message in-channel?
Answer: Yes, it should reply with an embed saying "Cache updated successfully" or "Failed to update cache" with the error message.

## 7. Admin / Config

- Besides the bot token and Pollinations API key, do you want any configurable settings in `.env`? Example: guild id for command sync, log level, rate limit values.
Answer: Yes, we can have the guild ID for command sync, and perhaps also the rate limit values as configurable settings in `.env` for easier adjustments without changing code.

- Do you want server-specific configuration later, or is one global configuration enough for now?
Answer: One global configuration is enough for now, but we can consider server-specific configuration in the future if we decide to expand the bot to multiple servers.

## 8. Tone

- The AGENTS file says the bot should be informal, friendly, concise, and a bit silly. Any boundaries on that tone?
Answer: Not really, just make sure it's not dramatic and doesn't use too much slang or jokes that might not land well with everyone. A lighthearted and approachable tone is good, but it should still be clear and professional when providing information.

- Should the bot avoid certain phrasing styles? Example: too much slang, too many jokes, too much emoji.
Answer: Yes, it should avoid too much slang, jokes, and emoji to maintain clarity and professionalism in its responses. A few well-placed emojis or light humor can be fine, but it shouldn't overdo it.

## 9. Deployment / Runtime

- Should I target local development only for now, or also prepare it for deployment as a long-running service?
Answer: For now, local development only, I'll move it to my VPS on DigitalOcean later when it's ready.

- Do you want logging written only to console, or also to a file?
Answer: Only to console for now, as it's easier for development. We can add file logging later if needed.

## 10. Nice-to-Haves

- Are there any features you already know you want soon after the MVP?
Answer: I think it should actually be done before the MVP. Since many users can talk with the bot at once, we should append their display name to their question when sending it to the LLM, so that it can personalize the response a bit and also keep track of who said what in the conversation.

- Anything you definitely do not want in v1?
Answer: Not sure about this yet.
