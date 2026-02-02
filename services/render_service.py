"""
Render service - converts HTML templates to JPG images.

Uses Jinja2 for templating and Pillow for image rendering.
For more complex rendering, uses Pillow-based drawing for key outputs.
"""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from weasyprint import HTML
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

from core.paths import BASE_DIR

logger = logging.getLogger("discbot.render")

# Template directory
TEMPLATES_DIR = BASE_DIR / "templates" / "renders"


class RenderService:
    """Service for rendering HTML templates to JPG images."""

    def __init__(self) -> None:
        if not HAS_JINJA2:
            raise RuntimeError("Jinja2 is required for render service. Install with: pip install jinja2")

        if not HAS_PILLOW:
            raise RuntimeError("Pillow is required for render service. Install with: pip install pillow")

        # Initialize Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(['html', 'xml']),
        )

    async def render_invoice(
        self,
        commission: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None,
        template: str = "default",
    ) -> bytes:
        """
        Render commission invoice to JPG.

        Args:
            commission: Commission data dictionary
            template: Template name (default/minimal/detailed)

        Returns:
            JPG image as bytes
        """
        template_name = f"invoice_{template}.html" if template != "default" else "invoice.html"

        try:
            template_obj = self.env.get_template(template_name)
        except Exception:
            # Fallback to default
            template_obj = self.env.get_template("invoice.html")

        html = template_obj.render(commission=commission, profile=profile or {})
        return await self._html_to_jpg(html, width=800, height=1000)

    async def render_rate_card(
        self,
        profile: Dict[str, Any],
        rates: Dict[str, Any],
        template: str = "minimal",
    ) -> bytes:
        """
        Render rate card to JPG.

        Args:
            profile: User profile data
            rates: Rate information
            template: Template style (minimal/detailed/colorful/professional)

        Returns:
            JPG image as bytes
        """
        template_name = f"rate_card_{template}.html"

        try:
            template_obj = self.env.get_template(template_name)
        except Exception:
            # Fallback to minimal
            template_obj = self.env.get_template("rate_card_minimal.html")

        html = template_obj.render(profile=profile, rates=rates)
        return await self._html_to_jpg(html, width=600, height=800)

    async def render_contract(
        self,
        commission: Dict[str, Any],
        terms: Dict[str, Any],
    ) -> bytes:
        """
        Render commission contract to JPG.

        Args:
            commission: Commission data
            terms: Contract terms and conditions

        Returns:
            JPG image as bytes
        """
        template_obj = self.env.get_template("contract.html")
        html = template_obj.render(commission=commission, terms=terms)
        return await self._html_to_jpg(html, width=800, height=1200)

    async def render_palette(
        self,
        colors: list[str],
        method: str,
        count: int,
    ) -> bytes:
        """
        Render color palette to JPG.

        Args:
            colors: List of hex color codes
            method: Color theory method used
            count: Number of colors

        Returns:
            JPG image as bytes
        """
        template_obj = self.env.get_template("palette.html")
        _ = template_obj.render(colors=colors, method=method, count=count)
        return self._render_palette_image(colors, method, count)

    async def _html_to_jpg(
        self,
        html: str,
        width: int = 800,
        height: int = 600,
    ) -> bytes:
        """
        Convert HTML to JPG image.

        Render HTML with WeasyPrint when available; otherwise use a Pillow placeholder.
        """
        if HAS_WEASYPRINT:
            try:
                return self._render_with_weasyprint(html)
            except Exception as exc:
                logger.warning("WeasyPrint render failed, using fallback: %s", exc)

        return self._render_placeholder(width, height)

    def _render_placeholder(self, width: int, height: int) -> bytes:
        img = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()

        draw.text((10, 10), "Rendered Image", fill="black", font=font)
        draw.text((10, 40), f"Width: {width}, Height: {height}", fill="black", font=font)
        draw.text((10, 70), "HTML rendering not available in this environment", fill="gray", font=font)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        return buffer.getvalue()

    def _render_with_weasyprint(self, html: str) -> bytes:
        if not HAS_WEASYPRINT:
            raise RuntimeError("WeasyPrint not available")

        # WeasyPrint supports PNG rendering via write_png in some versions.
        # Fall back to PDF if PNG isn't available.
        doc = HTML(string=html)
        if hasattr(doc, "write_png"):
            png_bytes = doc.write_png()
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=95)
            out.seek(0)
            return out.getvalue()

        pdf_bytes = doc.write_pdf()
        if not HAS_PILLOW:
            return pdf_bytes
        try:
            img = Image.open(io.BytesIO(pdf_bytes)).convert("RGB")
        except Exception:
            return self._render_placeholder(800, 600)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95)
        out.seek(0)
        return out.getvalue()

    def _render_palette_image(
        self,
        colors: list[str],
        method: str,
        count: int,
    ) -> bytes:
        width = 800
        height = 400
        margin = 40
        header_h = 70
        gutter = 10
        swatch_h = height - margin * 2 - header_h
        swatch_w = max(1, (width - margin * 2 - gutter * (len(colors) - 1)) // max(1, len(colors)))

        img = Image.new("RGB", (width, height), color="#f5f5f5")
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("arial.ttf", 24)
            meta_font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            title_font = ImageFont.load_default()
            meta_font = ImageFont.load_default()

        draw.text((margin, margin), "Color Palette", fill="#111111", font=title_font)
        draw.text(
            (margin, margin + 30),
            f"{method} · {count} colors",
            fill="#666666",
            font=meta_font,
        )

        x = margin
        y = margin + header_h
        for color in colors:
            draw.rounded_rectangle(
                [x, y, x + swatch_w, y + swatch_h],
                radius=8,
                fill=color,
                outline="#dddddd",
                width=1,
            )
            text = color.upper()
            text_w, text_h = draw.textsize(text, font=meta_font)
            text_x = x + (swatch_w - text_w) / 2
            text_y = y + swatch_h - text_h - 10
            draw.rectangle(
                [text_x - 6, text_y - 4, text_x + text_w + 6, text_y + text_h + 4],
                fill="#ffffff",
            )
            draw.text((text_x, text_y), text, fill="#111111", font=meta_font)
            x += swatch_w + gutter

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        return buffer.getvalue()


# Global service instance
_render_service: Optional[RenderService] = None


def get_render_service() -> RenderService:
    """Get or create the global render service instance."""
    global _render_service
    if _render_service is None:
        _render_service = RenderService()
    return _render_service


# Shared instance for convenient imports
render_service = get_render_service()
