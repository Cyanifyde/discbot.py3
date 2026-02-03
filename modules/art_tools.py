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
            ("palette [count]", "Random palette (1–8)"),
            ("palette [count] %h120", "Fix hue (0–360) (only one of %h/%s/%l)"),
            ("palette [count] %s40", "Fix saturation percent (0–100)"),
            ("palette [count] %l10", "Fix lightness percent (0–100)"),
            ("palette hex <#c1> <#c2> ...", "Palette from hex codes"),
            ("palette harmony [%h/%s/%l]", "Complementary + analogous (optional constraint)"),
            ("palette complementary [%h/%s/%l] [count]", "Complementary palette (optional constraint)"),
            ("palette analogous [%h/%s/%l] [count]", "Analogous palette (optional constraint)"),
            ("palette triadic [%h/%s/%l] [count]", "Triadic palette (optional constraint)"),
            ("palette monochromatic [%h/%s/%l] [count]", "Monochromatic palette (optional constraint)"),
            ("Reroll button", "Regenerate unlocked colors"),
            ("Lock buttons", "Toggle lock per color"),
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


def _random_color_with_constraint(constraint: Optional[Tuple[str, int]]) -> str:
    if not constraint:
        return generate_random_color()
    comp, val = constraint
    if comp == "h":
        h = val / 360.0
        l = random.uniform(0.25, 0.8)
        s = random.uniform(0.35, 0.95)
        return _hls_to_hex(h, l, s)
    if comp == "s":
        h = random.random()
        l = random.uniform(0.25, 0.8)
        s = val / 100.0
        return _hls_to_hex(h, l, s)
    h = random.random()
    l = val / 100.0
    s = random.uniform(0.35, 0.95)
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

            e = discord.Embed(
                title=title,
                description=f"Color {idx}/{len(colors)}",
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
