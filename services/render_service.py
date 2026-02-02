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

# Try PyMuPDF for PDF to image conversion (pure Python, no system dependencies)
try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

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

        # WeasyPrint doesn't reliably "auto-fit" page height; if we don't give a
        # target height, the PDF->image conversion can include a lot of empty
        # background. Use template-aware sizing so the screenshot is tightly
        # cropped to the card.
        tpl = (template or "minimal").lower()
        width_by_template = {
            "minimal": 540,
            "colorful": 650,
            "detailed": 700,
            "professional": 800,
        }
        base_by_template = {
            "minimal": 420,
            "colorful": 560,
            "detailed": 640,
            "professional": 700,
        }
        per_rate_by_template = {
            "minimal": 95,
            "colorful": 125,
            "detailed": 125,
            "professional": 150,
        }

        width = width_by_template.get(tpl, 540)
        base = base_by_template.get(tpl, 520)
        per_rate = per_rate_by_template.get(tpl, 120)

        # Count per-rate thumbnails (not the header profile image)
        rate_images = sum(1 for r in rates.values() if isinstance(r, dict) and r.get("image"))
        height = base + (len(rates) * per_rate) + (rate_images * 40)
        if profile.get("image"):
            height += 240

        # Clamp to reasonable bounds
        height = max(520, min(height, 2200))
        return await self._html_to_jpg(html, width=width, height=height)

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

        if not HAS_PYMUPDF:
            logger.error("PyMuPDF not available. Install with: pip install pymupdf")
            return self._render_placeholder(width, height)

        try:
            # First pass: ask WeasyPrint's layout engine for the actual rendered box
            # size of the main container (used by our templates). This uses private
            # WeasyPrint APIs but avoids guessing and yields tight crops.
            measured = self._measure_weasyprint_box(html, class_name="rate-card")
            pad = 20
            if measured:
                width = int(measured["width"]) + (pad * 2)
                height = int(measured["height"]) + (pad * 2)

            # Second pass: render a PDF at the measured size.
            render_height = max(height, 200)
            page_style = f"<style>@page {{ size: {width}px {render_height}px; margin: 0; }}</style>"
            # Override template body backgrounds so we capture the card content without
            # the big gradient/solid page background.
            pad_style = (
                f"<style>"
                f"html,body{{margin:0;overflow:hidden;background:#ffffff !important;}}"
                f"body{{padding:{pad}px;}}"
                f"</style>"
            ) if measured else (
                "<style>html,body{margin:0;overflow:hidden;background:#ffffff !important;}</style>"
            )
            html_with_size = html.replace("</head>", f"{page_style}{pad_style}</head>")

            html_doc = HTML(string=html_with_size)
            pdf_bytes = html_doc.write_pdf()

            if pdf_bytes:
                # Convert PDF to image using PyMuPDF (pure Python, no system dependencies)
                try:
                    # Open PDF from bytes
                    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
                    
                    # Get first page
                    page = pdf_document[0]
                    
                    # Render page to pixmap (image) at 2x for quality
                    zoom = 2.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    
                    # Convert to PIL Image
                    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    pdf_document.close()
                    
                    # Crop empty space from bottom only
                    # Scan from bottom to find where content ends
                    img_array = pil_img.load()
                    img_width, img_height = pil_img.size
                    
                    # Get the background color from bottom-left corner (should be empty bg)
                    bg_color = img_array[5, img_height - 5]
                    
                    # Find the last row that differs from background
                    content_bottom = img_height
                    for y in range(img_height - 1, 0, -1):
                        row_has_content = False
                        for x in range(0, img_width, 10):  # Sample every 10 pixels for speed
                            pixel = img_array[x, y]
                            # Check if pixel differs significantly from background
                            if abs(pixel[0] - bg_color[0]) > 10 or \
                               abs(pixel[1] - bg_color[1]) > 10 or \
                               abs(pixel[2] - bg_color[2]) > 10:
                                row_has_content = True
                                break
                        if row_has_content:
                            content_bottom = y + 20  # Add small padding
                            break
                    
                    # Crop to content
                    if content_bottom < img_height:
                        pil_img = pil_img.crop((0, 0, img_width, min(content_bottom, img_height)))
                    
                    # Convert back to JPEG bytes
                    buffer = io.BytesIO()
                    pil_img.save(buffer, format="JPEG", quality=92, optimize=True)
                    buffer.seek(0)
                    
                    return buffer.getvalue()
                    
                except Exception as pdf_error:
                    logger.error("PDF to image conversion failed: %s", pdf_error)
                    return self._render_placeholder(width, height)
                    
        except Exception as e:
            logger.error("WeasyPrint rendering failed: %s", e)

        # Fallback to placeholder if rendering fails
        return self._render_placeholder(width, height)

    def _measure_weasyprint_box(self, html: str, class_name: str) -> Optional[Dict[str, float]]:
        """
        Measure the rendered size of the first layout box that contains `class_name`.

        This relies on private WeasyPrint APIs (`page._page_box` and box.element/children).
        If WeasyPrint changes internals, measurement will gracefully fail and we'll fall
        back to our existing PDF crop logic.
        """
        try:
            # Render on a tall page so the element isn't clipped.
            probe_style = "<style>@page { size: 1200px 5000px; margin: 0; }</style>"
            html_probe = html.replace("</head>", f"{probe_style}</head>")
            doc = HTML(string=html_probe).render()
            if not doc.pages:
                return None

            root = getattr(doc.pages[0], "_page_box", None)
            if root is None:
                return None

            def _has_class(box: Any) -> bool:
                el = getattr(box, "element", None)
                if el is None:
                    return False
                cls = el.get("class") or ""
                return class_name in cls.split()

            def _walk(box: Any) -> Optional[Any]:
                if _has_class(box):
                    return box
                for child in getattr(box, "children", []) or []:
                    found = _walk(child)
                    if found is not None:
                        return found
                return None

            target = _walk(root)
            if target is None:
                return None

            w = float(getattr(target, "width", 0) or 0)
            h = float(getattr(target, "height", 0) or 0)
            if w <= 0 or h <= 0:
                return None
            return {"width": w, "height": h}
        except Exception as exc:
            logger.debug("WeasyPrint measure failed: %s", exc)
            return None



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
