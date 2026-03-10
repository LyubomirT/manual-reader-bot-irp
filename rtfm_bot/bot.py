from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from rtfm_bot.config import BotConfig
from rtfm_bot.docs_cache import DocsCacheManager, RefreshResult
from rtfm_bot.llm import ChatRequest, PollinationsClient, PollinationsError
from rtfm_bot.rate_limits import SlidingWindowRateLimiter
from rtfm_bot.storage import ConversationStore

LOGGER = logging.getLogger(__name__)


class ReaderBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.config = config
        self.store = ConversationStore(config.database_file_path)
        self.docs_cache = DocsCacheManager(config)
        self.http_session: aiohttp.ClientSession | None = None
        self.llm: PollinationsClient | None = None
        self.channel_rate_limiter = SlidingWindowRateLimiter(
            limit=config.channel_rate_limit_count,
            window_seconds=config.channel_rate_limit_window_seconds,
        )
        self.global_rate_limiter = SlidingWindowRateLimiter(
            limit=config.global_rate_limit_count,
            window_seconds=config.global_rate_limit_window_seconds,
        )
        self._cache_refresh_task: asyncio.Task[None] | None = None
        self._register_commands()

    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.config.http_timeout_seconds)
        self.http_session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "rtfm-read-bot-irp/0.1"},
        )
        self.llm = PollinationsClient(self.config, self.http_session)

        await self.store.initialize()

        refresh_result = await self.docs_cache.initialize(self.http_session)
        if refresh_result.error:
            LOGGER.warning("Docs cache initialization issue: %s", refresh_result.error)
        else:
            LOGGER.info(
                "Docs cache ready with %s entries (version=%s)",
                refresh_result.entry_count,
                refresh_result.version or "unknown",
            )

        self._cache_refresh_task = asyncio.create_task(
            self._cache_refresh_loop(),
            name="docs-cache-refresh-loop",
        )

        if self.config.command_guild_id:
            guild = discord.Object(id=self.config.command_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()

        LOGGER.info("Synced %s application commands.", len(synced))

    async def close(self) -> None:
        if self._cache_refresh_task is not None:
            self._cache_refresh_task.cancel()
            try:
                await self._cache_refresh_task
            except asyncio.CancelledError:
                pass

        if self.http_session is not None:
            await self.http_session.close()

        await super().close()

    async def on_ready(self) -> None:
        if self.user is None:
            return
        LOGGER.info("Logged in as %s (%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.guild is None:
            await message.channel.send("I only work inside the IntenseRP Next server, sorry.")
            return

        if message.guild.id != self.config.allowed_guild_id:
            return

        if not await self._message_targets_bot(message):
            return

        if not self._member_has_allowed_role(message.author):
            await message.reply(
                "You need the helper role to use me here.",
                mention_author=False,
            )
            return

        if message.channel.id is None:
            return

        channel_retry_after = self.channel_rate_limiter.retry_after(message.channel.id)
        if channel_retry_after:
            await message.reply(
                f"This channel is on cooldown for {channel_retry_after}s. Try me again in a minute.",
                mention_author=False,
            )
            return

        global_retry_after = self.global_rate_limiter.retry_after("global")
        if global_retry_after:
            await message.reply(
                f"I'm rate limited right now. Give me about {global_retry_after}s to chill.",
                mention_author=False,
            )
            return

        content = self._strip_bot_mentions(message.content)
        if not content:
            if message.attachments:
                await message.reply(
                    "I can't read attachments yet. Toss me a text question instead.",
                    mention_author=False,
                )
            else:
                await message.reply(
                    "Hit me with a docs question and I'll go rummage through the manual.",
                    mention_author=False,
                )
            return

        self.channel_rate_limiter.hit(message.channel.id)
        self.global_rate_limiter.hit("global")

        history = await self.store.get_recent_messages(
            scope_id=message.channel.id,
            guild_id=message.guild.id,
            limit=self.config.conversation_max_messages,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )
        docs = self.docs_cache.search(content, limit=5)

        request = ChatRequest(
            question=content,
            user_display_name=message.author.display_name,
            history=history,
            docs=docs,
            docs_available=self.docs_cache.available,
        )

        try:
            async with message.channel.typing():
                response_text = await self._generate_reply(request)
        except PollinationsError as exc:
            LOGGER.exception("Pollinations request failed.")
            await message.reply(
                f"I tripped over the AI request just now: {exc}",
                mention_author=False,
            )
            return
        except Exception:
            LOGGER.exception("Unexpected error while generating a reply.")
            await message.reply(
                "Something broke while I was digging through the manual. Please try again in a bit.",
                mention_author=False,
            )
            return

        await self._send_reply(message, response_text)

        await self.store.append_message(
            scope_id=message.channel.id,
            guild_id=message.guild.id,
            role="user",
            content=content,
            author_id=message.author.id,
            author_name=message.author.display_name,
            max_messages=self.config.conversation_max_messages,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )
        await self.store.append_message(
            scope_id=message.channel.id,
            guild_id=message.guild.id,
            role="assistant",
            content=response_text,
            author_id=self.user.id if self.user else None,
            author_name=self.user.display_name if self.user else "Reader of the Manual",
            max_messages=self.config.conversation_max_messages,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )

    async def _generate_reply(self, request: ChatRequest) -> str:
        if self.llm is None:
            raise RuntimeError("The LLM client is not initialized.")

        response_text = await self.llm.generate_reply(request)
        clean_text = response_text.strip()
        if not clean_text:
            return "I came back empty-handed there. Mind trying that one again?"

        return clean_text

    async def _cache_refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.cache_refresh_interval_seconds)
                if self.http_session is None:
                    continue

                result = await self.docs_cache.maybe_refresh(self.http_session)
                if result.error:
                    LOGGER.warning("Scheduled docs cache refresh failed: %s", result.error)
                elif result.updated:
                    LOGGER.info(
                        "Docs cache refreshed successfully (%s entries, version=%s)",
                        result.entry_count,
                        result.version or "unknown",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Unexpected error in docs cache refresh loop.")

    async def _message_targets_bot(self, message: discord.Message) -> bool:
        return self._is_direct_bot_mention(message) or await self._is_reply_to_bot(message)

    def _is_direct_bot_mention(self, message: discord.Message) -> bool:
        if self.user is None:
            return False
        return any(user.id == self.user.id for user in message.mentions)

    async def _is_reply_to_bot(self, message: discord.Message) -> bool:
        if self.user is None or message.reference is None:
            return False

        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            return resolved.author.id == self.user.id

        if message.reference.message_id is None:
            return False

        try:
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

        return referenced_message.author.id == self.user.id

    def _member_has_allowed_role(self, member: discord.abc.User | discord.Member) -> bool:
        if not isinstance(member, discord.Member):
            return False
        return any(role.id == self.config.allowed_role_id for role in member.roles)

    def _interaction_is_allowed(self, interaction: discord.Interaction[Any]) -> bool:
        if interaction.guild_id != self.config.allowed_guild_id:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == self.config.allowed_role_id for role in interaction.user.roles)

    def _strip_bot_mentions(self, content: str) -> str:
        if self.user is None:
            return content.strip()

        mention_pattern = rf"<@!?{self.user.id}>"
        stripped = re.sub(mention_pattern, " ", content)
        return re.sub(r"\s+", " ", stripped).strip(" \n\t,;:-")

    async def _send_reply(self, message: discord.Message, content: str) -> None:
        chunks = self._chunk_message(content)
        if not chunks:
            chunks = ["I somehow managed to say nothing. Impressive, but not useful."]

        await message.reply(chunks[0], mention_author=False)
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

    def _chunk_message(self, content: str, *, limit: int = 2000) -> list[str]:
        text = content.strip()
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            split_at = max(
                remaining.rfind("\n\n", 0, limit),
                remaining.rfind("\n", 0, limit),
                remaining.rfind(". ", 0, limit),
                remaining.rfind(" ", 0, limit),
            )
            if split_at <= 0:
                split_at = limit

            chunk = remaining[:split_at].rstrip()
            chunks.append(chunk)
            remaining = remaining[split_at:].lstrip()

        return chunks

    def _register_commands(self) -> None:
        @self.tree.command(name="update_cache", description="Force-refresh the docs cache.")
        async def update_cache(interaction: discord.Interaction[Any]) -> None:
            if interaction.user.id != self.config.owner_user_id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="Only the bot owner can run this one.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if interaction.guild_id != self.config.allowed_guild_id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Wrong Server",
                        description="This command is only available in the configured server.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.defer(thinking=True)

            if self.http_session is None:
                await interaction.followup.send(
                    embed=self._build_embed(
                        title="Failed To Update Cache",
                        description="The HTTP session is not ready yet.",
                        color=discord.Color.red(),
                    )
                )
                return

            result = await self.docs_cache.maybe_refresh(self.http_session, force=True)
            embed = self._cache_result_to_embed(result)
            await interaction.followup.send(embed=embed)

        @self.tree.command(
            name="clear_memory",
            description="Clear the saved conversation context for this channel or thread.",
        )
        async def clear_memory(interaction: discord.Interaction[Any]) -> None:
            if not self._interaction_is_allowed(interaction):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="You do not have access to clear memory here.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if interaction.channel_id is None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Can't Clear Memory",
                        description="I couldn't figure out which channel/thread to clear.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await self.store.clear_scope(scope_id=interaction.channel_id)
            await interaction.response.send_message(
                embed=self._build_embed(
                    title="Memory Cleared",
                    description="The saved conversation context for this channel/thread is gone now.",
                    color=discord.Color.green(),
                )
            )

    def _cache_result_to_embed(self, result: RefreshResult) -> discord.Embed:
        if result.error:
            return self._build_embed(
                title="Failed To Update Cache",
                description=(
                    f"Version: {result.version or 'unknown'}\n"
                    f"Details: {result.error}"
                ),
                color=discord.Color.red(),
            )

        return self._build_embed(
            title="Cache Updated Successfully",
            description=(
                f"Version: {result.version or 'unknown'}\n"
                f"Entries: {result.entry_count}"
            ),
            color=discord.Color.green(),
        )

    def _build_embed(self, *, title: str, description: str, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text="Reader of the Manual")
        return embed

