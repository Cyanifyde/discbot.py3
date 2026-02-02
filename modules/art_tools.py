"""
Art Tools module - creative tools for artists.

Provides color palettes, art prompts, dice rolls, and rate card generation.
"""
from __future__ import annotations

import logging
import random
from typing import List, Tuple

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled
from services.render_service import render_service

logger = logging.getLogger("discbot.art_tools")

MODULE_NAME = "art_tools"

# Art prompt components
SUBJECTS = [
    "dragon", "knight", "wizard", "fairy", "robot", "alien", "mermaid", "vampire",
    "werewolf", "phoenix", "unicorn", "griffin", "golem", "centaur", "elf", "dwarf",
    "cat", "wolf", "owl", "raven", "fox", "bear", "deer", "tiger", "eagle", "snake",
]

ACTIONS = [
    "flying", "sleeping", "fighting", "dancing", "reading", "meditating", "running",
    "jumping", "climbing", "swimming", "singing", "painting", "crafting", "brewing",
    "casting spells", "exploring", "guarding", "hunting", "resting", "training",
]

SETTINGS = [
    "in a forest", "on a mountain", "in a castle", "under the ocean", "in space",
    "in a city", "in ruins", "in a cave", "on a beach", "in the desert", "in the sky",
    "in a garden", "in a library", "in a laboratory", "in a temple", "in a dungeon",
    "at sunset", "at night", "in the rain", "in the snow", "in fog",
]

STYLES = [
    "realistic", "cartoon", "anime", "pixel art", "watercolor", "oil painting",
    "sketch", "digital art", "cel shaded", "impressionist", "minimalist", "gothic",
    "cyberpunk", "steampunk", "fantasy", "sci-fi", "noir", "comic book", "chibi",
]

MOODS = [
    "peaceful", "dramatic", "mysterious", "cheerful", "ominous", "whimsical",
    "melancholic", "energetic", "serene", "intense", "playful", "haunting",
]


def setup_art_tools() -> None:
    """Register help information for the art tools module."""
    help_system.register_module(
        name="Art Tools",
        description="Creative tools and generators for artists.",
        help_command="art help",
        commands=[
            ("palette [count]", "Generate random color palette (1-8 colors)"),
            ("palette hex <#color1> <#color2>...", "Create palette from hex codes"),
            ("palette harmony <#color>", "Generate complementary + analogous palette"),
            ("palette <method> <#color> [count]", "Methods: complementary, analogous, triadic, monochromatic"),
            ("prompt", "Generate random art prompt"),
            ("prompt custom <subject> <action> <setting>", "Create custom prompt"),
            ("artdice <sides>", "Roll art-themed dice"),
            ("artdice challenge", "Roll for art challenge"),
            ("ratecard [type]", "Generate rate card template"),
        ],
    )


async def handle_art_tools_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle art tools commands.

    Returns True if command was handled, False otherwise.
    """
    if not message.guild:
        return False

    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    content = message.content.strip()
    parts = content.split(maxsplit=2)

    if len(parts) < 1:
        return False

    command = parts[0].lower()

    # Route to handlers
    if command == "palette":
        if len(parts) >= 2 and parts[1].lower() == "help":
            await _handle_palette_help(message)
            return True
        await _handle_palette(message, parts)
        return True
    elif command == "prompt":
        await _handle_prompt(message, parts)
        return True
    elif command == "artdice":
        await _handle_artdice(message, parts)
        return True
    elif command == "ratecard":
        await _handle_ratecard(message, parts)
        return True

    return False


# ─── Color Palette Generator ──────────────────────────────────────────────────


def generate_random_color() -> str:
    """Generate a random hex color."""
    return f"#{random.randint(0, 0xFFFFFF):06x}"


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex color."""
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_complementary(hex_color: str) -> str:
    """Generate complementary color."""
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex(255 - r, 255 - g, 255 - b)


def generate_analogous(hex_color: str, count: int = 2) -> List[str]:
    """Generate analogous colors."""
    import colorsys

    r, g, b = hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    colors = []
    step = 30 / 360  # 30 degrees in hue

    for i in range(1, count + 1):
        new_h = (h + step * i) % 1.0
        new_r, new_g, new_b = colorsys.hsv_to_rgb(new_h, s, v)
        colors.append(rgb_to_hex(int(new_r * 255), int(new_g * 255), int(new_b * 255)))

    return colors


def generate_triadic(hex_color: str) -> List[str]:
    """Generate triadic color scheme."""
    import colorsys

    r, g, b = hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    colors = []
    for offset in [120/360, 240/360]:
        new_h = (h + offset) % 1.0
        new_r, new_g, new_b = colorsys.hsv_to_rgb(new_h, s, v)
        colors.append(rgb_to_hex(int(new_r * 255), int(new_g * 255), int(new_b * 255)))

    return colors


def generate_monochromatic(hex_color: str, count: int = 5) -> List[str]:
    """Generate monochromatic colors by adjusting value."""
    import colorsys

    r, g, b = hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    colors = []
    if count <= 1:
        return [hex_color]

    step = 0.6 / max(1, count - 1)
    for i in range(count):
        new_v = max(0.2, min(1.0, v - (step * i)))
        new_r, new_g, new_b = colorsys.hsv_to_rgb(h, s, new_v)
        colors.append(rgb_to_hex(int(new_r * 255), int(new_g * 255), int(new_b * 255)))

    return colors


def _build_palette_by_method(method: str, base_color: str, count: int) -> List[str]:
    method = method.lower()
    if method == "complementary":
        colors = [base_color, generate_complementary(base_color)]
        if count > 2:
            colors.extend(generate_analogous(base_color, count - 2))
        return colors[:count]
    if method == "analogous":
        colors = [base_color]
        colors.extend(generate_analogous(base_color, max(1, count - 1)))
        return colors[:count]
    if method == "triadic":
        colors = [base_color]
        colors.extend(generate_triadic(base_color))
        return colors[:count]
    if method == "monochromatic":
        return generate_monochromatic(base_color, count)
    return []


async def _handle_palette(message: discord.Message, parts: list[str]) -> None:
    """Handle palette generation."""
    method_label = "random"
    if len(parts) < 2:
        # Generate random palette
        count = 5
        colors = [generate_random_color() for _ in range(count)]
    elif parts[1].lower() == "hex":
        # Parse hex codes
        if len(parts) < 3:
            await message.reply(" Usage: `palette hex <#color1> <#color2>...`")
            return

        color_strs = parts[2].split()
        colors = [c for c in color_strs if c.startswith("#") and len(c) == 7]

        if not colors:
            await message.reply(" No valid hex colors provided")
            return
        method_label = "hex"
    elif parts[1].lower() == "harmony":
        # Generate harmonious palette
        if len(parts) < 3:
            await message.reply(" Usage: `palette harmony <#color>`")
            return

        base_color = parts[2].split()[0]
        if not base_color.startswith("#") or len(base_color) != 7:
            await message.reply(" Invalid hex color")
            return

        # Generate complementary + analogous
        colors = [base_color]
        colors.append(generate_complementary(base_color))
        colors.extend(generate_analogous(base_color, 2))
        method_label = "harmony"
    elif parts[1].lower() in {"complementary", "analogous", "triadic", "monochromatic"}:
        if len(parts) < 3:
            await message.reply(" Usage: `palette <method> <#color> [count]`")
            return
        method = parts[1].lower()
        args = parts[2].split()
        base_color = args[0]
        if not base_color.startswith("#") or len(base_color) != 7:
            await message.reply(" Invalid hex color")
            return
        count = 5
        if len(args) > 1:
            try:
                count = int(args[1])
                count = max(2, min(8, count))
            except ValueError:
                count = 5
        colors = _build_palette_by_method(method, base_color, count)
        if not colors:
            await message.reply(" Unknown palette method")
            return
        method_label = method
    else:
        # Random palette with specified count
        try:
            count = int(parts[1])
            count = max(1, min(8, count))  # Clamp to 1-8
        except ValueError:
            count = 5

        colors = [generate_random_color() for _ in range(count)]
        method_label = "random"

    try:
        image_bytes = await render_service.render_palette(colors, method_label, len(colors))
    except Exception as exc:
        logger.error("Palette render failed: %s", exc)
        await message.reply(" Failed to render palette.")
        return

    file = discord.File(fp=image_bytes, filename="palette.jpg")
    await message.reply(file=file)


async def _handle_palette_help(message: discord.Message) -> None:
    """Show help for palette commands."""
    await message.reply(
        "Palette commands:\n"
        "- `palette [count]` (random, 1-8)\n"
        "- `palette hex <#color1> <#color2>...`\n"
        "- `palette harmony <#color>`\n"
        "- `palette complementary <#color> [count]`\n"
        "- `palette analogous <#color> [count]`\n"
        "- `palette triadic <#color> [count]`\n"
        "- `palette monochromatic <#color> [count]`"
    )


# ─── Art Prompt Generator ─────────────────────────────────────────────────────


async def _handle_prompt(message: discord.Message, parts: list[str]) -> None:
    """Handle prompt generation."""
    if len(parts) >= 2 and parts[1].lower() == "custom":
        # Custom prompt
        if len(parts) < 3:
            await message.reply(" Usage: `prompt custom <subject> <action> <setting>`")
            return

        args = parts[2].split(maxsplit=2)
        if len(args) < 3:
            await message.reply(" Usage: `prompt custom <subject> <action> <setting>`")
            return

        subject, action, setting = args[0], args[1], args[2]
        style = random.choice(STYLES)
        mood = random.choice(MOODS)
    else:
        # Random prompt
        subject = random.choice(SUBJECTS)
        action = random.choice(ACTIONS)
        setting = random.choice(SETTINGS)
        style = random.choice(STYLES)
        mood = random.choice(MOODS)

    prompt = f"A {subject} {action} {setting}"

    embed = discord.Embed(
        title=" Art Prompt",
        description=f"**{prompt}**",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Style",
        value=style.title(),
        inline=True,
    )

    embed.add_field(
        name="Mood",
        value=mood.title(),
        inline=True,
    )

    # Add optional challenge
    if random.random() < 0.3:  # 30% chance
        challenges = [
            "Use only 3 colors",
            "Complete in under 1 hour",
            "Use only geometric shapes",
            "No sketching - direct inking",
            "Include a hidden symbol",
            "Use complementary colors only",
            "Draw from an unusual angle",
            "Include your signature creatively",
        ]
        challenge = random.choice(challenges)
        embed.add_field(
            name=" Challenge",
            value=challenge,
            inline=False,
        )

    await message.reply(embed=embed)


# ─── Art Dice ─────────────────────────────────────────────────────────────────


async def _handle_artdice(message: discord.Message, parts: list[str]) -> None:
    """Handle art dice rolls."""
    if len(parts) >= 2 and parts[1].lower() == "challenge":
        # Art challenge dice
        await _handle_art_challenge(message)
        return

    # Standard dice roll
    if len(parts) < 2:
        sides = 6
    else:
        try:
            sides = int(parts[1])
            sides = max(2, min(100, sides))  # Clamp to 2-100
        except ValueError:
            sides = 6

    result = random.randint(1, sides)

    # Art-themed interpretations
    interpretations = {
        1: "Sketch phase - rough ideas",
        2: "Lineart - clean and precise",
        3: "Base colors - flat fills",
        4: "Shading - add depth",
        5: "Highlights - make it pop",
        6: "Details - finishing touches",
    }

    embed = discord.Embed(
        title=" Art Dice",
        description=f"Rolling a d{sides}...",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Result",
        value=f"**{result}**",
        inline=True,
    )

    if result in interpretations:
        embed.add_field(
            name="Meaning",
            value=interpretations[result],
            inline=False,
        )

    await message.reply(embed=embed)


async def _handle_art_challenge(message: discord.Message) -> None:
    """Handle art challenge dice roll."""
    # Roll for multiple aspects
    time_limits = ["15 minutes", "30 minutes", "1 hour", "2 hours", "1 day", "1 week"]
    complexity = ["Simple", "Moderate", "Detailed", "Highly Detailed"]
    themes = ["Nature", "Urban", "Fantasy", "Sci-Fi", "Abstract", "Portrait", "Landscape"]
    restrictions = [
        "Monochrome only",
        "Limited palette (3 colors)",
        "No eraser/undo",
        "Non-dominant hand",
        "Use only basic shapes",
        "No reference allowed",
        "Include text/typography",
        "Must include water",
    ]

    time = random.choice(time_limits)
    level = random.choice(complexity)
    theme = random.choice(themes)
    restriction = random.choice(restrictions)

    embed = discord.Embed(
        title=" Art Challenge",
        description="Your challenge awaits!",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name=" Time Limit",
        value=time,
        inline=True,
    )

    embed.add_field(
        name=" Complexity",
        value=level,
        inline=True,
    )

    embed.add_field(
        name=" Theme",
        value=theme,
        inline=True,
    )

    embed.add_field(
        name=" Restriction",
        value=restriction,
        inline=False,
    )

    await message.reply(embed=embed)


# ─── Rate Card Generator ──────────────────────────────────────────────────────


async def _handle_ratecard(message: discord.Message, parts: list[str]) -> None:
    """Handle rate card generation."""
    card_type = "standard"
    if len(parts) >= 2:
        card_type = parts[1].lower()

    templates = {
        "standard": {
            "title": "Commission Rate Card",
            "items": [
                ("Sketch", "$X"),
                ("Lineart", "$Y"),
                ("Flat Colors", "$Z"),
                ("Full Render", "$W"),
            ],
        },
        "character": {
            "title": "Character Commission Rates",
            "items": [
                ("Headshot", "$X"),
                ("Bust", "$Y"),
                ("Half Body", "$Z"),
                ("Full Body", "$W"),
            ],
        },
        "background": {
            "title": "Background & Scene Rates",
            "items": [
                ("Simple Background", "$X"),
                ("Detailed Background", "$Y"),
                ("Full Scene", "$Z"),
            ],
        },
    }

    if card_type not in templates:
        card_type = "standard"

    template = templates[card_type]

    embed = discord.Embed(
        title=f" {template['title']} Template",
        description="Fill in your prices!",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )

    for item, price in template["items"]:
        embed.add_field(
            name=item,
            value=price,
            inline=True,
        )

    embed.add_field(
        name=" Tip",
        value="Replace the placeholder prices with your actual rates.\n"
              "Consider adding:\n"
              "• Additional character fees\n"
              "• Commercial use pricing\n"
              "• Rush order fees\n"
              "• Revision policies",
        inline=False,
    )

    embed.set_footer(text="Use 'ratecard standard', 'ratecard character', or 'ratecard background'")

    await message.reply(embed=embed)
