"""
Render service - converts HTML templates to JPG images.

Uses Jinja2 for templating and Pillow for image rendering.
For more complex rendering, uses WeasyPrint for PDF then converts to image.
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
    # Suppress WeasyPrint's verbose logging
    logging.getLogger("weasyprint").setLevel(logging.WARNING)
    logging.getLogger("fontTools").setLevel(logging.WARNING)
except ImportError:
    HAS_WEASYPRINT = False

# Try pdf2image for PDF to PNG conversion (requires poppler)
try:
    from pdf2image import convert_from_bytes
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

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
        html = template_obj.render(colors=colors, method=method, count=count)
        return await self._html_to_jpg(html, width=800, height=400)

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
                return self._render_with_weasyprint(html, width, height)
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

    def _render_with_weasyprint(self, html: str, width: int = 800, height: int = 600) -> bytes:
        if not HAS_WEASYPRINT:
            raise RuntimeError("WeasyPrint not available")

        try:
            # WeasyPrint v53+ removed write_png(), must use PDF then convert
            html_doc = HTML(string=html)
            pdf_bytes = html_doc.write_pdf()

            if not HAS_PDF2IMAGE:
                logger.error("pdf2image not available. Install with: pip install pdf2image")
                logger.error("Also requires poppler: https://github.com/oschwartz10612/poppler-windows/releases")
                return self._render_placeholder(width, height)

            if pdf_bytes:
                # Convert PDF to image using pdf2image (requires poppler)
                try:
                    images = convert_from_bytes(pdf_bytes, dpi=150)
                    if images:
                        img = images[0].convert("RGB")
                        out = io.BytesIO()
                        img.save(out, format="JPEG", quality=95)
                        out.seek(0)
                        return out.getvalue()
                except Exception as pdf_error:
                    logger.error("PDF to image conversion failed: %s", pdf_error)
                    logger.error("Ensure poppler is installed and in PATH")
                    logger.error("Windows: Download from https://github.com/oschwartz10612/poppler-windows/releases")
                    logger.error("Linux: apt-get install poppler-utils")
                    logger.error("macOS: brew install poppler")
                    return self._render_placeholder(width, height)
        except Exception as e:
            logger.error("WeasyPrint rendering failed: %s", e)

        # Fallback to placeholder if rendering fails
        return self._render_placeholder(width, height)



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
