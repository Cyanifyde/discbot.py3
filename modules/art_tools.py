"""
Art Tools module - creative tools for artists.

Provides color palettes, art prompts, dice rolls, and rate card generation.
"""
from __future__ import annotations

import colorsys
import io
import logging
import math
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

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
            ("palette harmony [%h/%s/%l]", "Generate complementary + analogous palette (optional constraint)"),
            ("palette <method> [%h/%s/%l] [count]", "Methods: complementary, analogous, triadic, monochromatic"),
            ("palette [count] %l10", "Constrain random palettes (only one of %h/%s/%l)"),
            ("palette rule60 [%h]", "60-30-10 design rule palette (UI/design)"),
            ("palette shading <#hex>", "Cel shading palette (shadows → highlights)"),
            ("palette warmcool [%h]", "Warm + cool colors for temperature contrast"),
            ("palette limited [3-5]", "Small harmonious palette (Zorn-style)"),
            ("palette skintone [#hex]", "Portrait palette with realistic skin lighting"),
            ("prompt", "Generate random art prompt"),
            ("prompt custom <subject> <action> <setting>", "Create custom prompt"),
            ("artdice <sides>", "Roll art-themed dice"),
            ("artdice challenge", "Roll for art challenge"),
        ],
    )

    help_system.register_module(
        name="Palette",
        description="Generate color palettes and reroll with locks.",
        help_command="palette help",
        commands=[
            ("palette [count]", "Random palette (1–8) with improved color generation"),
            ("palette [count] %h120", "Fix hue (0–360) (only one of %h/%s/%l)"),
            ("palette [count] %s40", "Fix saturation percent (0–100)"),
            ("palette [count] %l10", "Fix lightness percent (0–100)"),
            ("palette hex <#c1> <#c2> ...", "Palette from hex codes"),
            ("palette harmony [%h/%s/%l]", "Complementary + analogous (optional constraint)"),
            ("palette complementary [%h/%s/%l] [count]", "Complementary palette (optional constraint)"),
            ("palette analogous [%h/%s/%l] [count]", "Analogous palette (optional constraint)"),
            ("palette triadic [%h/%s/%l] [count]", "Triadic palette (optional constraint)"),
            ("palette monochromatic [%h/%s/%l] [count]", "Monochromatic palette (optional constraint)"),
            ("palette rule60 [%h]", "60-30-10 design rule (UI: dominant/secondary/accent)"),
            ("palette shading <#hex>", "Cel shading with desaturated shadows"),
            ("palette warmcool [%h]", "Temperature contrast (warm subjects + cool shadows)"),
            ("palette limited [3-5]", "Small harmonious palette for color mixing"),
            ("palette skintone [#hex]", "Portrait palette (warm light = cool shadows)"),
            ("Reroll button", "Regenerate unlocked colors"),
            ("Lock buttons", "Toggle lock per color"),
            ("Send to DM button", "DM yourself the current palette"),
            ("Temperature indicators", "🔥 Warm / ❄️ Cool / ⚖️ Neutral shown on all colors"),
            ("Value indicators", "✨ Highlight / 🎨 Midtone / 🌑 Shadow for shading guidance"),
        ],
        group="Art Tools",
        hidden=True,
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
    if command == "art":
        if len(parts) >= 2 and parts[1].lower() == "help":
            await _handle_art_help(message)
            return True
        return False
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

    return False


async def _handle_art_help(message: discord.Message) -> None:
    """Handle 'art help' command."""
    embed = help_system.get_module_help("Art Tools")
    if embed:
        await message.reply(embed=embed)
    else:
        await message.reply(" Help information not available.")


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


def _hex_to_hls(hex_color: str) -> Optional[Tuple[float, float, float]]:
    try:
        r, g, b = hex_to_rgb(hex_color)
        rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
        return colorsys.rgb_to_hls(rf, gf, bf)
    except Exception:
        return None


def _hls_to_hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, min(1.0, max(0.0, l)), min(1.0, max(0.0, s)))
    return rgb_to_hex(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _normalize_hex(token: str) -> Optional[str]:
    token = (token or "").strip()
    if token.startswith("#") and len(token) == 7:
        try:
            int(token[1:], 16)
        except Exception:
            return None
        return token.lower()
    return None


def _parse_hsl_color(token: str) -> Optional[str]:
    """
    Parse HSL into a hex color.
    Supported forms:
    - hsl(120, 50%, 40%)
    - hsl:120,50,40
    - hsl(120 50% 40%)
    """
    t = (token or "").strip().lower()
    if not t:
        return None

    if t.startswith("hsl(") and t.endswith(")"):
        inner = t[4:-1].strip()
    elif t.startswith("hsl:"):
        inner = t[4:].strip()
    else:
        return None

    inner = inner.replace(",", " ")
    parts = [p for p in inner.split() if p]
    if len(parts) != 3:
        return None

    try:
        h = float(parts[0])
        s_raw = parts[1].rstrip("%")
        l_raw = parts[2].rstrip("%")
        s = float(s_raw)
        l = float(l_raw)
    except Exception:
        return None

    h = (h % 360.0) / 360.0
    s = max(0.0, min(100.0, s)) / 100.0
    l = max(0.0, min(100.0, l)) / 100.0
    return _hls_to_hex(h, l, s)


def _parse_color_tokens(tokens: list[str], start: int = 0) -> Tuple[Optional[str], int]:
    """
    Parse a color starting at tokens[start].
    Returns (hex_color, consumed_token_count).
    Supports:
    - #rrggbb
    - hsl(...) / hsl:...
    - hsl <h> <s%> <l%>
    """
    if start < 0 or start >= len(tokens):
        return None, 0

    t0 = (tokens[start] or "").strip()
    hex0 = _normalize_hex(t0)
    if hex0:
        return hex0, 1

    hsl0 = _parse_hsl_color(t0)
    if hsl0:
        return hsl0, 1

    if t0.lower() == "hsl" and (start + 3) < len(tokens):
        h = (tokens[start + 1] or "").strip()
        s = (tokens[start + 2] or "").strip().rstrip("%")
        l = (tokens[start + 3] or "").strip().rstrip("%")
        try:
            hh = float(h)
            ss = float(s)
            ll = float(l)
        except Exception:
            return None, 0
        hh = (hh % 360.0) / 360.0
        ss = max(0.0, min(100.0, ss)) / 100.0
        ll = max(0.0, min(100.0, ll)) / 100.0
        return _hls_to_hex(hh, ll, ss), 4

    return None, 0


def _parse_hsl_constraint(tokens: list[str]) -> Tuple[Optional[Tuple[str, int]], Optional[str]]:
    """
    Parse tokens like %h120, %s40, %l10. Only one may be used.
    Returns (constraint, error_message).
    """
    found: Optional[Tuple[str, int]] = None
    for t in tokens:
        if not t or not t.startswith("%") or len(t) < 3:
            continue
        comp = t[1:2].lower()
        if comp not in {"h", "s", "l"}:
            continue
        raw = t[2:].strip()
        if raw.startswith("="):
            raw = raw[1:].strip()
        raw = raw.rstrip("%")
        if not raw or not raw.lstrip("+-").isdigit():
            continue
        val = int(raw)
        if comp == "h":
            val = max(0, min(360, val))
        else:
            val = max(0, min(100, val))
        if found is not None and found[0] != comp:
            return None, "Only one of `%h`, `%s`, or `%l` can be used at a time."
        found = (comp, val)
    return found, None


def _random_color_with_constraint(
    constraint: Optional[Tuple[str, int]],
    *,
    use_smart_ranges: bool = True,
    perceptual_adjust: bool = True,
) -> str:
    """
    Generate random color with optional constraint and smart ranges.

    Args:
        constraint: Optional (component, value) tuple for %h/%s/%l
        use_smart_ranges: Use aesthetic lightness/saturation distributions (default True)
        perceptual_adjust: Apply perceptual lightness corrections (default True)

    Returns:
        Hex color string
    """
    if not constraint:
        if use_smart_ranges:
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
        else:
            l_min, l_max = (0.25, 0.8)
            s_min, s_max = (0.35, 0.95)

        h = random.random()
        l = random.uniform(l_min, l_max)
        s = random.uniform(s_min, s_max)

        if perceptual_adjust:
            l = _perceptual_lightness_adjust(h, l)

        return _hls_to_hex(h, l, s)

    comp, val = constraint
    if comp == "h":
        h = val / 360.0
        if use_smart_ranges:
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
            l = random.uniform(l_min, l_max)
            s = random.uniform(s_min, s_max)
        else:
            l = random.uniform(0.25, 0.8)
            s = random.uniform(0.35, 0.95)

        if perceptual_adjust:
            l = _perceptual_lightness_adjust(h, l)

        return _hls_to_hex(h, l, s)

    if comp == "s":
        h = random.random()
        s = val / 100.0
        if use_smart_ranges:
            l_min, l_max = _smart_lightness_range()
            l = random.uniform(l_min, l_max)
        else:
            l = random.uniform(0.25, 0.8)

        if perceptual_adjust:
            l = _perceptual_lightness_adjust(h, l)

        return _hls_to_hex(h, l, s)

    # comp == "l"
    h = random.random()
    l = val / 100.0
    if use_smart_ranges:
        s_min, s_max = _smart_saturation_range()
        s = random.uniform(s_min, s_max)
    else:
        s = random.uniform(0.35, 0.95)

    # No perceptual adjustment when lightness is constrained
    return _hls_to_hex(h, l, s)


def _vary_color_from_seed(seed_hex: str, constraint: Optional[Tuple[str, int]]) -> str:
    seed = _hex_to_hls(seed_hex)
    if seed is None:
        return _random_color_with_constraint(constraint)
    h, l, s = seed
    h = (h + random.uniform(-0.12, 0.12)) % 1.0
    l = min(1.0, max(0.0, l + random.uniform(-0.15, 0.15)))
    s = min(1.0, max(0.0, s + random.uniform(-0.2, 0.2)))
    if constraint:
        comp, val = constraint
        if comp == "h":
            h = val / 360.0
        elif comp == "s":
            s = val / 100.0
        else:
            l = val / 100.0
    return _hls_to_hex(h, l, s)


def _blend_locked_base(locked_colors: list[str]) -> Optional[str]:
    """Blend locked colors into a single base (circular mean hue + average lightness/saturation)."""
    hls_list: list[Tuple[float, float, float]] = []
    for c in locked_colors:
        hls = _hex_to_hls(c)
        if hls is not None:
            hls_list.append(hls)
    if not hls_list:
        return None

    # Circular mean for hue (on [0,1)).
    x = 0.0
    y = 0.0
    l_sum = 0.0
    s_sum = 0.0
    for h, l, s in hls_list:
        ang = h * 2.0 * math.pi
        x += math.cos(ang)
        y += math.sin(ang)
        l_sum += l
        s_sum += s

    avg_l = l_sum / len(hls_list)
    avg_s = s_sum / len(hls_list)
    avg_h = 0.0
    if abs(x) > 1e-9 or abs(y) > 1e-9:
        avg_h = (math.atan2(y, x) / (2.0 * math.pi)) % 1.0
    return _hls_to_hex(avg_h, avg_l, avg_s)


def _rotate_hue(hex_color: str, degrees: float) -> str:
    """Rotate hue by degrees, preserving saturation/lightness."""
    hls = _hex_to_hls(hex_color)
    if hls is None:
        return hex_color
    h, l, s = hls
    h = (h + (degrees / 360.0)) % 1.0
    return _hls_to_hex(h, l, s)


def _jitter_sl(base_h: float, base_l: float, base_s: float, *, max_dl: float, max_ds: float) -> Tuple[float, float]:
    """
    Small random variations in saturation/lightness while keeping hue fixed.
    Helps rerolls change without breaking the hue-relationship rules.
    """
    l = min(0.95, max(0.05, base_l + random.uniform(-max_dl, max_dl)))
    s = min(0.98, max(0.08, base_s + random.uniform(-max_ds, max_ds)))
    return l, s


def _smart_lightness_range() -> Tuple[float, float]:
    """
    Generate lightness range with aesthetic biases for cohesive palettes.

    Returns (min_l, max_l) tuple.

    Distribution:
    - 40% chance: vibrant range (0.45-0.75) - rich, saturated colors
    - 30% chance: bright range (0.60-0.90) - light, airy palettes
    - 20% chance: deep range (0.20-0.55) - dramatic, moody tones
    - 10% chance: full spectrum (0.15-0.85) - maximum variety
    """
    roll = random.random()
    if roll < 0.4:
        return (0.45, 0.75)  # Vibrant
    elif roll < 0.7:
        return (0.60, 0.90)  # Bright
    elif roll < 0.9:
        return (0.20, 0.55)  # Deep
    else:
        return (0.15, 0.85)  # Full spectrum


def _smart_saturation_range() -> Tuple[float, float]:
    """
    Generate saturation range with aesthetic biases.

    Distribution:
    - 50% chance: vivid range (0.50-0.95) - bold, punchy colors
    - 30% chance: balanced range (0.30-0.70) - versatile, professional
    - 15% chance: muted range (0.10-0.40) - subtle, sophisticated
    - 5% chance: desaturated (0.05-0.25) - neutral, minimalist
    """
    roll = random.random()
    if roll < 0.5:
        return (0.50, 0.95)  # Vivid
    elif roll < 0.8:
        return (0.30, 0.70)  # Balanced
    elif roll < 0.95:
        return (0.10, 0.40)  # Muted
    else:
        return (0.05, 0.25)  # Desaturated


def _perceptual_lightness_adjust(h: float, l: float) -> float:
    """
    Adjust HSL lightness for perceptual uniformity.

    Yellow appears brightest, blue darkest at same L value.
    This function applies hue-dependent corrections so colors
    appear equally bright across the hue spectrum.

    Based on research: yellows/greens need L reduction,
    blues/purples need L boost for perceptual equivalence.
    """
    # Convert hue to degrees
    h_deg = h * 360.0

    # Define correction curve based on hue
    if 40 <= h_deg < 80:  # Yellow-green range
        correction = -0.05  # Reduce lightness
    elif 80 <= h_deg < 140:  # Green range
        correction = -0.03
    elif 140 <= h_deg < 200:  # Cyan range
        correction = 0.0
    elif 200 <= h_deg < 280:  # Blue-purple range
        correction = 0.08  # Increase lightness
    elif 280 <= h_deg < 340:  # Magenta range
        correction = 0.05
    else:  # Red-orange range
        correction = 0.0

    return min(0.98, max(0.02, l + correction))


def _calculate_contrast_ratio(hex1: str, hex2: str) -> float:
    """
    Calculate WCAG contrast ratio between two colors.

    Returns ratio (1.0 to 21.0).
    Formula: (L1 + 0.05) / (L2 + 0.05) where L1 > L2
    """
    def relative_luminance(hex_color: str) -> float:
        hls = _hex_to_hls(hex_color)
        if hls is None:
            return 0.5
        h, l, s = hls
        # Convert to RGB
        r, g, b = colorsys.hls_to_rgb(h, l, s)

        # Apply sRGB gamma correction
        def gamma(c):
            if c <= 0.03928:
                return c / 12.92
            return ((c + 0.055) / 1.055) ** 2.4

        r_lin = gamma(r)
        g_lin = gamma(g)
        b_lin = gamma(b)

        # Calculate relative luminance
        return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin

    L1 = relative_luminance(hex1)
    L2 = relative_luminance(hex2)

    lighter = max(L1, L2)
    darker = min(L1, L2)

    return (lighter + 0.05) / (darker + 0.05)


def _is_colorblind_safe(colors: list[str]) -> bool:
    """
    Check if palette avoids problematic red-green combinations.

    Returns True if palette is colorblind-friendly.
    """
    for i, c1 in enumerate(colors):
        for c2 in colors[i+1:]:
            hls1 = _hex_to_hls(c1)
            hls2 = _hex_to_hls(c2)

            if hls1 is None or hls2 is None:
                continue

            h1_deg = hls1[0] * 360
            h2_deg = hls2[0] * 360

            # Check for problematic red-green pairs
            is_red1 = (h1_deg < 30 or h1_deg > 330)
            is_green1 = (80 <= h1_deg < 160)
            is_red2 = (h2_deg < 30 or h2_deg > 330)
            is_green2 = (80 <= h2_deg < 160)

            # If both colors are similar in lightness and one is red, one is green
            l_diff = abs(hls1[1] - hls2[1])
            if l_diff < 0.2 and ((is_red1 and is_green2) or (is_green1 and is_red2)):
                return False

    return True


def _ensure_accessible_contrast(colors: list[str], min_ratio: float = 3.0) -> list[str]:
    """
    Adjust palette to meet minimum contrast requirements.

    Ensures adjacent colors have sufficient contrast.
    """
    adjusted = colors.copy()

    for i in range(len(adjusted) - 1):
        ratio = _calculate_contrast_ratio(adjusted[i], adjusted[i+1])

        if ratio < min_ratio:
            # Adjust lightness of second color
            hls = _hex_to_hls(adjusted[i+1])
            if hls is not None:
                h, l, s = hls
                # Increase or decrease lightness to improve contrast
                hls_prev = _hex_to_hls(adjusted[i])
                if hls_prev and hls_prev[1] > 0.5:
                    l = max(0.1, l - 0.3)  # Darken
                else:
                    l = min(0.9, l + 0.3)  # Lighten

                adjusted[i+1] = _hls_to_hex(h, l, s)

    return adjusted


def _get_color_temperature(hex_color: str) -> str:
    """Return warm/cool/neutral indicator based on hue."""
    hls = _hex_to_hls(hex_color)
    if hls is None:
        return "⚖️ Neutral"

    h_deg = hls[0] * 360.0

    # Warm: reds, oranges, yellows (330-360, 0-90)
    if h_deg >= 330 or h_deg < 90:
        return "🔥 Warm"
    # Cool: cyans, blues, purples (150-270)
    elif 150 <= h_deg < 270:
        return "❄️ Cool"
    # Neutral: greens, yellow-greens (90-150, 270-330)
    else:
        return "⚖️ Neutral"


def _get_value_role(lightness: float) -> str:
    """Suggest usage based on lightness value for shading."""
    if lightness < 0.35:
        return "🌑 Shadow"
    elif lightness < 0.65:
        return "🎨 Midtone"
    else:
        return "✨ Highlight"


def _scheme_color(
    base_hls: Tuple[float, float, float],
    *,
    hue_offset_deg: float,
    sl_jitter: bool,
    max_dl: float,
    max_ds: float,
) -> str:
    h, l, s = base_hls
    h = (h + (hue_offset_deg / 360.0)) % 1.0
    if sl_jitter:
        l, s = _jitter_sl(h, l, s, max_dl=max_dl, max_ds=max_ds)
    return _hls_to_hex(h, l, s)


def _generate_by_method(method_label: str, base_color: str, count: int) -> list[str]:
    method = (method_label or "random").lower()
    base_color = base_color.lower()
    count = max(1, min(8, int(count)))

    base_hls = _hex_to_hls(base_color)
    if base_hls is None:
        base_hls = _hex_to_hls(generate_random_color())
    if base_hls is None:
        return [base_color] * count

    if method == "harmony":
        # Color-theory "harmony": base + complementary + analogous around base.
        colors = [_scheme_color(base_hls, hue_offset_deg=0.0, sl_jitter=False, max_dl=0.0, max_ds=0.0)]
        if count >= 2:
            colors.append(_scheme_color(base_hls, hue_offset_deg=180.0, sl_jitter=True, max_dl=0.06, max_ds=0.08))
        if count >= 3:
            colors.append(_scheme_color(base_hls, hue_offset_deg=30.0, sl_jitter=True, max_dl=0.05, max_ds=0.08))
        if count >= 4:
            colors.append(_scheme_color(base_hls, hue_offset_deg=-30.0, sl_jitter=True, max_dl=0.05, max_ds=0.08))
        # Fill remainder with additional analogous steps (±60, ±90...) keeping relationships.
        step = 30.0
        k = 2
        while len(colors) < count:
            colors.append(_scheme_color(base_hls, hue_offset_deg=step * k, sl_jitter=True, max_dl=0.05, max_ds=0.08))
            if len(colors) >= count:
                break
            colors.append(_scheme_color(base_hls, hue_offset_deg=-step * k, sl_jitter=True, max_dl=0.05, max_ds=0.08))
            k += 1
        return colors[:count]

    if method in {"complementary", "analogous", "triadic", "monochromatic"}:
        if method == "complementary":
            colors = [
                _scheme_color(base_hls, hue_offset_deg=0.0, sl_jitter=False, max_dl=0.0, max_ds=0.0),
                _scheme_color(base_hls, hue_offset_deg=180.0, sl_jitter=True, max_dl=0.06, max_ds=0.08),
            ]
            while len(colors) < count:
                # Alternate analogous around the base to fill.
                offset = 30.0 * (len(colors) - 1)
                colors.append(_scheme_color(base_hls, hue_offset_deg=offset, sl_jitter=True, max_dl=0.05, max_ds=0.08))
            return colors[:count]

        if method == "analogous":
            colors = [_scheme_color(base_hls, hue_offset_deg=0.0, sl_jitter=False, max_dl=0.0, max_ds=0.0)]
            offsets = [30.0, -30.0, 60.0, -60.0, 90.0, -90.0, 120.0, -120.0]
            for off in offsets:
                if len(colors) >= count:
                    break
                colors.append(_scheme_color(base_hls, hue_offset_deg=off, sl_jitter=True, max_dl=0.05, max_ds=0.08))
            return colors[:count]

        if method == "triadic":
            colors = [
                _scheme_color(base_hls, hue_offset_deg=0.0, sl_jitter=False, max_dl=0.0, max_ds=0.0),
                _scheme_color(base_hls, hue_offset_deg=120.0, sl_jitter=True, max_dl=0.06, max_ds=0.08),
                _scheme_color(base_hls, hue_offset_deg=240.0, sl_jitter=True, max_dl=0.06, max_ds=0.08),
            ]
            while len(colors) < count:
                colors.append(_scheme_color(base_hls, hue_offset_deg=30.0 * (len(colors) - 2), sl_jitter=True, max_dl=0.04, max_ds=0.06))
            return colors[:count]

        # monochromatic: fixed hue, varied lightness, keep saturation similar.
        h, l, s = base_hls
        # Create a monotonic range around base lightness.
        spread = 0.35
        lo = max(0.05, l - spread)
        hi = min(0.95, l + spread)
        if count == 1:
            return [_hls_to_hex(h, l, s)]
        vals = [lo + (hi - lo) * (i / (count - 1)) for i in range(count)]
        # Slight reroll variation: shift the whole ramp a little.
        ramp_shift = random.uniform(-0.06, 0.06)
        vals = [min(0.95, max(0.05, v + ramp_shift)) for v in vals]
        colors = [_hls_to_hex(h, v, min(0.98, max(0.08, s + random.uniform(-0.06, 0.06)))) for v in vals]
        return colors[:count]

    # random/hex fallback
    colors = [base_color]
    while len(colors) < count:
        colors.append(_vary_color_from_seed(colors[-1], None))
    return colors[:count]


def _generate_60_30_10_palette(base_color: str) -> Tuple[list[str], list[int]]:
    """
    Generate a 60-30-10 rule palette (UI/design focused).

    The 60-30-10 rule:
    - 60% Dominant color - main backgrounds, large areas
    - 30% Secondary color - supporting elements
    - 10% Accent color - calls-to-action, highlights

    Returns:
        (colors, proportions) where proportions = [60, 30, 10]
    """
    base_hls = _hex_to_hls(base_color)
    if base_hls is None:
        base_hls = _hex_to_hls(generate_random_color())
    if base_hls is None:
        return ([base_color] * 3, [60, 30, 10])

    h, l, s = base_hls

    # Dominant color (60%): Base, slightly desaturated for versatility
    dominant_s = max(0.15, min(0.65, s * 0.85))
    dominant_l = min(0.95, max(0.25, l))
    dominant = _hls_to_hex(h, _perceptual_lightness_adjust(h, dominant_l), dominant_s)

    # Secondary color (30%): Analogous or split-complementary
    if random.random() < 0.6:
        # Analogous: 30-45 degrees offset
        secondary_offset = random.choice([30, 45, -30, -45])
    else:
        # Split-complementary: 150-180 degrees
        secondary_offset = random.choice([150, 165, 180, -150, -165])

    secondary_h = (h + (secondary_offset / 360.0)) % 1.0
    secondary_l = min(0.95, max(0.20, l + random.uniform(-0.15, 0.15)))
    secondary_s = max(0.25, min(0.85, s * random.uniform(0.9, 1.1)))
    secondary = _hls_to_hex(secondary_h, _perceptual_lightness_adjust(secondary_h, secondary_l), secondary_s)

    # Accent color (10%): High contrast complementary
    accent_offset = 180  # True complementary
    accent_h = (h + (accent_offset / 360.0)) % 1.0
    accent_s = max(0.70, min(0.98, s * 1.3))

    # Contrasting lightness
    if dominant_l > 0.5:
        accent_l = max(0.25, min(0.45, l - 0.3))  # Dark accent
    else:
        accent_l = max(0.60, min(0.85, l + 0.3))  # Light accent

    accent = _hls_to_hex(accent_h, _perceptual_lightness_adjust(accent_h, accent_l), accent_s)

    colors = [dominant, secondary, accent]
    proportions = [60, 30, 10]

    return (colors, proportions)


def _generate_shading_palette(base_color: str) -> list[str]:
    """
    Generate shading palette from base color (shadows to highlights).

    Based on cel shading research: light changes both value AND saturation.
    Shadows are desaturated, highlights maintain or slightly decrease saturation.
    """
    hls = _hex_to_hls(base_color)
    if hls is None:
        hls = _hex_to_hls(generate_random_color())
    if hls is None:
        return [base_color] * 5

    h, l, s = hls

    # Generate 5 values: deep shadow, shadow, base, highlight, bright highlight
    offsets = [-0.30, -0.15, 0.0, 0.15, 0.30]
    # Saturation multipliers: shadows desaturated, highlights maintained
    sat_multipliers = [0.70, 0.85, 1.0, 0.95, 0.90]
    colors = []

    for offset, sat_mult in zip(offsets, sat_multipliers):
        new_l = min(0.95, max(0.05, l + offset))
        # Apply saturation change based on research
        new_s = min(0.98, max(0.05, s * sat_mult))
        # Apply perceptual adjustment
        new_l = _perceptual_lightness_adjust(h, new_l)
        colors.append(_hls_to_hex(h, new_l, new_s))

    return colors


def _generate_warmcool_palette(base_color: str) -> list[str]:
    """
    Generate palette with warm/cool temperature contrast.

    Based on color temperature principle: warm light = cool shadows, cool light = warm shadows.
    Creates natural depth through temperature contrast (warm advances, cool recedes).
    """
    hls = _hex_to_hls(base_color)
    if hls is None:
        hls = _hex_to_hls(generate_random_color())
    if hls is None:
        return [base_color] * 6

    h, l, s = hls
    h_deg = h * 360.0

    # Determine if base is warm or cool
    is_warm = (h_deg >= 330 or h_deg < 90)

    colors = []

    if is_warm:
        # 3 warm colors (base ± 20-30°)
        for offset in [0, -25, 20]:
            new_h = (h + (offset / 360.0)) % 1.0
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
            l_val = random.uniform(l_min, l_max)
            s_val = random.uniform(s_min, s_max)
            colors.append(_hls_to_hex(new_h, _perceptual_lightness_adjust(new_h, l_val), s_val))

        # 2-3 cool complements (base + 180° ± 30°)
        for offset in [180, 160, 200]:
            new_h = (h + (offset / 360.0)) % 1.0
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
            l_val = random.uniform(l_min, l_max)
            s_val = random.uniform(s_min, s_max) * 0.9  # Slightly less saturated
            colors.append(_hls_to_hex(new_h, _perceptual_lightness_adjust(new_h, l_val), s_val))
    else:
        # 3 cool colors (base ± 20-30°)
        for offset in [0, -25, 20]:
            new_h = (h + (offset / 360.0)) % 1.0
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
            l_val = random.uniform(l_min, l_max)
            s_val = random.uniform(s_min, s_max)
            colors.append(_hls_to_hex(new_h, _perceptual_lightness_adjust(new_h, l_val), s_val))

        # 2-3 warm complements (base + 180° ± 30°)
        for offset in [180, 160, 200]:
            new_h = (h + (offset / 360.0)) % 1.0
            l_min, l_max = _smart_lightness_range()
            s_min, s_max = _smart_saturation_range()
            l_val = random.uniform(l_min, l_max)
            s_val = random.uniform(s_min, s_max) * 0.9  # Slightly less saturated
            colors.append(_hls_to_hex(new_h, _perceptual_lightness_adjust(new_h, l_val), s_val))

    return colors[:6]


def _generate_limited_palette(count: int = 4) -> list[str]:
    """
    Generate small harmonious palette for mixing (traditional artist approach).

    Based on classic limited palettes like Zorn palette.
    Forces color mixing and creates natural harmony.
    """
    count = max(3, min(5, count))

    # Choose random base hue
    base_h = random.random()

    colors = []

    # 1. Base color (medium value, high saturation)
    colors.append(_hls_to_hex(base_h, random.uniform(0.45, 0.65), random.uniform(0.60, 0.85)))

    # 2. Analogous color (±30°)
    analog_h = (base_h + (30 / 360.0)) % 1.0
    colors.append(_hls_to_hex(analog_h, random.uniform(0.50, 0.70), random.uniform(0.55, 0.80)))

    # 3. Complementary accent (180°)
    comp_h = (base_h + 0.5) % 1.0
    colors.append(_hls_to_hex(comp_h, random.uniform(0.40, 0.60), random.uniform(0.65, 0.90)))

    # 4. Light neutral (if count >= 4)
    if count >= 4:
        colors.append(_hls_to_hex(random.random(), random.uniform(0.75, 0.90), random.uniform(0.05, 0.15)))

    # 5. Dark neutral (if count >= 5)
    if count >= 5:
        colors.append(_hls_to_hex(random.random(), random.uniform(0.15, 0.30), random.uniform(0.10, 0.25)))

    return colors[:count]


def _generate_skintone_palette(base_color: str) -> list[str]:
    """
    Generate skin tone palette for portraits.

    Uses warm light = cool shadow principle (traditional portrait lighting).
    Mimics outdoor/sunlight conditions where skin is warm, shadows are cool (blue/purple).
    """
    hls = _hex_to_hls(base_color)
    if hls is None:
        # Generate random warm skin tone
        h = random.uniform(15, 35) / 360.0
        l = random.uniform(0.50, 0.70)
        s = random.uniform(0.30, 0.50)
        hls = (h, l, s)

    h, l, s = hls

    colors = []

    # 1. Base skin (warm)
    colors.append(_hls_to_hex(h, l, s))

    # 2. Shadow (cooler - shift toward blue/purple for warm light scenario)
    # Warm light = cool shadows (shift hue toward 240° blue range)
    shadow_h = (h + 180/360.0) % 1.0  # Shift toward complementary (cooler)
    # Adjust to ensure it's in cool range (blue/purple: 200-280°)
    shadow_h_deg = shadow_h * 360.0
    if shadow_h_deg < 200 or shadow_h_deg > 280:
        shadow_h = 240 / 360.0  # Default to blue
    shadow_l = max(0.10, l - 0.20)
    shadow_s = max(0.15, s * 0.8)  # Reduce saturation in shadows
    colors.append(_hls_to_hex(shadow_h, shadow_l, shadow_s))

    # 3. Deep shadow (even cooler, more desaturated)
    deep_l = max(0.08, l - 0.35)
    deep_s = max(0.10, s * 0.6)
    colors.append(_hls_to_hex(shadow_h, deep_l, deep_s))

    # 4. Highlight (warmer - shift toward yellow/orange)
    highlight_h = (h + 15/360.0) % 1.0  # Shift warmer (toward yellow)
    highlight_l = min(0.95, l + 0.15)
    highlight_s = max(0.15, s * 0.9)
    colors.append(_hls_to_hex(highlight_h, highlight_l, highlight_s))

    # 5. Bright highlight (very warm, desaturated)
    bright_l = min(0.98, l + 0.30)
    bright_s = max(0.05, s * 0.4)
    colors.append(_hls_to_hex(highlight_h, bright_l, bright_s))

    # 6. Blush (reddish - subsurface scattering effect)
    blush_h = random.uniform(0, 15) / 360.0
    blush_l = min(0.90, l + 0.05)
    blush_s = min(0.70, s * 1.4)
    colors.append(_hls_to_hex(blush_h, blush_l, blush_s))

    return colors


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


@dataclass
class _PaletteState:
    method_label: str
    colors: list[str]
    constraint: Optional[Tuple[str, int]] = None
    proportions: Optional[list[int]] = None  # For 60-30-10 rule and other weighted palettes


class _PaletteLockButton(discord.ui.Button):
    def __init__(self, view: "_PaletteView", index: int) -> None:
        self._palette_view = view
        self.index = int(index)
        super().__init__(
            label=str(self.index + 1),
            style=discord.ButtonStyle.secondary,
            row=0 if self.index < 5 else 1,
        )
        self._sync()

    def _sync(self) -> None:
        locked = self.index in self._palette_view.locked_indices
        self.emoji = "🔒" if locked else "🔓"
        self.style = discord.ButtonStyle.primary if locked else discord.ButtonStyle.secondary

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.index in self._palette_view.locked_indices:
            self._palette_view.locked_indices.remove(self.index)
        else:
            self._palette_view.locked_indices.add(self.index)
        await self._palette_view.update(interaction)


class _PaletteView(discord.ui.View):
    def __init__(self, *, author_id: int, state: _PaletteState) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.state = state
        self.locked_indices: set[int] = set()
        self.message: Optional[discord.Message] = None

        self.lock_buttons: list[_PaletteLockButton] = []
        for i in range(len(self.state.colors)):
            btn = _PaletteLockButton(self, i)
            self.lock_buttons.append(btn)
            self.add_item(btn)
        self._sync_controls()

    def _sync_controls(self) -> None:
        for b in self.lock_buttons:
            b._sync()
        try:
            self.reroll.disabled = self.state.method_label == "hex" or len(self.locked_indices) >= len(self.state.colors)
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and int(interaction.user.id) == self.author_id:
            return True
        try:
            await interaction.response.send_message(
                "Only the person who generated this palette can use these controls.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def build_files_and_embeds(self) -> Tuple[list[discord.File], list[discord.Embed]]:
        files: list[discord.File] = []
        embeds: list[discord.Embed] = []

        colors = self.state.colors
        for idx, c in enumerate(colors, start=1):
            hex_upper = (c or "").upper()
            try:
                color_int = int((c or "").lstrip("#"), 16)
                embed_color = discord.Color(color_int)
            except Exception:
                embed_color = discord.Color.blurple()

            patch_name: Optional[str] = None
            try:
                patch_bytes = await render_service.render_color_patch(c, size=96)
                patch_name = f"swatch_{idx}.png"
                files.append(discord.File(fp=io.BytesIO(patch_bytes), filename=patch_name))
            except Exception:
                patch_name = None

            title = hex_upper or f"Color {idx}"
            if (idx - 1) in self.locked_indices:
                title += " (locked)"

            # Add art-focused indicators (temperature & value)
            hls = _hex_to_hls(c)
            art_info = ""
            if hls:
                h, l, s = hls
                temperature = _get_color_temperature(c)
                value_role = _get_value_role(l)
                art_info = f"\n{temperature} • {value_role}"

            description = f"Color {idx}/{len(colors)}{art_info}"

            e = discord.Embed(
                title=title,
                description=description,
                color=embed_color,
            )
            if patch_name:
                e.set_thumbnail(url=f"attachment://{patch_name}")
            embeds.append(e)

        summary_lines: list[str] = []
        if self.locked_indices:
            locked_list = ", ".join(str(i + 1) for i in sorted(self.locked_indices))
            summary_lines.append(f"Locked: {locked_list}")
        if self.state.constraint:
            comp, val = self.state.constraint
            summary_lines.append(f"Constraint: `%{comp}{val}`")
        summary_lines.append(" ".join(f"`{c.upper()}`" for c in colors))
        summary_lines.append("Use the 🔓/🔒 buttons to lock colors, then **Reroll** to regenerate the rest.")
        summary_lines.append("Use `%h/%s/%l` to constrain generated palettes.")

        summary = discord.Embed(
            title=f"Palette ({self.state.method_label}) — {len(colors)} color(s)",
            description="\n".join(summary_lines),
            color=discord.Color.dark_teal(),
        )
        embeds.append(summary)

        return files, embeds

    async def update(self, interaction: discord.Interaction) -> None:
        self._sync_controls()
        files, embeds = await self.build_files_and_embeds()
        try:
            await interaction.response.edit_message(embeds=embeds, attachments=files, view=self)
        except Exception:
            try:
                await interaction.edit_original_response(embeds=embeds, attachments=files, view=self)
            except Exception:
                pass

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary, row=1)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.state.method_label == "hex":
            return
        colors = self.state.colors
        locked = self.locked_indices
        locked_colors = [colors[i] for i in sorted(locked) if 0 <= i < len(colors)]

        # Special handling for 60-30-10 rule palette
        if self.state.method_label == "60-30-10 Rule":
            # If dominant (index 0) is locked, use it as base
            if 0 in locked:
                base = colors[0]
            elif locked_colors:
                base = locked_colors[0]
            else:
                base = _random_color_with_constraint(self.state.constraint)

            new_colors, proportions = _generate_60_30_10_palette(base)

            # Apply locked colors back
            for i in range(len(colors)):
                if i not in locked:
                    colors[i] = new_colors[i]

            self.state.proportions = proportions
        else:
            # Determine a base to generate "in symphony" with.
            if locked_colors:
                base = _blend_locked_base(locked_colors) or locked_colors[0]
            else:
                base = _random_color_with_constraint(self.state.constraint)

            generated = _generate_by_method(self.state.method_label, base, len(colors))

            # Apply locked colors back onto their slots, regenerate the rest deterministically
            # from the sequence (so color N is based on color N-1 / base).
            for i in range(len(colors)):
                if i in locked:
                    continue
                colors[i] = generated[i]

        # Enforce constraint after generation (if present).
        if self.state.constraint:
            comp, val = self.state.constraint
            for i in range(len(colors)):
                if i in locked:
                    continue
                hls = _hex_to_hls(colors[i])
                if hls is None:
                    continue
                h, l, s = hls
                if comp == "h":
                    h = val / 360.0
                elif comp == "s":
                    s = val / 100.0
                else:
                    l = val / 100.0
                colors[i] = _hls_to_hex(h, l, s)

        await self.update(interaction)

    @discord.ui.button(label="Send to DM", style=discord.ButtonStyle.secondary, row=2)
    async def send_dm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return

        user = interaction.user
        if user is None:
            return

        files, embeds = await self.build_files_and_embeds()
        try:
            dm = await user.create_dm()
            await dm.send(
                embeds=embeds,
                files=files,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            try:
                await interaction.followup.send("Sent to your DMs.", ephemeral=True)
            except Exception:
                pass
        except discord.Forbidden:
            try:
                await interaction.followup.send("I can't DM you (your DMs might be closed).", ephemeral=True)
            except Exception:
                pass
        except Exception:
            try:
                await interaction.followup.send("Failed to send DM.", ephemeral=True)
            except Exception:
                pass


async def _handle_palette(message: discord.Message, parts: list[str]) -> None:
    """Handle palette generation."""
    raw_tokens = (message.content or "").strip().split()
    args = raw_tokens[1:] if len(raw_tokens) > 1 else []

    constraint, constraint_err = _parse_hsl_constraint(args)
    if constraint_err:
        await message.reply(f" {constraint_err}", allowed_mentions=discord.AllowedMentions.none())
        return
    args_no_constraint = [
        a for a in args if not (a.startswith("%") and len(a) >= 3 and a[1:2].lower() in {"h", "s", "l"})
    ]

    def apply_constraint(hex_color: str) -> str:
        if not constraint:
            return hex_color
        hls = _hex_to_hls(hex_color)
        if hls is None:
            return hex_color
        h, l, s = hls
        comp, val = constraint
        if comp == "h":
            h = val / 360.0
        elif comp == "s":
            s = val / 100.0
        else:
            l = val / 100.0
        return _hls_to_hex(h, l, s)

    method_label = "random"
    colors: list[str] = []

    if not args_no_constraint:
        count = 5
        colors = [_random_color_with_constraint(constraint)]
        while len(colors) < count:
            colors.append(_vary_color_from_seed(colors[-1], constraint))
    elif args_no_constraint[0].lower() == "hex":
        if len(args_no_constraint) < 2:
            await message.reply(" Usage: `palette hex <#color1> <#color2>...`")
            return
        raw = [t for t in args_no_constraint[1:] if t.strip()]
        parsed = [_normalize_hex(t) for t in raw]
        colors = [c for c in parsed if c]
        if not colors:
            await message.reply(" No valid hex colors provided")
            return
        if len(colors) > 8:
            colors = colors[:8]
        method_label = "hex"
    elif args_no_constraint[0].lower() == "harmony":
        if len(args_no_constraint) > 1:
            await message.reply(
                " `palette harmony` only accepts constraints (e.g. `%h120`).",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        base_color = _random_color_with_constraint(constraint)
        colors = [base_color, generate_complementary(base_color)]
        colors.extend(generate_analogous(base_color, 2))
        method_label = "harmony"
    elif args_no_constraint[0].lower() in {"complementary", "analogous", "triadic", "monochromatic"}:
        method = args_no_constraint[0].lower()
        base_color: Optional[str] = None
        count = 5

        rest = args_no_constraint[1:]
        if rest:
            if rest[0].isdigit():
                count = int(rest[0])
            else:
                await message.reply(
                    f" Usage: `palette {method} [%h/%s/%l] [count]`",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        count = max(2, min(8, count))
        if base_color is None:
            base_color = _random_color_with_constraint(constraint)
        colors = _build_palette_by_method(method, base_color, count)
        if not colors:
            await message.reply(" Unknown palette method")
            return
        method_label = method
    elif args_no_constraint[0].lower() in {"rule60", "60-30-10"}:
        # 60-30-10 design rule palette
        base_color = _random_color_with_constraint(constraint)
        colors_with_proportions = _generate_60_30_10_palette(base_color)
        colors, proportions = colors_with_proportions
        colors = [apply_constraint(c) for c in colors]
        method_label = "60-30-10 Rule"
        state = _PaletteState(method_label=method_label, colors=[c.lower() for c in colors], constraint=constraint, proportions=proportions)
        view = _PaletteView(author_id=message.author.id, state=state)
        files, embeds = await view.build_files_and_embeds()
        view.message = await message.reply(files=files, embeds=embeds, view=view)
        return
    elif args_no_constraint[0].lower() == "shading":
        # Shading palette (cel shading / value ramp)
        if len(args_no_constraint) < 2:
            await message.reply(" Usage: `palette shading <#hexcolor>`")
            return
        base_hex = args_no_constraint[1]
        if not base_hex.startswith("#"):
            await message.reply(" Please provide a hex color starting with #")
            return
        colors = _generate_shading_palette(base_hex)
        colors = [apply_constraint(c) for c in colors]
        method_label = "Shading"
    elif args_no_constraint[0].lower() in {"warmcool", "warm-cool"}:
        # Warm/cool split palette
        base_color = _random_color_with_constraint(constraint)
        colors = _generate_warmcool_palette(base_color)
        colors = [apply_constraint(c) for c in colors]
        method_label = "Warm/Cool Split"
    elif args_no_constraint[0].lower() == "limited":
        # Limited palette (3-5 colors, traditional artist style)
        count = 4  # default
        if len(args_no_constraint) >= 2 and args_no_constraint[1].isdigit():
            count = max(3, min(5, int(args_no_constraint[1])))
        colors = _generate_limited_palette(count)
        colors = [apply_constraint(c) for c in colors]
        method_label = "Limited Palette"
    elif args_no_constraint[0].lower() in {"skintone", "skin"}:
        # Skin tone palette for portraits
        base_hex = None
        if len(args_no_constraint) >= 2 and args_no_constraint[1].startswith("#"):
            base_hex = args_no_constraint[1]
        else:
            # Generate random warm skin tone
            h_val = random.randint(15, 35)
            base_hex = _random_color_with_constraint(("h", h_val))
        colors = _generate_skintone_palette(base_hex)
        colors = [apply_constraint(c) for c in colors]
        method_label = "Skin Tone"
    else:
        # Random palette with specified count
        if args_no_constraint[0].isdigit():
            count = max(1, min(8, int(args_no_constraint[0])))
        else:
            count = 5
        colors = [_random_color_with_constraint(constraint)]
        while len(colors) < count:
            colors.append(_vary_color_from_seed(colors[-1], constraint))
        method_label = "random"

    colors = [apply_constraint(c) for c in colors]

    state = _PaletteState(method_label=method_label, colors=[c.lower() for c in colors], constraint=constraint)
    view = _PaletteView(author_id=message.author.id, state=state)
    files, embeds = await view.build_files_and_embeds()
    sent = await message.reply(
        embeds=embeds,
        files=files,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    view.message = sent


async def _handle_palette_help(message: discord.Message) -> None:
    """Show help for palette commands."""
    embed = help_system.get_module_help("Palette")
    if embed:
        await message.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return
    await message.reply(" Usage: `palette [count]` (try `palette help`)", allowed_mentions=discord.AllowedMentions.none())


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
