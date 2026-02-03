"""
Render service - small image generators for Discord.

HTML template rendering has been removed. The only supported renderer is the
color palette image used by the `palette` command.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("discbot.render")


class RenderService:
    """Generate small helper images (currently: palettes)."""

    async def render_palette(
        self,
        colors: list[str],
        method: str,
        count: int,
    ) -> bytes:
        return await asyncio.to_thread(self._render_palette_sync, colors, method, count)

    async def render_color_patch(self, hex_color: str, *, size: int = 96) -> bytes:
        return await asyncio.to_thread(self._render_color_patch_sync, hex_color, int(size))

    async def render_weighted_palette(
        self,
        colors: list[str],
        proportions: list[int],
        method: str,
    ) -> bytes:
        """Render palette with proportional color swatches (for 60-30-10 rule)."""
        return await asyncio.to_thread(self._render_weighted_palette_sync, colors, proportions, method)

    def _render_palette_sync(self, colors: list[str], method: str, count: int) -> bytes:
        width = 800
        height = 400
        padding = 24
        header_h = 90

        img = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("arial.ttf", 22)
            small_font = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            title_font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        safe_method = (method or "palette").strip()
        title = f"Palette ({safe_method}) — {count} color(s)"
        draw.text((padding, padding), title, fill="black", font=title_font)

        # Draw swatches.
        swatch_top = header_h
        swatch_h = height - swatch_top - padding
        swatch_w = (width - (padding * 2)) // max(1, len(colors))

        for i, hex_color in enumerate(colors):
            x0 = padding + (i * swatch_w)
            x1 = x0 + swatch_w
            y0 = swatch_top
            y1 = swatch_top + swatch_h

            # Best-effort hex parsing; fall back to gray.
            try:
                h = (hex_color or "").lstrip("#")
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                rgb = (r, g, b)
            except Exception:
                rgb = (180, 180, 180)

            draw.rectangle([x0, y0, x1 - 2, y1], fill=rgb, outline=(0, 0, 0))

            label = (hex_color or "").upper()
            label_x = x0 + 8
            label_y = y1 - 28
            # Choose a readable text color based on luminance.
            lum = (rgb[0] * 0.299) + (rgb[1] * 0.587) + (rgb[2] * 0.114)
            text_color = "black" if lum > 150 else "white"
            draw.text((label_x, label_y), label, fill=text_color, font=small_font)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()

    def _render_color_patch_sync(self, hex_color: str, size: int) -> bytes:
        size = max(16, min(256, int(size)))
        try:
            h = (hex_color or "").lstrip("#")
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            rgb = (r, g, b)
        except Exception:
            rgb = (180, 180, 180)

        img = Image.new("RGB", (size, size), color=rgb)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size - 1, size - 1], outline=(0, 0, 0), width=2)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _render_weighted_palette_sync(
        self,
        colors: list[str],
        proportions: list[int],
        method: str
    ) -> bytes:
        """
        Synchronous weighted palette rendering.

        Creates a bar showing proportional color distribution (for 60-30-10 rule).
        """
        width = 800
        height = 500
        padding = 24
        header_h = 90
        bar_height = 80  # Proportional bar

        img = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("arial.ttf", 22)
            label_font = ImageFont.truetype("arial.ttf", 18)
            small_font = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            title_font = ImageFont.load_default()
            label_font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        # Title
        title = f"Palette ({method})"
        draw.text((padding, padding), title, fill="black", font=title_font)

        # Subtitle explaining proportions
        role_names = ["Dominant", "Secondary", "Accent"] if len(colors) == 3 else ["Color"] * len(colors)
        subtitle = "Color proportions: " + " | ".join([f"{role_names[i]} {proportions[i]}%" for i in range(len(colors))])
        draw.text((padding, padding + 35), subtitle, fill="gray", font=small_font)

        # Draw proportional bar
        bar_top = header_h
        bar_bottom = bar_top + bar_height
        bar_left = padding
        bar_right = width - padding
        bar_width_total = bar_right - bar_left

        current_x = bar_left
        for i, (color, proportion) in enumerate(zip(colors, proportions)):
            # Parse color
            try:
                h = (color or "").lstrip("#")
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                rgb = (r, g, b)
            except Exception:
                rgb = (180, 180, 180)

            # Calculate width for this proportion
            segment_width = int((proportion / 100.0) * bar_width_total)

            # Draw segment
            draw.rectangle(
                [current_x, bar_top, current_x + segment_width - 2, bar_bottom],
                fill=rgb,
                outline=(0, 0, 0),
                width=2
            )

            # Draw percentage label on bar
            lum = (rgb[0] * 0.299) + (rgb[1] * 0.587) + (rgb[2] * 0.114)
            text_color = "black" if lum > 150 else "white"
            label = f"{proportion}%"

            # Center text in segment
            try:
                bbox = draw.textbbox((0, 0), label, font=label_font)
                text_width = bbox[2] - bbox[0]
            except:
                text_width = 30

            text_x = current_x + (segment_width // 2) - (text_width // 2)
            text_y = bar_top + (bar_height // 2) - 10
            draw.text((text_x, text_y), label, fill=text_color, font=label_font)

            current_x += segment_width

        # Draw individual color swatches below
        swatch_top = bar_bottom + 30
        swatch_h = height - swatch_top - padding - 40
        swatch_w = (width - (padding * 2)) // max(1, len(colors))

        for i, (hex_color, role) in enumerate(zip(colors, role_names)):
            x0 = padding + (i * swatch_w)
            x1 = x0 + swatch_w
            y0 = swatch_top
            y1 = swatch_top + swatch_h

            # Parse color
            try:
                h = (hex_color or "").lstrip("#")
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                rgb = (r, g, b)
            except Exception:
                rgb = (180, 180, 180)

            # Draw swatch
            draw.rectangle([x0, y0, x1 - 2, y1], fill=rgb, outline=(0, 0, 0))

            # Role label
            role_y = y0 + 10
            lum = (rgb[0] * 0.299) + (rgb[1] * 0.587) + (rgb[2] * 0.114)
            text_color = "black" if lum > 150 else "white"
            draw.text((x0 + 8, role_y), role, fill=text_color, font=label_font)

            # Hex label
            label = (hex_color or "").upper()
            label_x = x0 + 8
            label_y = y1 - 28
            draw.text((label_x, label_y), label, fill=text_color, font=small_font)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()


_render_service: Optional[RenderService] = None


def get_render_service() -> RenderService:
    """Get or create the global render service instance."""
    global _render_service
    if _render_service is None:
        _render_service = RenderService()
    return _render_service


def __getattr__(name: str):
    """Lazy proxy for render_service attribute access."""
    if name == "render_service":
        return get_render_service()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
