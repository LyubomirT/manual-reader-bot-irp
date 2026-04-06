from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from rtfm_bot.config import BotConfig
from rtfm_bot.docs_cache import DocsCacheManager, RefreshResult
from rtfm_bot.llm import (
    BatchClassifierMessage,
    ChatRequest,
    PollinationsClient,
    PollinationsError,
    is_ban_user_signal,
)
from rtfm_bot.model_catalog import ModelSpec, get_model_spec, iter_model_specs
from rtfm_bot.rate_limits import RateLimitStatus, SlidingWindowRateLimiter
from rtfm_bot.storage import (
    BAN_SOURCE_AI,
    BAN_SOURCE_MANUAL,
    ConversationMessage,
    ConversationScopeSummary,
    ConversationStore,
    UserBan,
)
from rtfm_bot.statuses import BotStatusSpec, DEFAULT_ROTATING_STATUSES, choose_next_status

LOGGER = logging.getLogger(__name__)
MAX_MEMORY_SCOPE_OPTIONS = 25
MEMORY_VIEW_TIMEOUT_SECONDS = 300
MODEL_RESPONSE_VIEW_TIMEOUT_SECONDS = 1800
INITIAL_STATUS_UPDATE_DELAY_SECONDS = 5
LOADER_EMOJI = "<a:loader:1488104843108290570>"
BATCH_QUEUE_MAX_MESSAGES = 250
BATCH_CONTEXT_MAX_USER_MESSAGES = 15
BATCH_CONTEXT_HISTORY_FETCH_LIMIT = 100
AUTO_REPLY_SIMILARITY_THRESHOLD = 0.72
AI_BAN_BLOCK_MESSAGE = "You're blocked from using Reader of the Manual now."
AI_BAN_NOTICE_MESSAGE = (
    "The AI blocked you for suspected abuse, weird messaging, or wasting the owner's tokens. "
    "Only the bot owner can undo an AI block."
)
GENERIC_BANNED_MESSAGE = "You're blocked from using Reader of the Manual."


@dataclass(slots=True)
class PendingStoredMessage:
    author_id: int | None
    author_name: str | None
    content: str


@dataclass(slots=True)
class QueuedBatchMessage:
    message: discord.Message
    content: str
    author_display_name: str
    candidate: bool


def _truncate_text(value: str | None, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if not compact:
        return "No recent text."
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


class MemoryScopeSelect(discord.ui.Select):
    def __init__(self, parent: "MemoryInspectorView") -> None:
        super().__init__(
            placeholder="Pick a channel or thread",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Loading memory scopes...",
                    value="loading",
                )
            ],
        )
        self._memory_view = parent

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        await self._memory_view.handle_scope_select(interaction, int(self.values[0]))


class MemoryInspectorView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: "ReaderBot",
        actor_id: int,
        guild: discord.Guild,
        scope_summaries: list[ConversationScopeSummary],
        initial_scope_id: int | None = None,
    ) -> None:
        super().__init__(timeout=MEMORY_VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.actor_id = actor_id
        self.guild = guild
        self.scope_summaries = scope_summaries
        self.scope_id = initial_scope_id or (scope_summaries[0].scope_id if scope_summaries else None)
        self.page_index = 0
        self.message: discord.InteractionMessage | None = None

        self.scope_select = MemoryScopeSelect(self)
        self.add_item(self.scope_select)

        self._reposition_page()
        self._refresh_controls()

    @property
    def total_pages(self) -> int:
        if not self.scope_summaries:
            return 1
        return max(1, (len(self.scope_summaries) + MAX_MEMORY_SCOPE_OPTIONS - 1) // MAX_MEMORY_SCOPE_OPTIONS)

    @property
    def selected_summary(self) -> ConversationScopeSummary | None:
        if self.scope_id is None:
            return None
        return next(
            (summary for summary in self.scope_summaries if summary.scope_id == self.scope_id),
            None,
        )

    @property
    def scope_position(self) -> int | None:
        if self.scope_id is None:
            return None
        for index, summary in enumerate(self.scope_summaries, start=1):
            if summary.scope_id == self.scope_id:
                return index
        return None

    async def handle_scope_select(
        self,
        interaction: discord.Interaction[Any],
        scope_id: int,
    ) -> None:
        if not await self._ensure_actor(interaction):
            return

        self.scope_id = scope_id
        self._reposition_page()
        self._refresh_controls()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

        if self.message is None:
            return

        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            return

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button,
    ) -> None:
        del button
        if not await self._ensure_actor(interaction):
            return

        self.page_index = max(0, self.page_index - 1)
        self._ensure_selected_scope_on_page()
        self._refresh_controls()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_panel(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button,
    ) -> None:
        del button
        if not await self._ensure_actor(interaction):
            return

        self.scope_summaries = await self.bot.store.get_scope_summaries(
            guild_id=self.guild.id,
            inactivity_seconds=self.bot.config.conversation_inactivity_seconds,
        )
        if self.scope_summaries and self.scope_id not in {
            summary.scope_id for summary in self.scope_summaries
        }:
            self.scope_id = self.scope_summaries[0].scope_id
        if not self.scope_summaries:
            self.scope_id = None

        self._reposition_page()
        self._refresh_controls()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button,
    ) -> None:
        del button
        if not await self._ensure_actor(interaction):
            return

        self.page_index = min(self.total_pages - 1, self.page_index + 1)
        self._ensure_selected_scope_on_page()
        self._refresh_controls()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _ensure_actor(self, interaction: discord.Interaction[Any]) -> bool:
        if interaction.user.id == self.actor_id:
            return True

        await interaction.response.send_message(
            "This memory panel belongs to someone else.",
            ephemeral=True,
        )
        return False

    async def _build_embed(self) -> discord.Embed:
        if self.scope_id is None:
            return self.bot._build_empty_memory_embed()

        return await self.bot._build_memory_embed(
            guild=self.guild,
            scope_id=self.scope_id,
            summary=self.selected_summary,
            scope_position=self.scope_position,
            total_scopes=len(self.scope_summaries),
            page_index=self.page_index + 1,
            total_pages=self.total_pages,
        )

    def _current_page_summaries(self) -> list[ConversationScopeSummary]:
        start = self.page_index * MAX_MEMORY_SCOPE_OPTIONS
        end = start + MAX_MEMORY_SCOPE_OPTIONS
        return self.scope_summaries[start:end]

    def _ensure_selected_scope_on_page(self) -> None:
        page_summaries = self._current_page_summaries()
        if not page_summaries:
            self.scope_id = None
            return

        page_scope_ids = {summary.scope_id for summary in page_summaries}
        if self.scope_id not in page_scope_ids:
            self.scope_id = page_summaries[0].scope_id

    def _reposition_page(self) -> None:
        if not self.scope_summaries:
            self.page_index = 0
            self.scope_id = None
            return

        if self.scope_id is None:
            self.scope_id = self.scope_summaries[0].scope_id

        selected_index = next(
            (
                index
                for index, summary in enumerate(self.scope_summaries)
                if summary.scope_id == self.scope_id
            ),
            0,
        )
        self.page_index = selected_index // MAX_MEMORY_SCOPE_OPTIONS
        self.scope_id = self.scope_summaries[selected_index].scope_id

    def _refresh_controls(self) -> None:
        page_summaries = self._current_page_summaries()
        if not page_summaries:
            self.scope_select.options = [
                discord.SelectOption(
                    label="No active memory",
                    value="0",
                    description="Nothing is cached right now.",
                )
            ]
            self.scope_select.disabled = True
        else:
            options: list[discord.SelectOption] = []
            for summary in page_summaries:
                scope_name = self.bot._format_scope_label(self.guild, summary.scope_id)
                description = (
                    f"{summary.message_count} msgs | "
                    f"{_truncate_text(summary.last_message_preview, 72)}"
                )
                options.append(
                    discord.SelectOption(
                        label=_truncate_text(scope_name, 100),
                        value=str(summary.scope_id),
                        description=_truncate_text(description, 100),
                        default=summary.scope_id == self.scope_id,
                    )
                )
            self.scope_select.options = options
            self.scope_select.disabled = False

        self.previous_page.disabled = self.page_index <= 0 or self.total_pages <= 1
        self.next_page.disabled = self.page_index >= self.total_pages - 1 or self.total_pages <= 1


class ModelPickerSelect(discord.ui.Select):
    def __init__(self, parent: "ModelPickerView") -> None:
        self._picker_view = parent
        options = [
            discord.SelectOption(
                label=model_spec.display_name[:100],
                value=model_spec.id,
                description=parent.bot._format_model_picker_option(model_spec),
                emoji=model_spec.heaviness_emoji,
                default=model_spec.id == parent.current_model_id,
            )
            for model_spec in iter_model_specs()
        ]
        super().__init__(
            placeholder="Pick your default model",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        await self._picker_view.handle_selection(interaction, self.values[0])


class ModelPickerView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: "ReaderBot",
        actor_id: int,
        current_model_id: str,
    ) -> None:
        super().__init__(timeout=MEMORY_VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.actor_id = actor_id
        self.current_model_id = current_model_id
        self.message: discord.InteractionMessage | None = None
        self.model_select = ModelPickerSelect(self)
        self.add_item(self.model_select)

    async def handle_selection(
        self,
        interaction: discord.Interaction[Any],
        model_id: str,
    ) -> None:
        if not await self._ensure_actor(interaction):
            return
        await self.bot.store.set_user_model_preference(
            user_id=interaction.user.id,
            model_id=model_id,
        )
        self.current_model_id = model_id
        self.model_select.options = [
            discord.SelectOption(
                label=model_spec.display_name[:100],
                value=model_spec.id,
                description=self.bot._format_model_picker_option(model_spec),
                emoji=model_spec.heaviness_emoji,
                default=model_spec.id == model_id,
            )
            for model_spec in iter_model_specs()
        ]
        await interaction.response.edit_message(
            embed=self.bot._build_model_picker_embed(get_model_spec(model_id)),
            view=self,
        )

    async def on_timeout(self) -> None:
        self.model_select.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            return

    async def _ensure_actor(self, interaction: discord.Interaction[Any]) -> bool:
        if interaction.user.id == self.actor_id:
            return True
        await interaction.response.send_message(
            "This model picker belongs to someone else.",
            ephemeral=True,
        )
        return False


class ResponseStatsView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: "ReaderBot",
        model_spec: ModelSpec,
    ) -> None:
        super().__init__(timeout=MODEL_RESPONSE_VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.model_spec = model_spec
        self.message: discord.Message | None = None

        self.model_button = discord.ui.Button(
            label=model_spec.button_label[:80],
            style=discord.ButtonStyle.secondary,
        )
        self.model_button.callback = self._open_model_picker
        self.add_item(self.model_button)
        self.add_item(
            discord.ui.Button(
                label=bot._format_model_rate_button_label(model_spec),
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )
        self.add_item(
            discord.ui.Button(
                label=bot._format_model_context_button_label(model_spec),
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )

    async def on_timeout(self) -> None:
        self.model_button.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            return

    async def _open_model_picker(self, interaction: discord.Interaction[Any]) -> None:
        if not await self.bot._ensure_model_picker_access(interaction):
            return

        current_model = await self.bot._get_user_model_spec(interaction.user.id)
        picker_view = ModelPickerView(
            bot=self.bot,
            actor_id=interaction.user.id,
            current_model_id=current_model.id,
        )
        await interaction.response.send_message(
            embed=self.bot._build_model_picker_embed(current_model),
            view=picker_view,
            ephemeral=True,
        )
        try:
            picker_view.message = await interaction.original_response()
        except discord.HTTPException:
            picker_view.message = None


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
        setattr(self.tree, "interaction_check", self._tree_interaction_check)
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
        self._batch_queue_task: asyncio.Task[None] | None = None
        self._status_rotation_task: asyncio.Task[None] | None = None
        self._status_random = random.Random()
        self._current_status: BotStatusSpec | None = None
        self._batch_queue: list[QueuedBatchMessage] = []
        self._batch_queue_lock = asyncio.Lock()
        self._conversation_storage_limit = max(
            model_spec.scaled_history_limit(config.conversation_max_messages)
            for model_spec in iter_model_specs()
        )
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
                "Docs cache ready with %s pages (version=%s)",
                refresh_result.entry_count,
                refresh_result.version or "unknown",
            )

        self._cache_refresh_task = asyncio.create_task(
            self._cache_refresh_loop(),
            name="docs-cache-refresh-loop",
        )
        if self.config.auto_reply_enabled:
            self._batch_queue_task = asyncio.create_task(
                self._batch_queue_loop(),
                name="batch-docs-triage-loop",
            )
        else:
            LOGGER.info("Automatic replies are disabled; skipping batched docs triage loop.")
        self._status_rotation_task = asyncio.create_task(
            self._status_rotation_loop(),
            name="bot-status-rotation-loop",
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
        if self._batch_queue_task is not None:
            self._batch_queue_task.cancel()
            try:
                await self._batch_queue_task
            except asyncio.CancelledError:
                pass
        if self._status_rotation_task is not None:
            self._status_rotation_task.cancel()
            try:
                await self._status_rotation_task
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
            if (
                self.config.auto_reply_enabled
                and self._message_is_batch_eligible(message)
                and (self.user is None or message.author.id != self.user.id)
            ):
                content = self._prepare_message_content(message)
                if self._content_has_substance(content):
                    await self._enqueue_batch_message(
                        message,
                        content=content,
                        candidate=False,
                    )
            return

        if message.guild is None:
            if await self._handle_banned_message(message):
                return
            await message.channel.send("I only work inside the IntenseRP Next server, sorry.")
            return

        if message.guild.id != self.config.allowed_guild_id:
            return

        targets_bot = await self._message_targets_bot(message)
        if not targets_bot:
            if (
                self.config.auto_reply_enabled
                and self._member_has_allowed_role(message.author)
                and await self._get_user_ban(message.author.id) is None
            ):
                content = self._prepare_message_content(message)
                if self._content_has_substance(content):
                    await self._enqueue_batch_message(
                        message,
                        content=content,
                        candidate=True,
                    )
            return

        if await self._handle_banned_message(message):
            return

        if not self._member_has_allowed_role(message.author):
            await message.reply(
                "You need the helper role to use me here.",
                mention_author=False,
            )
            return

        content = self._prepare_message_content(message)
        if not self._content_has_substance(content):
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

        model_spec = await self._get_user_model_spec(message.author.id)
        await self._handle_ai_request(
            message,
            content=content,
            model_spec=model_spec,
            stored_messages=[
                PendingStoredMessage(
                    author_id=message.author.id,
                    author_name=message.author.display_name,
                    content=content,
                )
            ],
            target_users=[],
            auto_reply=False,
        )

    def _message_is_batch_eligible(self, message: discord.Message) -> bool:
        return (
            message.guild is not None
            and message.guild.id == self.config.allowed_guild_id
        )

    async def _generate_reply(self, request: ChatRequest) -> str:
        if self.llm is None:
            raise RuntimeError("The LLM client is not initialized.")

        response_text = await self.llm.generate_reply(request)
        clean_text = response_text.strip()
        if not clean_text:
            return "I came back empty-handed there. Mind trying that one again?"

        return clean_text

    async def _handle_ai_request(
        self,
        message: discord.Message,
        *,
        content: str,
        model_spec: ModelSpec,
        stored_messages: list[PendingStoredMessage],
        target_users: list[discord.abc.User],
        auto_reply: bool,
    ) -> bool:
        if message.guild is None or message.channel.id is None:
            return False

        channel_status = self._channel_rate_status_for_model(message.channel.id, model_spec)
        if channel_status.retry_after:
            if not auto_reply:
                await message.reply(
                    f"This channel is on cooldown for {channel_status.retry_after}s. Try me again in a minute.",
                    mention_author=False,
                )
            return False

        global_status = self._global_rate_status_for_model(model_spec)
        if global_status.retry_after:
            if not auto_reply:
                await message.reply(
                    f"I'm rate limited right now. Give me about {global_status.retry_after}s to chill.",
                    mention_author=False,
                )
            return False

        self.channel_rate_limiter.hit(message.channel.id)
        self.global_rate_limiter.hit("global")

        history_limit = model_spec.scaled_history_limit(self.config.conversation_max_messages)
        docs_page_limit = model_spec.scaled_docs_limit(self.config.docs_selector_page_limit)
        history = await self.store.get_recent_messages(
            scope_id=message.channel.id,
            guild_id=message.guild.id,
            limit=history_limit,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )
        audience_names = [
            self._display_name_for_user(user)
            for user in target_users
            if user.id != message.author.id
        ]
        request = ChatRequest(
            question=content,
            user_display_name=message.author.display_name,
            history=history,
            docs=self.docs_cache.get_pages(),
            docs_available=self.docs_cache.available,
            model_id=model_spec.id,
            docs_page_limit=docs_page_limit,
            is_auto_reply=auto_reply,
            audience_names=audience_names,
        )

        loader_added = await self._maybe_add_loader_reaction(message)
        try:
            async with message.channel.typing():
                response_text = await self._generate_reply(request)
        except PollinationsError as exc:
            LOGGER.exception("Pollinations request failed.")
            if not auto_reply:
                await message.reply(
                    f"I tripped over the AI request just now: {exc}",
                    mention_author=False,
                )
            return False
        except Exception:
            LOGGER.exception("Unexpected error while generating a reply.")
            if not auto_reply:
                await message.reply(
                    "Something broke while I was digging through the manual. Please try again in a bit.",
                    mention_author=False,
                )
            return False
        finally:
            if loader_added:
                await self._maybe_remove_loader_reaction(message)

        if is_ban_user_signal(response_text):
            try:
                await self._apply_ai_ban(message)
            except Exception:
                LOGGER.exception("Failed to apply an AI-triggered ban.")
                if not auto_reply:
                    await message.reply(
                        "I tripped over the moderation bit just now. Please poke the bot owner.",
                        mention_author=False,
                    )
            return False

        try:
            await self._send_reply(
                message,
                response_text,
                model_spec=model_spec,
                target_users=target_users if len(target_users) > 1 else [],
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to send the generated reply.")
            return False

        for stored_message in stored_messages:
            await self.store.append_message(
                scope_id=message.channel.id,
                guild_id=message.guild.id,
                role="user",
                content=stored_message.content,
                author_id=stored_message.author_id,
                author_name=stored_message.author_name,
                max_messages=self._conversation_storage_limit,
                inactivity_seconds=self.config.conversation_inactivity_seconds,
            )
        await self.store.append_message(
            scope_id=message.channel.id,
            guild_id=message.guild.id,
            role="assistant",
            content=response_text,
            author_id=self.user.id if self.user else None,
            author_name=self.user.display_name if self.user else "Reader of the Manual",
            max_messages=self._conversation_storage_limit,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )
        return True

    async def _batch_queue_loop(self) -> None:
        await self.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(self.config.auto_reply_batch_interval_seconds)
                await self._process_batch_queue()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Unexpected error in the batched docs triage loop.")

    async def _enqueue_batch_message(
        self,
        message: discord.Message,
        *,
        content: str,
        candidate: bool,
    ) -> None:
        entry = QueuedBatchMessage(
            message=message,
            content=content,
            author_display_name=self._display_name_for_batch_model(message.author),
            candidate=candidate,
        )
        async with self._batch_queue_lock:
            self._batch_queue.append(entry)
            if len(self._batch_queue) > BATCH_QUEUE_MAX_MESSAGES:
                self._batch_queue = self._batch_queue[-BATCH_QUEUE_MAX_MESSAGES:]

    async def _process_batch_queue(self) -> None:
        async with self._batch_queue_lock:
            if not self._batch_queue:
                return
            queued_messages = list(self._batch_queue)
            self._batch_queue.clear()

        if self.llm is None:
            return
        if not any(entry.candidate for entry in queued_messages):
            return

        classifier_messages: list[BatchClassifierMessage] = []
        entry_by_batch_id: dict[str, QueuedBatchMessage] = {}
        for index, entry in enumerate(queued_messages, start=1):
            batch_id = f"M{index:02d}"
            classifier_messages.append(
                BatchClassifierMessage(
                    batch_id=batch_id,
                    author_display_name=entry.author_display_name,
                    content=entry.content,
                    channel_label=self._format_batch_channel_label(entry.message),
                    created_at_label=self._format_batch_timestamp(entry.message.created_at),
                    is_bot=not entry.candidate,
                )
            )
            entry_by_batch_id[batch_id] = entry

        conversation_prefix = await self._build_batch_classifier_context_prefix(
            queued_messages
        )
        try:
            selected_ids = await self.llm.classify_docs_candidates(
                classifier_messages,
                conversation_prefix=conversation_prefix,
            )
        except PollinationsError:
            LOGGER.exception("Batched docs classifier failed.")
            return

        selected_entries = [
            entry_by_batch_id[batch_id]
            for batch_id in selected_ids
            if batch_id in entry_by_batch_id and entry_by_batch_id[batch_id].candidate
        ]
        if not selected_entries:
            return

        for cluster in self._cluster_batch_entries(selected_entries):
            latest_entry = cluster[-1]
            model_spec = await self._get_user_model_spec(latest_entry.message.author.id)
            target_users: list[discord.abc.User] = []
            seen_user_ids: set[int] = set()
            for entry in cluster:
                if entry.message.author.id in seen_user_ids:
                    continue
                seen_user_ids.add(entry.message.author.id)
                target_users.append(entry.message.author)

            await self._handle_ai_request(
                latest_entry.message,
                content=latest_entry.content,
                model_spec=model_spec,
                stored_messages=[
                    PendingStoredMessage(
                        author_id=entry.message.author.id,
                        author_name=entry.author_display_name,
                        content=entry.content,
                    )
                    for entry in cluster
                ],
                target_users=target_users,
                auto_reply=True,
            )

    def _cluster_batch_entries(
        self,
        entries: list[QueuedBatchMessage],
    ) -> list[list[QueuedBatchMessage]]:
        remaining = sorted(entries, key=lambda entry: entry.message.created_at)
        clusters: list[list[QueuedBatchMessage]] = []

        while remaining:
            anchor = remaining.pop()
            cluster = [anchor]
            unmatched: list[QueuedBatchMessage] = []
            for candidate in remaining:
                if self._queued_messages_are_similar(candidate, anchor):
                    cluster.append(candidate)
                else:
                    unmatched.append(candidate)
            remaining = unmatched
            clusters.append(sorted(cluster, key=lambda entry: entry.message.created_at))

        clusters.reverse()
        return clusters

    async def _build_batch_classifier_context_prefix(
        self,
        queued_messages: list[QueuedBatchMessage],
    ) -> str:
        queued_ids_by_channel: dict[int, set[int]] = {}
        representative_messages: dict[int, discord.Message] = {}

        for entry in queued_messages:
            channel_id = entry.message.channel.id
            queued_ids_by_channel.setdefault(channel_id, set()).add(entry.message.id)
            representative_messages.setdefault(channel_id, entry.message)

        channel_blocks: list[str] = []
        for channel_id, representative_message in representative_messages.items():
            lines = await self._fetch_batch_context_lines(
                representative_message,
                exclude_message_ids=queued_ids_by_channel[channel_id],
            )
            if not lines:
                continue
            channel_blocks.append(
                f"{self._format_batch_channel_label(representative_message)}\n"
                + "\n".join(lines)
            )

        return "\n\n".join(channel_blocks)

    async def _fetch_batch_context_lines(
        self,
        message: discord.Message,
        *,
        exclude_message_ids: set[int],
    ) -> list[str]:
        lines: list[str] = []
        try:
            async for history_message in message.channel.history(
                limit=BATCH_CONTEXT_HISTORY_FETCH_LIMIT
            ):
                if history_message.author.bot:
                    continue
                if history_message.id in exclude_message_ids:
                    continue

                content = self._prepare_message_content(history_message)
                if not self._content_has_substance(content):
                    continue

                lines.append(
                    self._format_batch_context_line(
                        history_message,
                        content=content,
                    )
                )
                if len(lines) >= BATCH_CONTEXT_MAX_USER_MESSAGES:
                    break
        except (
            AttributeError,
            discord.Forbidden,
            discord.HTTPException,
            discord.NotFound,
        ):
            return []

        lines.reverse()
        return lines

    def _queued_messages_are_similar(
        self,
        first: QueuedBatchMessage,
        second: QueuedBatchMessage,
    ) -> bool:
        if first.message.channel.id != second.message.channel.id:
            return False

        first_text = self._normalize_similarity_text(first.content)
        second_text = self._normalize_similarity_text(second.content)
        if not first_text or not second_text:
            return False

        first_tokens = set(re.findall(r"[a-z0-9]+", first_text))
        second_tokens = set(re.findall(r"[a-z0-9]+", second_text))
        token_overlap = 0.0
        if first_tokens and second_tokens:
            token_overlap = len(first_tokens & second_tokens) / max(
                1,
                min(len(first_tokens), len(second_tokens)),
            )

        sequence_score = SequenceMatcher(None, first_text, second_text).ratio()
        return max(token_overlap, sequence_score) >= AUTO_REPLY_SIMILARITY_THRESHOLD

    def _normalize_similarity_text(self, content: str) -> str:
        tokens = tokenize(content)
        if tokens:
            return " ".join(tokens)
        return re.sub(r"\s+", " ", content.casefold()).strip()

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
                        "Docs cache refreshed successfully (%s pages, version=%s)",
                        result.entry_count,
                        result.version or "unknown",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Unexpected error in docs cache refresh loop.")

    async def _status_rotation_loop(self) -> None:
        await self.wait_until_ready()
        # Avoid changing presence immediately after the gateway becomes ready.
        await asyncio.sleep(
            min(
                INITIAL_STATUS_UPDATE_DELAY_SECONDS,
                self.config.status_rotation_interval_seconds,
            )
        )

        while True:
            try:
                await self._rotate_status()
                await asyncio.sleep(self.config.status_rotation_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Unexpected error in bot status rotation loop.")
                await asyncio.sleep(self.config.status_rotation_interval_seconds)

    async def _rotate_status(self) -> None:
        next_status = choose_next_status(
            DEFAULT_ROTATING_STATUSES,
            rng=self._status_random,
            current=self._current_status,
        )
        activity = self._build_status_activity(next_status)
        await self.change_presence(
            activity=activity,
            status=discord.Status.online,
        )
        self._current_status = next_status
        LOGGER.info("Updated bot status: %s %s", next_status.kind, next_status.text)

    def _build_status_activity(
        self,
        status: BotStatusSpec,
    ) -> discord.Activity | discord.Game | discord.CustomActivity:
        if status.kind == "watching":
            return discord.Activity(
                name=status.text,
                type=discord.ActivityType.watching,
            )
        if status.kind == "playing":
            return discord.Game(name=status.text)
        if status.kind == "listening":
            return discord.Activity(
                name=status.text,
                type=discord.ActivityType.listening,
            )
        if status.kind == "custom":
            return discord.CustomActivity(name=status.text)
        raise ValueError(f"Unsupported bot status kind: {status.kind}")

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

    def _prepare_message_content(self, message: discord.Message) -> str:
        content = message.content

        for mentioned_user in message.mentions:
            display_name = self._display_name_for_user(mentioned_user)
            placeholder = f"[mention of ({display_name})]"
            content = re.sub(rf"<@!?{mentioned_user.id}>", placeholder, content)

        for mentioned_role in message.role_mentions:
            content = content.replace(
                f"<@&{mentioned_role.id}>",
                f"[mention of role ({mentioned_role.name})]",
            )

        if message.guild is not None:
            def replace_channel(match: re.Match[str]) -> str:
                channel_id = int(match.group(1))
                channel = message.guild.get_channel(channel_id) or message.guild.get_thread(channel_id)
                if channel is None:
                    return "[mention of channel (unknown)]"
                return f"[mention of channel ({channel.name})]"

            content = re.sub(r"<#(\d+)>", replace_channel, content)

        return re.sub(r"\s+", " ", content).strip(" \n\t,;:-")

    def _content_has_substance(self, content: str) -> bool:
        stripped = re.sub(r"\[mention of [^\]]+\]", " ", content, flags=re.IGNORECASE)
        stripped = re.sub(r"\[mention of role [^\]]+\]", " ", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\[mention of channel [^\]]+\]", " ", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"[^a-z0-9]+", "", stripped.casefold())
        return bool(stripped)

    async def _get_user_model_spec(self, user_id: int) -> ModelSpec:
        preference = await self.store.get_user_model_preference(user_id=user_id)
        if preference is not None:
            return get_model_spec(preference.model_id)
        return get_model_spec(self.config.pollinations_model)

    async def _ensure_model_picker_access(
        self,
        interaction: discord.Interaction[Any],
    ) -> bool:
        ban = await self._get_user_ban(interaction.user.id)
        if ban is not None:
            await interaction.response.send_message(
                embed=self._build_banned_embed(),
                ephemeral=True,
            )
            return False

        if not self._interaction_is_allowed(interaction):
            await interaction.response.send_message(
                embed=self._build_embed(
                    title="Nope",
                    description="You need the helper role to change your model here.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return False

        return True

    def _format_window_short(self, seconds: int) -> str:
        if seconds % 3600 == 0:
            return f"{seconds // 3600}h"
        if seconds % 60 == 0:
            return f"{seconds // 60}m"
        return f"{seconds}s"

    def _format_model_rate_button_label(self, model_spec: ModelSpec) -> str:
        channel_limit = model_spec.scaled_rate_limit(self.config.channel_rate_limit_count)
        global_limit = model_spec.scaled_rate_limit(self.config.global_rate_limit_count)
        return (
            f"{channel_limit}/{self._format_window_short(self.config.channel_rate_limit_window_seconds)}"
            f" • {global_limit}/{self._format_window_short(self.config.global_rate_limit_window_seconds)}"
        )[:80]

    def _format_model_context_button_label(self, model_spec: ModelSpec) -> str:
        history_limit = model_spec.scaled_history_limit(self.config.conversation_max_messages)
        docs_limit = model_spec.scaled_docs_limit(self.config.docs_selector_page_limit)
        return f"{history_limit} mem • {docs_limit} docs • {model_spec.context_label}"[:80]

    def _format_model_picker_option(self, model_spec: ModelSpec) -> str:
        return (
            f"{model_spec.heaviness_name} • {model_spec.context_label} ctx • "
            f"{model_spec.scaled_history_limit(self.config.conversation_max_messages)} mem"
        )[:100]

    def _build_model_picker_embed(self, model_spec: ModelSpec) -> discord.Embed:
        embed = self._build_embed(
            title="Pick Your Model",
            description=(
                "This changes your personal default model for future answers. "
                "Use the selector below whenever you want to swap."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Current", value=model_spec.button_label, inline=False)
        embed.add_field(
            name="Heaviness",
            value=model_spec.heaviness_label,
            inline=True,
        )
        embed.add_field(
            name="Rate",
            value=self._format_model_rate_button_label(model_spec),
            inline=True,
        )
        embed.add_field(
            name="Context",
            value=self._format_model_context_button_label(model_spec),
            inline=False,
        )
        return embed

    def _channel_rate_status_for_model(
        self,
        channel_id: int,
        model_spec: ModelSpec,
    ) -> RateLimitStatus:
        limit = model_spec.scaled_rate_limit(self.config.channel_rate_limit_count)
        return self.channel_rate_limiter.status(channel_id, limit=limit)

    def _global_rate_status_for_model(self, model_spec: ModelSpec) -> RateLimitStatus:
        limit = model_spec.scaled_rate_limit(self.config.global_rate_limit_count)
        return self.global_rate_limiter.status("global", limit=limit)

    def _format_batch_channel_label(self, message: discord.Message) -> str:
        guild = message.guild
        if guild is None:
            return "#unknown"
        return self._format_scope_label(guild, message.channel.id)

    def _format_batch_timestamp(self, created_at: datetime) -> str:
        return created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")

    def _format_batch_context_line(
        self,
        message: discord.Message,
        *,
        content: str,
    ) -> str:
        return (
            f"{self._format_batch_timestamp(message.created_at)} | "
            f"{self._display_name_for_batch_model(message.author)}: {content}"
        )

    async def _maybe_add_loader_reaction(self, message: discord.Message) -> bool:
        emoji = discord.PartialEmoji.from_str(LOADER_EMOJI)
        try:
            await message.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException):
            return False
        return True

    async def _maybe_remove_loader_reaction(self, message: discord.Message) -> None:
        if self.user is None:
            return
        emoji = discord.PartialEmoji.from_str(LOADER_EMOJI)
        try:
            await message.remove_reaction(emoji, self.user)
        except (discord.Forbidden, discord.HTTPException):
            return

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

    def _interaction_is_admin(self, interaction: discord.Interaction[Any]) -> bool:
        if interaction.guild_id != self.config.allowed_guild_id:
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        if interaction.user.id == self.config.owner_user_id:
            return True

        permissions = interaction.user.guild_permissions
        return permissions.administrator or permissions.manage_guild

    def _user_is_owner(self, user_id: int) -> bool:
        return user_id == self.config.owner_user_id

    def _display_name_for_user(self, user: discord.abc.User) -> str:
        display_name = getattr(user, "display_name", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()

        global_name = getattr(user, "global_name", None)
        if isinstance(global_name, str) and global_name.strip():
            return global_name.strip()

        name = getattr(user, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()

        return "Unknown user"

    def _display_name_for_batch_model(self, user: discord.abc.User) -> str:
        display_name = self._display_name_for_user(user)
        user_id = getattr(user, "id", None)
        if isinstance(user_id, int) and self._user_is_owner(user_id):
            return f"{display_name} [MAINTAINER]"
        return display_name

    async def _tree_interaction_check(self, interaction: discord.Interaction[Any]) -> bool:
        ban = await self._get_user_ban(interaction.user.id)
        if ban is None:
            return True

        await interaction.response.send_message(
            embed=self._build_banned_embed(),
            ephemeral=True,
        )
        return False

    async def _get_user_ban(self, user_id: int) -> UserBan | None:
        if self._user_is_owner(user_id):
            return None
        return await self.store.get_user_ban(user_id=user_id)

    async def _handle_banned_message(self, message: discord.Message) -> bool:
        ban = await self._get_user_ban(message.author.id)
        if ban is None:
            return False

        await message.reply(
            GENERIC_BANNED_MESSAGE,
            mention_author=False,
        )
        return True

    async def _apply_ai_ban(self, message: discord.Message) -> None:
        if self._user_is_owner(message.author.id):
            LOGGER.warning(
                "Ignored AI-triggered ban attempt for owner user %s.",
                message.author.id,
            )
            await message.reply(
                "I almost swung the ban hammer there, but I won't auto-ban the bot owner.",
                mention_author=False,
            )
            return

        banned_by_user_id = self.user.id if self.user is not None else None
        banned_by_name = self.user.display_name if self.user is not None else "Reader of the Manual"
        await self.store.ban_user(
            user_id=message.author.id,
            source=BAN_SOURCE_AI,
            banned_by_user_id=banned_by_user_id,
            banned_by_name=banned_by_name,
        )

        block_message = await message.reply(
            AI_BAN_BLOCK_MESSAGE,
            mention_author=False,
        )
        await block_message.reply(
            AI_BAN_NOTICE_MESSAGE,
            mention_author=False,
        )

    def _strip_bot_mentions(self, content: str) -> str:
        if self.user is None:
            return content.strip()

        mention_pattern = rf"<@!?{self.user.id}>"
        stripped = re.sub(mention_pattern, " ", content)
        return re.sub(r"\s+", " ", stripped).strip(" \n\t,;:-")

    async def _send_reply(
        self,
        message: discord.Message,
        content: str,
        *,
        model_spec: ModelSpec,
        target_users: list[discord.abc.User],
    ) -> None:
        allowed_mentions = None
        prefix = ""
        if target_users:
            unique_users: list[discord.abc.User] = []
            seen_user_ids: set[int] = set()
            for user in target_users:
                if user.id in seen_user_ids:
                    continue
                seen_user_ids.add(user.id)
                unique_users.append(user)
            if unique_users:
                prefix = " ".join(user.mention for user in unique_users)
                allowed_mentions = discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=unique_users,
                    replied_user=False,
                )

        full_content = f"{prefix}\n{content}" if prefix else content
        chunks = self._chunk_message(full_content)
        if not chunks:
            chunks = ["I somehow managed to say nothing. Impressive, but not useful."]

        view = ResponseStatsView(bot=self, model_spec=model_spec)
        sent_message = await message.reply(
            chunks[0],
            mention_author=False,
            allowed_mentions=allowed_mentions,
            view=view,
        )
        view.message = sent_message
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

    def _resolve_scope(
        self,
        guild: discord.Guild,
        scope_id: int,
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        channel = guild.get_channel(scope_id)
        if channel is not None:
            return channel

        thread = guild.get_thread(scope_id)
        if thread is not None:
            return thread

        get_channel_or_thread = getattr(guild, "get_channel_or_thread", None)
        if callable(get_channel_or_thread):
            resolved = get_channel_or_thread(scope_id)
            if resolved is not None:
                return resolved

        return None

    def _format_scope_label(self, guild: discord.Guild, scope_id: int) -> str:
        scope = self._resolve_scope(guild, scope_id)
        if scope is None:
            return f"Unknown scope ({scope_id})"
        if isinstance(scope, discord.Thread):
            return f"{scope.parent.name if scope.parent else 'thread'} / {scope.name}"
        return f"#{scope.name}"

    def _format_memory_author(self, message: ConversationMessage) -> str:
        if message.author_name:
            return message.author_name
        if message.role == "assistant":
            return "Reader of the Manual"
        return "Unknown user"

    def _format_memory_transcript(self, messages: list[ConversationMessage]) -> str:
        if not messages:
            return "No saved messages in this scope right now."

        lines = []
        for message in messages:
            timestamp = discord.utils.format_dt(message.created_at, style="t")
            speaker = self._format_memory_author(message)
            content = _truncate_text(message.content, 180)
            lines.append(f"{timestamp} {speaker}: {content}")

        transcript = "\n".join(lines)
        if len(transcript) <= 4000:
            return transcript
        return transcript[:3997].rstrip() + "..."

    async def _build_memory_embed(
        self,
        *,
        guild: discord.Guild,
        scope_id: int,
        summary: ConversationScopeSummary | None,
        scope_position: int | None = None,
        total_scopes: int | None = None,
        page_index: int | None = None,
        total_pages: int | None = None,
    ) -> discord.Embed:
        messages = await self.store.get_recent_messages(
            scope_id=scope_id,
            guild_id=guild.id,
            limit=self._conversation_storage_limit,
            inactivity_seconds=self.config.conversation_inactivity_seconds,
        )

        scope = self._resolve_scope(guild, scope_id)
        scope_title = self._format_scope_label(guild, scope_id)
        scope_ref = scope.mention if scope is not None and hasattr(scope, "mention") else f"`{scope_id}`"
        embed = discord.Embed(
            title=f"Memory Inspector: {scope_title}",
            description=self._format_memory_transcript(messages),
            color=discord.Color.blurple(),
        )

        summary_lines = [f"Scope: {scope_ref}"]
        summary_lines.append(
            f"Stored messages: {summary.message_count if summary is not None else len(messages)}"
        )
        if summary is not None:
            summary_lines.append(
                f"Last activity: {discord.utils.format_dt(summary.last_activity_at, style='R')}"
            )
        summary_lines.append(f"Message cap: {self._conversation_storage_limit}")
        summary_lines.append(
            f"Inactivity expiry: {self.config.conversation_inactivity_seconds}s"
        )
        if scope_position is not None and total_scopes is not None:
            summary_lines.append(f"Scope position: {scope_position}/{total_scopes}")

        embed.add_field(name="Summary", value="\n".join(summary_lines), inline=False)

        footer_parts = ["Reader of the Manual"]
        if page_index is not None and total_pages is not None:
            footer_parts.append(f"Page {page_index}/{total_pages}")
        embed.set_footer(text=" | ".join(footer_parts))
        return embed

    def _build_empty_memory_embed(self) -> discord.Embed:
        return self._build_embed(
            title="Memory Inspector",
            description="No active channel or thread memory is cached right now.",
            color=discord.Color.orange(),
        )

    def _build_banned_embed(self) -> discord.Embed:
        return self._build_embed(
            title="Blocked",
            description=GENERIC_BANNED_MESSAGE,
            color=discord.Color.red(),
        )

    def _format_rate_limit_status(self, status: RateLimitStatus) -> str:
        if status.used == 0:
            recent_activity = "No recent hits."
        else:
            recent_activity = f"Oldest hit resets in about {status.resets_in}s."

        state = "Cooling down" if status.retry_after else "Ready"
        lines = [
            f"State: {state}",
            f"Used: {status.used}/{status.limit}",
            f"Remaining: {status.remaining}",
            f"Window: {status.window_seconds}s",
            recent_activity,
        ]
        if status.retry_after:
            lines.append(f"Retry after: {status.retry_after}s")
        return "\n".join(lines)

    def _build_rate_limit_status_embed(
        self,
        channel_id: int,
        model_spec: ModelSpec,
    ) -> discord.Embed:
        channel_status = self._channel_rate_status_for_model(channel_id, model_spec)
        global_status = self._global_rate_status_for_model(model_spec)
        embed = self._build_embed(
            title="Rate Limit Status",
            description=(
                "Current cooldown buckets for this channel and the whole bot "
                f"using your {model_spec.display_name} limits."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="This Channel",
            value=self._format_rate_limit_status(channel_status),
            inline=False,
        )
        embed.add_field(
            name="Global",
            value=self._format_rate_limit_status(global_status),
            inline=False,
        )
        return embed

    def _build_help_embed(self) -> discord.Embed:
        mention = self.user.mention if self.user is not None else "@Reader of the Manual"
        embed = self._build_embed(
            title="How To Use Reader of the Manual",
            description=(
                f"Mention {mention} or reply to one of my answers with a docs question and "
                "I'll dig through the IntenseRP Next manual for you."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Public Commands",
            value="/help\n/model\n/rate_limit_status",
            inline=False,
        )
        embed.add_field(
            name="Helper Role",
            value=(
                "Mention or reply to me to ask docs questions.\n"
                "/clear_memory to wipe the saved conversation for the current channel or thread."
            ),
            inline=False,
        )
        embed.add_field(
            name="Admin Stuff",
            value=(
                "/inspect_channel_memory\n"
                "/inspect_memory_global\n"
                "/ban_user\n"
                "/unban_user\n"
                "/update_cache (owner only)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Example",
            value=f"{mention} how do I install the app?",
            inline=False,
        )
        return embed

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
            name="inspect_channel_memory",
            description="Inspect the saved memory for this channel or thread.",
        )
        @app_commands.default_permissions(administrator=True)
        async def inspect_channel_memory(interaction: discord.Interaction[Any]) -> None:
            if not self._interaction_is_admin(interaction):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="You need admin-level server permissions for this one.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if interaction.guild is None or interaction.channel_id is None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Can't Inspect Memory",
                        description="I couldn't figure out which channel/thread to inspect.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            summaries = await self.store.get_scope_summaries(
                guild_id=interaction.guild.id,
                inactivity_seconds=self.config.conversation_inactivity_seconds,
            )
            summary = next(
                (
                    candidate
                    for candidate in summaries
                    if candidate.scope_id == interaction.channel_id
                ),
                None,
            )
            embed = await self._build_memory_embed(
                guild=interaction.guild,
                scope_id=interaction.channel_id,
                summary=summary,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(
            name="inspect_memory_global",
            description="Browse saved memory across channels and threads.",
        )
        @app_commands.default_permissions(administrator=True)
        async def inspect_memory_global(interaction: discord.Interaction[Any]) -> None:
            if not self._interaction_is_admin(interaction):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="You need admin-level server permissions for this one.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if interaction.guild is None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Wrong Server",
                        description="This command only works inside the configured server.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            summaries = await self.store.get_scope_summaries(
                guild_id=interaction.guild.id,
                inactivity_seconds=self.config.conversation_inactivity_seconds,
            )
            if not summaries:
                await interaction.response.send_message(
                    embed=self._build_empty_memory_embed(),
                    ephemeral=True,
                )
                return

            view = MemoryInspectorView(
                bot=self,
                actor_id=interaction.user.id,
                guild=interaction.guild,
                scope_summaries=summaries,
            )
            embed = await self._build_memory_embed(
                guild=interaction.guild,
                scope_id=view.scope_id,
                summary=view.selected_summary,
                scope_position=view.scope_position,
                total_scopes=len(summaries),
                page_index=view.page_index + 1,
                total_pages=view.total_pages,
            )
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
            try:
                view.message = await interaction.original_response()
            except discord.HTTPException:
                view.message = None

        @self.tree.command(
            name="ban_user",
            description="Block a user from using the bot.",
        )
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(user="User to block from the bot")
        async def ban_user(
            interaction: discord.Interaction[Any],
            user: discord.User,
        ) -> None:
            if not self._interaction_is_admin(interaction):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="You need admin-level server permissions for this one.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if user.id == interaction.user.id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nice Try",
                        description="I'm not letting you ban yourself with a slash command.",
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return

            if self._user_is_owner(user.id):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Hands Off",
                        description="The bot owner cannot be banned.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if self.user is not None and user.id == self.user.id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Absolutely Not",
                        description="I'm not banning myself. That seems bad for business.",
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return

            existing_ban = await self.store.get_user_ban(user_id=user.id)
            if existing_ban is not None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Already Blocked",
                        description=f"{user.mention} is already blocked from using the bot.",
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return

            await self.store.ban_user(
                user_id=user.id,
                source=BAN_SOURCE_MANUAL,
                banned_by_user_id=interaction.user.id,
                banned_by_name=self._display_name_for_user(interaction.user),
            )
            await interaction.response.send_message(
                embed=self._build_embed(
                    title="User Blocked",
                    description=f"{user.mention} can no longer use the bot.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )

        @self.tree.command(
            name="unban_user",
            description="Unblock a user from using the bot.",
        )
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(user="User to unblock from the bot")
        async def unban_user(
            interaction: discord.Interaction[Any],
            user: discord.User,
        ) -> None:
            if not self._interaction_is_admin(interaction):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Nope",
                        description="You need admin-level server permissions for this one.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            existing_ban = await self.store.get_user_ban(user_id=user.id)
            if existing_ban is None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Not Blocked",
                        description=f"{user.mention} is not currently blocked.",
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return

            if (
                existing_ban.source == BAN_SOURCE_AI
                and interaction.user.id != self.config.owner_user_id
            ):
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Owner Only",
                        description="Only the bot owner can undo an AI-triggered block.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await self.store.unban_user(user_id=user.id)
            await interaction.response.send_message(
                embed=self._build_embed(
                    title="User Unblocked",
                    description=f"{user.mention} can use the bot again.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )

        @self.tree.command(name="help", description="Show how to use the bot.")
        async def help_command(interaction: discord.Interaction[Any]) -> None:
            if interaction.guild_id != self.config.allowed_guild_id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Wrong Server",
                        description="This bot only works inside the configured server.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=self._build_help_embed(),
                ephemeral=True,
            )

        @self.tree.command(name="model", description="Pick your default answer model.")
        async def model_command(interaction: discord.Interaction[Any]) -> None:
            if not await self._ensure_model_picker_access(interaction):
                return

            current_model = await self._get_user_model_spec(interaction.user.id)
            picker_view = ModelPickerView(
                bot=self,
                actor_id=interaction.user.id,
                current_model_id=current_model.id,
            )
            await interaction.response.send_message(
                embed=self._build_model_picker_embed(current_model),
                view=picker_view,
                ephemeral=True,
            )
            try:
                picker_view.message = await interaction.original_response()
            except discord.HTTPException:
                picker_view.message = None

        @self.tree.command(
            name="rate_limit_status",
            description="Show the current channel and global cooldown status.",
        )
        async def rate_limit_status(interaction: discord.Interaction[Any]) -> None:
            if interaction.guild_id != self.config.allowed_guild_id:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Wrong Server",
                        description="This bot only works inside the configured server.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if interaction.channel_id is None:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Can't Check Rate Limits",
                        description="I couldn't determine the current channel.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            model_spec = await self._get_user_model_spec(interaction.user.id)
            await interaction.response.send_message(
                embed=self._build_rate_limit_status_embed(interaction.channel_id, model_spec),
                ephemeral=True,
            )

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
                f"Pages: {result.entry_count}"
            ),
            color=discord.Color.green(),
        )

    def _build_embed(self, *, title: str, description: str, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_footer(text="Reader of the Manual")
        return embed
