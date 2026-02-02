"""
Commission reviews module - user reviews with a simple dispute workflow.

Commands:
- review @artist <1-5> <text> [commission_id]
- review list @artist [page]
- review dispute <review_id> <reason>
- review resolve <review_id> <upheld|removed|amended> [note or new_text]
"""
from __future__ import annotations

import logging

import discord

from core.commission_review_storage import CommissionReviewStore
from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled

logger = logging.getLogger("discbot.commission_reviews")

MODULE_NAME = "commissionreviews"
REVIEWS_PER_PAGE = 5


def setup_commission_reviews() -> None:
    help_system.register_module(
        name="Commission Reviews",
        description="Leave reviews for artists and handle disputes via moderator resolution.",
        help_command="review help",
        commands=[
            ("review @artist <1-5> <text> [commission_id]", "Leave a review"),
            ("review list @artist [page]", "List reviews for an artist"),
            ("review dispute <review_id> <reason>", "Dispute a review (artist/client)"),
            ("review resolve <review_id> <upheld|removed|amended> [note]", "Resolve a dispute (mod only)"),
            ("review help", "Show this help message"),
        ],
    )


async def handle_commission_reviews_command(message: discord.Message, bot: discord.Client) -> bool:
    if not message.guild:
        return False

    content = (message.content or "").strip()
    if not content:
        return False

    if not content.lower().startswith("review"):
        return False

    parts = content.split(maxsplit=3)
    if len(parts) == 1:
        await _cmd_help(message)
        return True

    sub = parts[1].lower().strip(",.!?")
    if sub == "help":
        await _cmd_help(message)
        return True

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.channel.send(
            "Commission Reviews module is disabled in this server.\n"
            "An administrator can enable it with `modules enable commissionreviews`",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if sub == "list":
        await _cmd_list(message, content)
        return True

    if sub == "dispute":
        await _cmd_dispute(message, content)
        return True

    if sub == "resolve":
        await _cmd_resolve(message, content)
        return True

    # Default: create review
    await _cmd_create(message, content)
    return True


async def _cmd_help(message: discord.Message) -> None:
    embed = help_system.get_module_help("Commission Reviews")
    if embed:
        await message.channel.send(embed=embed)
    else:
        await message.channel.send(" Usage: `review @artist <1-5> <text>`")


async def _cmd_create(message: discord.Message, content: str) -> None:
    if not message.guild:
        return

    if not message.mentions:
        await message.channel.send(" Usage: `review @artist <1-5> <text> [commission_id]`")
        return

    parts = content.split(maxsplit=4)
    if len(parts) < 4:
        await message.channel.send(" Usage: `review @artist <1-5> <text> [commission_id]`")
        return

    artist = message.mentions[0]

    try:
        rating = int(parts[2])
    except Exception:
        await message.channel.send(" Rating must be an integer 1-5.")
        return

    if rating < 1 or rating > 5:
        await message.channel.send(" Rating must be between 1 and 5.")
        return

    text = parts[3].strip()
    commission_id = parts[4].strip() if len(parts) >= 5 else None
    if not text:
        await message.channel.send(" Review text cannot be empty.")
        return

    store = CommissionReviewStore(message.guild.id)
    await store.initialize()
    rid = await store.create_review(
        artist_id=artist.id,
        client_id=message.author.id,
        rating=rating,
        text=text,
        commission_id=commission_id,
    )

    await message.channel.send(
        f"Review created for {artist.mention}.\n"
        f"Review ID: `{rid}`",
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _cmd_list(message: discord.Message, content: str) -> None:
    if not message.guild:
        return

    parts = content.split()
    if len(parts) < 3 or not message.mentions:
        await message.channel.send(" Usage: `review list @artist [page]`")
        return

    artist = message.mentions[0]
    page = 1
    if len(parts) >= 4 and parts[3].isdigit():
        page = max(1, int(parts[3]))

    store = CommissionReviewStore(message.guild.id)
    await store.initialize()
    reviews = await store.list_reviews_for_artist(artist.id)
    if not reviews:
        await message.channel.send(f"No reviews found for {artist.mention}.", allowed_mentions=discord.AllowedMentions.none())
        return

    start = (page - 1) * REVIEWS_PER_PAGE
    end = start + REVIEWS_PER_PAGE
    page_reviews = reviews[start:end]
    if not page_reviews:
        await message.channel.send("No more reviews.", allowed_mentions=discord.AllowedMentions.none())
        return

    embed = discord.Embed(
        title=f"Reviews: {artist.display_name}",
        description=f"Page {page} (showing {len(page_reviews)} of {len(reviews)})",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    for r in page_reviews:
        status = r.get("status", "active")
        rating = r.get("rating", "?")
        rid = r.get("id", "")
        text = r.get("amended_text") or r.get("text") or ""
        if len(text) > 200:
            text = text[:197] + "..."
        embed.add_field(
            name=f"{rid} | {rating}/5 | {status}",
            value=text or "(no text)",
            inline=False,
        )

    await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _cmd_dispute(message: discord.Message, content: str) -> None:
    if not message.guild:
        return

    parts = content.split(maxsplit=3)
    if len(parts) < 4:
        await message.channel.send(" Usage: `review dispute <review_id> <reason>`")
        return

    review_id = parts[2].strip()
    reason = parts[3].strip()
    if not reason:
        await message.channel.send(" Please include a reason.")
        return

    store = CommissionReviewStore(message.guild.id)
    await store.initialize()
    review = await store.get_review(review_id)
    if not review:
        await message.channel.send(f" Review `{review_id}` not found.")
        return

    actor_id = message.author.id
    is_party = actor_id in {review.get("artist_id"), review.get("client_id")}
    can_mod = isinstance(message.author, discord.Member) and await can_use_command(message.author, "review dispute")
    if not is_party and not can_mod:
        await message.channel.send(" You don't have permission to dispute this review.")
        return

    ok = await store.dispute(review_id, actor_id=actor_id, reason=reason)
    if ok:
        await message.channel.send(f" Review `{review_id}` marked as disputed.")
    else:
        await message.channel.send(" Could not dispute that review.")


async def _cmd_resolve(message: discord.Message, content: str) -> None:
    if not message.guild:
        return
    if not isinstance(message.author, discord.Member) or not await can_use_command(message.author, "review resolve"):
        await message.channel.send(" You don't have permission to resolve reviews.")
        return

    parts = content.split(maxsplit=4)
    if len(parts) < 4:
        await message.channel.send(" Usage: `review resolve <review_id> <upheld|removed|amended> [note]`")
        return

    review_id = parts[2].strip()
    outcome = parts[3].strip().lower()
    note = parts[4].strip() if len(parts) >= 5 else None

    store = CommissionReviewStore(message.guild.id)
    await store.initialize()

    amended_text = note if outcome == "amended" else None
    ok = await store.resolve(
        review_id,
        moderator_id=message.author.id,
        outcome=outcome,
        note=None if outcome == "amended" else note,
        amended_text=amended_text,
    )
    if ok:
        await message.channel.send(f" Review `{review_id}` resolved: `{outcome}`.")
    else:
        await message.channel.send(" Failed to resolve review (check ID/outcome).")

