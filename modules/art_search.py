"""
Art search module - channel-restricted image search for a user's posted art.

Feature goals:
- Only searches in configured channels (per guild)
- Filters out GIFs
- Returns paginated message links
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import discord

from core.art_search_storage import ArtSearchStore
from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled

logger = logging.getLogger("discbot.art_search")

MODULE_NAME = "artsearch"
RESULTS_PER_PAGE = 5
RESULT_BATCH_SIZE = 20
SCAN_CHUNK_MESSAGES = 200
MAX_MESSAGES_SCANNED_TOTAL = 50000
PREVIEW_IMAGE_WIDTH = 768
PREVIEW_IMAGE_HEIGHT = 768


@dataclass
class _ArtHit:
    channel_id: int
    message_id: int
    author_id: int
    created_at_iso: str
    attachment_url: str
    filename: str


def setup_art_search() -> None:
    help_system.register_module(
        name="Art Search",
        description="Search for images posted by a user in approved channels.",
        help_command="artsearch help",
        commands=[
            ("art search @user [page]", "Search a user's images (filters GIFs)"),
            ("art channels", "List configured search channels"),
            ("art channels add <channel_id>", "Add a channel to the search allowlist (mod only)"),
            ("art channels remove <channel_id>", "Remove a channel from the search allowlist (mod only)"),
            ("artsearch search @user [page]", "Alias for `art search`"),
            ("artsearch help", "Show this help message"),
        ],
    )


async def handle_art_search_command(message: discord.Message, bot: discord.Client) -> bool:
    if not message.guild:
        return False

    content = (message.content or "").strip()
    if not content:
        return False

    parts = content.split()
    if not parts:
        return False

    root = parts[0].lower().strip(",.!?")
    if root not in {"artsearch", "art"}:
        return False
    if len(parts) == 1:
        return False

    sub = parts[1].lower().strip(",.!?")
    if sub == "help":
        if root == "artsearch":
            await _cmd_help(message)
            return True
        # Keep `art help` reserved for Art Tools.
        return False

    if sub == "channels":
        await _cmd_channels(message, parts[2:])
        return True

    if sub == "search":
        if not await is_module_enabled(message.guild.id, MODULE_NAME):
            await message.channel.send(
                "Art Search module is disabled in this server.\n"
                "An administrator can enable it with `modules enable artsearch`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        await _cmd_search(message, parts[2:])
        return True

    return False


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_help("Art Search")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(" Usage: `art search @user [page]`")


async def _cmd_channels(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return

    store = ArtSearchStore(message.guild.id)
    await store.initialize()

    if not args:
        channels = await store.list_channels()
        if not channels:
            await message.channel.send(
                "No art-search channels configured.\n"
                "Mods can add one with: `art channels add <channel_id>`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        lines = ["**Art search channels:**"] + [f"- `{cid}`" for cid in channels]
        await message.channel.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())
        return

    action = args[0].lower()
    if action not in {"add", "remove"}:
        await message.channel.send(" Usage: `art channels [add|remove] <channel_id>`")
        return

    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "art channels"):
        await message.channel.send(" You don't have permission to manage art-search channels.")
        return

    if len(args) < 2 or not args[1].isdigit():
        await message.channel.send(" Usage: `art channels [add|remove] <channel_id>`")
        return

    channel_id = int(args[1])
    if action == "add":
        await store.add_channel(channel_id)
        await message.channel.send(f" Added `{channel_id}` to art-search channels.")
        return

    ok = await store.remove_channel(channel_id)
    if ok:
        await message.channel.send(f" Removed `{channel_id}` from art-search channels.")
    else:
        await message.channel.send(f" `{channel_id}` was not configured.")


async def _cmd_search(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return

    if not args or not message.mentions:
        await message.channel.send(" Usage: `art search @user [page]`")
        return

    target = message.mentions[0]
    page = 1
    if len(args) >= 2 and args[1].isdigit():
        page = max(1, int(args[1]))

    store = ArtSearchStore(message.guild.id)
    await store.initialize()
    channel_ids = await store.list_channels()
    if not channel_ids:
        await message.channel.send(
            "Art search is channel-restricted but no channels are configured.\n"
            "Mods: `art channels add <channel_id>`",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    needed = max(page * RESULTS_PER_PAGE, RESULTS_PER_PAGE)
    desired_loaded = max(
        RESULT_BATCH_SIZE,
        int(math.ceil(needed / RESULT_BATCH_SIZE) * RESULT_BATCH_SIZE),
    )

    view = _ArtSearchView(
        guild_id=message.guild.id,
        author_id=message.author.id,
        target_display_name=target.display_name,
        target_user_id=target.id,
        channel_ids=channel_ids,
        hits=[],
        page_index=max(0, page - 1),
    )
    await view.bootstrap(message.guild, desired_loaded=desired_loaded, time_budget_seconds=3.0)
    hits = view.hits
    if not hits:
        await message.channel.send(f"No recent images found for {target.mention}.", allowed_mentions=discord.AllowedMentions.none())
        return

    total_pages = max(1, int(math.ceil(len(hits) / RESULTS_PER_PAGE)))
    page = min(page, total_pages)
    start_page_index = page - 1
    if start_page_index * RESULTS_PER_PAGE >= len(hits):
        await message.channel.send("No more results.", allowed_mentions=discord.AllowedMentions.none())
        return

    view.page_index = start_page_index
    view._sync_buttons()
    embed = view.build_embed()
    sent = await message.channel.send(
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    view.message = sent


def _pick_image_attachment(msg: discord.Message) -> Optional[discord.Attachment]:
    for att in msg.attachments:
        ctype = (att.content_type or "").lower()
        filename = (att.filename or "").lower()

        is_image = ctype.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        if not is_image:
            continue
        if filename.endswith(".gif") or ctype == "image/gif":
            continue
        return att
    return None


def _message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _scaled_image_url(url: str, *, width: int, height: int) -> str:
    try:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["width"] = str(int(width))
        query["height"] = str(int(height))
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}width={int(width)}&height={int(height)}"


class _ArtSearchView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        author_id: int,
        target_display_name: str,
        target_user_id: int,
        channel_ids: list[int],
        hits: list[_ArtHit],
        page_index: int = 0,
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.author_id = int(author_id)
        self.target_display_name = target_display_name
        self.target_user_id = int(target_user_id)
        self.channel_ids = [int(c) for c in channel_ids]
        self.hits = hits
        self.page_index = max(0, int(page_index))
        self.message: Optional[discord.Message] = None

        self.channel_before: dict[int, Optional[int]] = {cid: None for cid in self.channel_ids}
        self.channel_done: set[int] = set()
        self._channel_rr_index = 0
        self.scanned_messages_total = 0
        self.truncated = False  # scan limit reached
        self._sync_buttons()

    def _page_count(self) -> int:
        return max(1, int(math.ceil(len(self.hits) / RESULTS_PER_PAGE)))

    def _scan_complete(self) -> bool:
        return len(self.channel_done) >= len(self.channel_ids)

    def _sync_buttons(self) -> None:
        total_pages = self._page_count()
        self.prev.disabled = self.page_index <= 0
        at_last_known_page = self.page_index >= (total_pages - 1)
        self.next.disabled = bool(at_last_known_page and (self.truncated or self._scan_complete()))

    def build_embed(self) -> discord.Embed:
        total_pages = self._page_count()
        self.page_index = max(0, min(self.page_index, total_pages - 1))

        start = self.page_index * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        page_hits = self.hits[start:end]

        preview = page_hits[0] if page_hits else None
        preview_time = None
        if preview:
            try:
                preview_time = datetime.fromisoformat(preview.created_at_iso)
            except Exception:
                preview_time = None

        lines: list[str] = []
        for idx, hit in enumerate(page_hits, start=start + 1):
            link = _message_link(self.guild_id, hit.channel_id, hit.message_id)
            lines.append(f"**#{idx}** <#{hit.channel_id}> — `{hit.filename}` ([jump]({link}))")

        desc = "\n".join(lines) if lines else "No results on this page."
        embed = discord.Embed(
            title=f"Art Search: {self.target_display_name}",
            description=desc,
            color=discord.Color.blurple(),
            timestamp=preview_time or discord.utils.utcnow(),
        )

        if preview:
            embed.add_field(
                name="Preview",
                value=f"Result **#{start + 1}** • <#{preview.channel_id}>",
                inline=False,
            )
            embed.set_image(
                url=_scaled_image_url(
                    preview.attachment_url,
                    width=PREVIEW_IMAGE_WIDTH,
                    height=PREVIEW_IMAGE_HEIGHT,
                )
            )

        footer = f"Page {self.page_index + 1}/{total_pages} • {len(self.hits)} results"
        if self.truncated:
            footer += " • scan limit reached"
        elif not self._scan_complete():
            footer += " • more may exist (keeps scanning as you page)"
        embed.set_footer(text=footer)
        return embed

    async def bootstrap(self, guild: discord.Guild, *, desired_loaded: int, time_budget_seconds: float) -> None:
        await self._scan_until(
            guild,
            desired_loaded=desired_loaded,
            time_budget_seconds=time_budget_seconds,
            allow_sleep=False,
        )

    async def _scan_until(
        self,
        guild: discord.Guild,
        *,
        desired_loaded: int,
        time_budget_seconds: float,
        allow_sleep: bool,
    ) -> None:
        desired_loaded = max(RESULT_BATCH_SIZE, int(desired_loaded))
        start = time.monotonic()
        nohit_chunks = 0

        while (
            len(self.hits) < desired_loaded
            and not self.truncated
            and not self._scan_complete()
            and (time.monotonic() - start) < float(time_budget_seconds)
        ):
            # Global hard cap so a single view can't churn forever.
            if self.scanned_messages_total >= MAX_MESSAGES_SCANNED_TOTAL:
                self.truncated = True
                break

            # Pick next channel in round-robin.
            if not self.channel_ids:
                break
            attempts = 0
            channel_id = None
            while attempts < len(self.channel_ids):
                cid = self.channel_ids[self._channel_rr_index % len(self.channel_ids)]
                self._channel_rr_index += 1
                attempts += 1
                if cid in self.channel_done:
                    continue
                channel_id = cid
                break
            if channel_id is None:
                break

            channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                self.channel_done.add(channel_id)
                continue

            before_id = self.channel_before.get(channel_id)
            before_obj = discord.Object(id=int(before_id)) if before_id else None

            last_seen_id: Optional[int] = None
            found_in_chunk = 0
            try:
                async for msg in channel.history(limit=SCAN_CHUNK_MESSAGES, before=before_obj, oldest_first=False):
                    self.scanned_messages_total += 1
                    last_seen_id = int(msg.id)
                    if self.scanned_messages_total >= MAX_MESSAGES_SCANNED_TOTAL:
                        self.truncated = True
                        break
                    if msg.author.id != self.target_user_id:
                        continue
                    att = _pick_image_attachment(msg)
                    if not att:
                        continue
                    self.hits.append(
                        _ArtHit(
                            channel_id=int(msg.channel.id),
                            message_id=int(msg.id),
                            author_id=int(msg.author.id),
                            created_at_iso=msg.created_at.isoformat(),
                            attachment_url=att.url,
                            filename=att.filename or "image",
                        )
                    )
                    found_in_chunk += 1
                    if len(self.hits) >= desired_loaded:
                        break
            except Exception as e:
                logger.warning("Art search scan failed in channel %s: %s", channel_id, e)
                self.channel_done.add(channel_id)
                continue

            if last_seen_id is None:
                # End of history for this channel.
                self.channel_done.add(channel_id)
                continue

            self.channel_before[channel_id] = last_seen_id

            if found_in_chunk <= 0:
                nohit_chunks = min(nohit_chunks + 1, 6)
            else:
                nohit_chunks = 0

            # Rate-limit the deeper we go without finding images.
            if allow_sleep and nohit_chunks >= 3 and (time.monotonic() - start) < (time_budget_seconds - 0.5):
                delay = min(0.25 * (2 ** (nohit_chunks - 3)), 1.0)
                await asyncio.sleep(delay)

        # Keep newest-first ordering for paging.
        self.hits.sort(key=lambda h: h.created_at_iso, reverse=True)

    async def _ensure_batch_loaded(self, guild: discord.Guild, *, time_budget_seconds: float) -> None:
        needed_for_page = (self.page_index + 1) * RESULTS_PER_PAGE
        desired_loaded = max(
            RESULT_BATCH_SIZE,
            int(math.ceil(needed_for_page / RESULT_BATCH_SIZE) * RESULT_BATCH_SIZE),
        )
        if len(self.hits) >= desired_loaded or self.truncated or self._scan_complete():
            return
        await self._scan_until(
            guild,
            desired_loaded=desired_loaded,
            time_budget_seconds=time_budget_seconds,
            allow_sleep=True,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and int(interaction.user.id) == self.author_id:
            return True
        try:
            await interaction.response.send_message(
                "Only the person who ran the search can use these buttons.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page_index = max(0, self.page_index - 1)
        self._sync_buttons()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Forward ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Optimistically advance; if we can't load enough results, we'll clamp back.
        self.page_index += 1

        try:
            await interaction.response.defer()
        except Exception:
            return

        guild = interaction.guild
        if guild is not None:
            await self._ensure_batch_loaded(guild, time_budget_seconds=6.0)

        total_pages = self._page_count()
        if self.page_index * RESULTS_PER_PAGE >= len(self.hits):
            self.page_index = max(0, total_pages - 1)

        self._sync_buttons()
        embed = self.build_embed()
        try:
            if interaction.message is not None:
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass

