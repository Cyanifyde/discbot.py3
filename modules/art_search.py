"""
Art Search -- channel-restricted image search for a user's posted art.

Searches configured channels for every non-GIF image attachment posted by a
target user, returning paginated results with thumbnails and jump links.
Paging forward lazily scans deeper history with no hard cap.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
import sys
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import discord

from core.art_search_storage import ArtSearchStore, parse_channel_id
from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled

logger = logging.getLogger("discbot.art_search")

# ── constants ────────────────────────────────────────────────────────────────

MODULE_NAME = "artsearch"
RESULTS_PER_PAGE = 5
SCAN_CHUNK = 100          # messages fetched per API call
BOOTSTRAP_BUDGET = 5.0    # seconds for the initial scan
PAGE_BUDGET = 8.0         # seconds when loading more results on page-forward
EMBED_THUMB_PX = 100      # thumbnail max dimension in px
VIEW_TIMEOUT = 300        # seconds before buttons are disabled

# ── data model ───────────────────────────────────────────────────────────────


_dataclass_kwargs = {"slots": True} if sys.version_info >= (3, 10) else {}


@dataclass(**_dataclass_kwargs)
class _Hit:
    """One image attachment found during a scan."""
    channel_id: int
    message_id: int
    author_id: int
    timestamp: float        # POSIX UTC -- used for sorting
    attachment_url: str
    filename: str


# ── module registration ─────────────────────────────────────────────────────


def setup_art_search() -> None:
    help_system.register_module(
        name="Art Search",
        description="Search for images posted by a user in approved channels.",
        help_command="artsearch help",
        commands=[
            ("art search @user [page]", "Search a user's images (filters GIFs)"),
            ("art channels", "List configured search channels"),
            ("art channels add <#channel | id>", "Add a channel (mod only)"),
            ("art channels remove <#channel | id>", "Remove a channel (mod only)"),
            ("artsearch help", "Show this help message"),
        ],
    )


# ── command dispatcher ──────────────────────────────────────────────────────


async def handle_art_search_command(
    message: discord.Message, bot: discord.Client,
) -> bool:
    """Return ``True`` if the message was handled by this module."""
    if not message.guild:
        return False

    parts = (message.content or "").split()
    if len(parts) < 2:
        return False

    root = parts[0].lower().rstrip(",.!?")
    sub = parts[1].lower().rstrip(",.!?")

    if root not in {"artsearch", "art"}:
        return False

    if sub == "help":
        if root == "artsearch":
            await _cmd_help(message)
            return True
        return False  # `art help` belongs to Art Tools

    if sub == "channels":
        await _cmd_channels(message, parts[2:])
        return True

    if sub == "search":
        if not await is_module_enabled(message.guild.id, MODULE_NAME):
            await message.channel.send(
                "Art Search is disabled in this server.\n"
                "An admin can enable it with `modules enable artsearch`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        await _cmd_search(message, parts[2:])
        return True

    return False


# ── help ─────────────────────────────────────────────────────────────────────


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_help("Art Search")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send("Usage: `art search @user [page]`")


# ── channel management ──────────────────────────────────────────────────────


async def _cmd_channels(message: discord.Message, args: list[str]) -> None:
    if not message.guild:
        return

    store = ArtSearchStore(message.guild.id)
    await store.initialize()

    # List
    if not args:
        channels = await store.list_channels()
        if not channels:
            await message.channel.send(
                "No art-search channels configured.\n"
                "Add one with: `art channels add #channel`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            body = "\n".join(f"- <#{cid}> (`{cid}`)" for cid in channels)
            await message.channel.send(
                f"**Art search channels:**\n{body}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return

    action = args[0].lower()
    if action not in {"add", "remove"}:
        await message.channel.send("Usage: `art channels add|remove <#channel | id>`")
        return

    # Permission gate
    if not isinstance(message.author, discord.Member) or not await can_use_command(
        message.author, "art channels",
    ):
        await message.channel.send("You don't have permission to manage art-search channels.")
        return

    # Parse the channel identifier -- accept #mentions or raw IDs
    raw = args[1] if len(args) >= 2 else ""
    # Also check if the message has channel_mentions from Discord
    if message.channel_mentions and len(args) >= 2:
        cid = message.channel_mentions[0].id
    else:
        parsed = parse_channel_id(raw)
        if parsed is None:
            await message.channel.send("Usage: `art channels add|remove <#channel | id>`")
            return
        cid = parsed

    if action == "add":
        added = await store.add_channel(cid)
        if added:
            await message.channel.send(f"Added <#{cid}> to art-search channels.")
        else:
            await message.channel.send(f"<#{cid}> is already configured.")
    else:
        removed = await store.remove_channel(cid)
        if removed:
            await message.channel.send(f"Removed <#{cid}> from art-search channels.")
        else:
            await message.channel.send(f"<#{cid}> was not configured.")


# ── search command ───────────────────────────────────────────────────────────


async def _cmd_search(message: discord.Message, args: list[str]) -> None:
    guild = message.guild
    if not guild:
        return

    if not args or not message.mentions:
        await message.channel.send("Usage: `art search @user [page]`")
        return

    target = message.mentions[0]
    page = 1
    for a in args:
        if a.isdigit():
            page = max(1, int(a))
            break

    store = ArtSearchStore(guild.id)
    await store.initialize()
    channel_ids = await store.list_channels()
    if not channel_ids:
        await message.channel.send(
            "No art-search channels configured.\n"
            "Add one with: `art channels add #channel`",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    # Build the interactive view and send the initial "Searching" embed.
    view = _SearchView(
        guild_id=guild.id,
        author_id=message.author.id,
        target=target,
        channel_ids=channel_ids,
        start_page=page - 1,
    )

    embed = discord.Embed(
        title=f"Art Search: {target.display_name}",
        description="Searching...",
        color=discord.Color.dark_grey(),
    )
    sent = await message.channel.send(
        embed=embed, view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    view.message = sent

    # Bootstrap: scan enough history to fill the requested page.
    desired = max(RESULTS_PER_PAGE, page * RESULTS_PER_PAGE)
    t0 = time.monotonic()
    async with message.channel.typing():
        await view.scan(guild, desired=desired, budget=BOOTSTRAP_BUDGET)
    logger.info(
        "ArtSearch bootstrap %.2fs, %d hits, %d messages scanned",
        time.monotonic() - t0, len(view.hits), view.scanned,
    )

    # Show results (or "no results").
    await view.refresh(sent)


# ── helpers ──────────────────────────────────────────────────────────────────


def _image_attachments(msg: discord.Message) -> list[discord.Attachment]:
    """Return all non-GIF image attachments from *msg*."""
    out: list[discord.Attachment] = []
    for att in msg.attachments:
        ct = (att.content_type or "").lower()
        fn = (att.filename or "").lower()
        is_image = ct.startswith("image/") or fn.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"),
        )
        if not is_image:
            continue
        if fn.endswith(".gif") or ct == "image/gif":
            continue
        out.append(att)
    return out


def _jump_url(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _thumb_url(url: str, size: int = EMBED_THUMB_PX) -> str:
    """Rewrite a Discord CDN URL to the media proxy with size parameters."""
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if host.startswith("cdn.discordapp."):
            host = "media.discordapp.net"
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        q["width"] = q["height"] = str(size)
        return urlunparse(p._replace(netloc=host, query=urlencode(q)))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}width={size}&height={size}"


def _trunc(text: str, n: int) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: max(1, n - 1)] + "..."


# ── interactive view ─────────────────────────────────────────────────────────


class _SearchView(discord.ui.View):
    """Paginated art-search results with lazy-loading on page-forward."""

    def __init__(
        self,
        *,
        guild_id: int,
        author_id: int,
        target: discord.User | discord.Member,
        channel_ids: list[int],
        start_page: int = 0,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)

        self.guild_id = guild_id
        self.author_id = author_id
        self.target_name = target.display_name
        self.target_id = target.id
        self.channel_ids = list(channel_ids)
        self.page = max(0, start_page)
        self.message: Optional[discord.Message] = None

        # Scan state -- sequential per-channel cursors.
        self.hits: list[_Hit] = []
        self.scanned = 0           # total messages inspected
        self._cursors: dict[int, Optional[int]] = {c: None for c in channel_ids}
        self._exhausted: set[int] = set()  # channels fully scanned
        self._chan_idx = 0          # next channel to scan
        self._loading = False

        self._sync_buttons()

    # -- properties -----------------------------------------------------------

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.hits) / RESULTS_PER_PAGE))

    @property
    def all_exhausted(self) -> bool:
        return len(self._exhausted) >= len(self.channel_ids)

    # -- scanning -------------------------------------------------------------

    async def scan(
        self,
        guild: discord.Guild,
        *,
        desired: int,
        budget: float,
        progress_message: Optional[discord.Message] = None,
    ) -> None:
        """Scan channel history until *desired* hits are collected or *budget* seconds elapse."""
        if self.all_exhausted or len(self.hits) >= desired:
            return

        deadline = time.monotonic() + budget
        last_progress = 0.0

        async def _maybe_update_progress(extra: str = "") -> None:
            nonlocal last_progress
            if not progress_message:
                return
            now = time.monotonic()
            if now - last_progress < 2.0:
                return
            last_progress = now
            desc = f"Scanning more history...\nChecked **{self.scanned:,}** messages so far."
            if extra:
                desc += f"\n{extra}"
            e = discord.Embed(
                title=f"Art Search: {self.target_name}",
                description=desc,
                color=discord.Color.dark_grey(),
            )
            try:
                await progress_message.edit(embeds=[e], view=self)
            except Exception:
                pass

        while len(self.hits) < desired and not self.all_exhausted:
            if time.monotonic() >= deadline:
                break

            # Pick the next un-exhausted channel.
            ch_id = self._next_channel()
            if ch_id is None:
                break

            channel = guild.get_channel(ch_id) or guild.get_thread(ch_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                self._exhausted.add(ch_id)
                continue

            cursor = self._cursors.get(ch_id)
            before = discord.Object(id=cursor) if cursor else None
            last_id: Optional[int] = None
            found = 0

            try:
                async for msg in channel.history(
                    limit=SCAN_CHUNK, before=before, oldest_first=False,
                ):
                    self.scanned += 1
                    last_id = msg.id
                    await _maybe_update_progress(f"Currently scanning <#{ch_id}>")

                    if msg.author.id != self.target_id:
                        continue

                    for att in _image_attachments(msg):
                        self.hits.append(_Hit(
                            channel_id=msg.channel.id,
                            message_id=msg.id,
                            author_id=msg.author.id,
                            timestamp=msg.created_at.timestamp(),
                            attachment_url=att.url,
                            filename=att.filename or "image",
                        ))
                        found += 1

                    if len(self.hits) >= desired:
                        break

            except discord.HTTPException as exc:
                status = getattr(exc, "status", 0)
                if status == 429:
                    retry = max(0.5, min(float(getattr(exc, "retry_after", 1) or 1), 60))
                    logger.warning("ArtSearch rate-limited, sleeping %.1fs", retry)
                    await _maybe_update_progress(f"Rate limited. Retrying in **{retry:.1f}s**…")
                    await asyncio.sleep(retry)
                    # Don't count this against the budget.
                    deadline += retry
                    continue
                logger.warning("ArtSearch HTTP error in %s: %s", ch_id, exc)
                self._exhausted.add(ch_id)
                continue
            except Exception as exc:
                logger.warning("ArtSearch error in %s: %s", ch_id, exc)
                self._exhausted.add(ch_id)
                continue

            if last_id is None:
                # Channel history returned nothing -- fully scanned.
                self._exhausted.add(ch_id)
            else:
                self._cursors[ch_id] = last_id

        # Keep newest-first.
        self.hits.sort(key=lambda h: h.timestamp, reverse=True)

    def _next_channel(self) -> Optional[int]:
        """Return the next channel that hasn't been fully exhausted."""
        n = len(self.channel_ids)
        for _ in range(n):
            cid = self.channel_ids[self._chan_idx % n]
            self._chan_idx += 1
            if cid not in self._exhausted:
                return cid
        return None

    # -- display --------------------------------------------------------------

    def _build_embeds(self) -> list[discord.Embed]:
        """Build the list of embeds for the current page."""
        total = len(self.hits)
        pages = self.page_count
        self.page = max(0, min(self.page, pages - 1))

        start = self.page * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        page_hits = self.hits[start:end]

        embeds: list[discord.Embed] = []
        for idx, hit in enumerate(page_hits, start=start + 1):
            link = _jump_url(self.guild_id, hit.channel_id, hit.message_id)
            ts_discord = f"<t:{int(hit.timestamp)}:R>"
            fname = _trunc(hit.filename.replace("`", "'"), 60) or "image"

            e = discord.Embed(
                title=f"#{idx} -- {fname}",
                description=f"<#{hit.channel_id}> {ts_discord} -- [jump]({link})",
                color=discord.Color.blurple(),
            )
            e.set_thumbnail(url=_thumb_url(hit.attachment_url))
            embeds.append(e)

        # Summary embed at the bottom.
        if page_hits:
            desc = f"Results **{start + 1}--{start + len(page_hits)}** of **{total}** found."
        else:
            desc = "No results on this page."

        if not self.all_exhausted:
            desc += "\nMore may exist -- press **Next** to scan deeper."

        summary = discord.Embed(
            title=f"Art Search: {self.target_name}",
            description=desc,
            color=discord.Color.blurple(),
        )
        footer = f"Page {self.page + 1}/{pages}"
        if self.all_exhausted:
            footer += f" -- scan complete ({self.scanned:,} messages checked)"
        else:
            footer += f" -- {self.scanned:,} messages checked so far"
        summary.set_footer(text=footer)
        embeds.append(summary)
        return embeds

    def _sync_buttons(self) -> None:
        self.btn_prev.disabled = self._loading or self.page <= 0
        at_end = self.page >= self.page_count - 1
        self.btn_next.disabled = self._loading or (at_end and self.all_exhausted)

    async def refresh(self, msg: Optional[discord.Message] = None) -> None:
        """Edit *msg* (or ``self.message``) with current page content."""
        msg = msg or self.message
        if not msg:
            return
        self._loading = False
        self._sync_buttons()

        if not self.hits:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            try:
                await msg.edit(
                    content=f"No images found for <@{self.target_id}> in the configured channels.",
                    embed=None, embeds=[], view=self,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass
            return

        try:
            await msg.edit(content=None, embeds=self._build_embeds(), view=self)
        except Exception:
            pass

    # -- interaction checks ---------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        try:
            await interaction.response.send_message(
                "Only the person who ran the search can use these buttons.",
                ephemeral=True,
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

    # -- buttons --------------------------------------------------------------

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def btn_prev(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.defer()
        await self.refresh(interaction.message)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def btn_next(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        self.page += 1
        await interaction.response.defer()

        # If we need more results, scan deeper.
        needed = (self.page + 1) * RESULTS_PER_PAGE
        if len(self.hits) < needed and not self.all_exhausted:
            guild = interaction.guild
            if guild:
                self._loading = True
                self._sync_buttons()
                # Show a loading state briefly.
                loading_embed = discord.Embed(
                    title=f"Art Search: {self.target_name}",
                    description="Scanning more history...",
                    color=discord.Color.dark_grey(),
                )
                try:
                    if interaction.message:
                        await interaction.message.edit(
                            embeds=[loading_embed], view=self,
                        )
                except Exception:
                    pass

                await self.scan(
                    guild,
                    desired=needed,
                    budget=PAGE_BUDGET,
                    progress_message=interaction.message,
                )

        # Clamp if we couldn't load enough.
        if self.page >= self.page_count:
            self.page = max(0, self.page_count - 1)

        await self.refresh(interaction.message)
