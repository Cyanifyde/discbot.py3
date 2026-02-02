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

