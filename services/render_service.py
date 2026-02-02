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
from urllib.parse import urlparse
import ipaddress

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


def _safe_url_fetcher(url: str) -> dict:
    """
    Secure URL fetcher for WeasyPrint to prevent SSRF attacks.
    
    Only allows data: URIs, blocks file:// and private IPs.
    """
    from weasyprint import default_url_fetcher
    
    parsed = urlparse(url)
    
    # Allow data: URIs for embedded images by using default fetcher
    if parsed.scheme == "data":
        return default_url_fetcher(url)
    
    # Block file:// and other dangerous schemes
    if parsed.scheme in ("file", "ftp", ""):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    
    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")
    
    # Block private IP addresses
    if parsed.hostname:
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValueError(f"Blocked private/loopback IP: {parsed.hostname}")
        except ValueError:
            # Not an IP address, check for localhost
            if parsed.hostname.lower() in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(f"Blocked localhost: {parsed.hostname}")
    
    # If we get here, block all external URLs for security
    # Templates should only use data: URIs for embedded images
    raise ValueError(f"External URL blocked: {url}")


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
        height = base + (len(rates) * per_rate) + (rate_images * 5)
        if profile.get("image"):
            height += 100

        # Clamp to reasonable bounds
        height = max(520, min(height, 4000))
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
                # Run rendering in thread pool to avoid blocking event loop
                return await asyncio.to_thread(
                    self._render_with_weasyprint, html, width, height
                )
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
            # Measure the full body content height (includes padding and card)
            measured = self._measure_weasyprint_body(html)
            if measured:
                # Use ceil + a small safety margin to avoid clipping due to rounding.
                import math

                width = int(math.ceil(measured["width"])) + 2
                height = int(math.ceil(measured["height"])) + 6

            # Render PDF at measured size - keep original styles including backgrounds
            render_height = max(height, 200)
            page_style = f"<style>@page {{ size: {width}px {render_height}px; margin: 0; }}</style>"
            html_with_size = html.replace("</head>", f"{page_style}</head>")

            # Use secure URL fetcher to prevent SSRF attacks
            html_doc = HTML(string=html_with_size, url_fetcher=_safe_url_fetcher)
            pdf_bytes = html_doc.write_pdf()

            if pdf_bytes:
                try:
                    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_document[0]
                    
                    # Render at 2x for quality
                    zoom = 2.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    
                    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    pdf_document.close()
                    
                    # No cropping needed - we measured the exact size
                    buffer = io.BytesIO()
                    pil_img.save(buffer, format="JPEG", quality=92, optimize=True)
                    buffer.seek(0)
                    
                    return buffer.getvalue()
                    
                except Exception as pdf_error:
                    logger.error("PDF to image conversion failed: %s", pdf_error)
                    return self._render_placeholder(width, height)
                    
        except Exception as e:
            logger.error("WeasyPrint rendering failed: %s", e)

        return self._render_placeholder(width, height)

    def _measure_weasyprint_body(self, html: str) -> Optional[Dict[str, float]]:
        """
        Measure the rendered size of the `.rate-card` box and approximate the
        surrounding background padding from the template's `body { padding: ... }`.

        This relies on private WeasyPrint box APIs and is best-effort. If anything
        fails, callers should fall back to a conservative fixed size.
        """
        try:
            import re

            # Try to infer the template's body padding so the final image includes
            # the intended background border without excessive empty space.
            padding = 24
            m = re.search(r"body\\s*\\{[^}]*?padding\\s*:\\s*(\\d+)px", html, flags=re.IGNORECASE | re.DOTALL)
            if m:
                try:
                    padding = int(m.group(1))
                except Exception:
                    padding = 24

            # Render on a tall page so content isn't clipped
            probe_style = "<style>@page { size: 1400px 5000px; margin: 0; }</style>"
            html_probe = html.replace("</head>", f"{probe_style}</head>")
            doc = HTML(string=html_probe).render()
            if not doc.pages:
                return None

            root = getattr(doc.pages[0], "_page_box", None)
            if root is None:
                return None

            def _find_by_class(box: Any, class_name: str) -> Optional[Any]:
                el = getattr(box, "element", None)
                if el is not None:
                    cls = el.get("class") or ""
                    if class_name in cls.split():
                        return box
                for child in getattr(box, "children", []) or []:
                    found = _find_by_class(child, class_name)
                    if found:
                        return found
                return None

            # Find .rate-card element
            card = _find_by_class(root, "rate-card")
            if card is None:
                return None

            # WeasyPrint boxes sometimes report element height/width as the available
            # page size. Compute a bounding box using descendant extents instead.
            card_left = float(getattr(card, "position_x", 0) or 0)
            card_top = float(getattr(card, "position_y", 0) or 0)
            min_x0 = card_left
            min_y0 = card_top
            max_x1 = card_left
            max_y1 = card_top

            def _box_w(b: Any) -> float:
                return float(
                    getattr(b, "border_width", 0)
                    or getattr(b, "width", 0)
                    or 0
                )

            def _box_h(b: Any) -> float:
                return float(
                    getattr(b, "border_height", 0)
                    or getattr(b, "height", 0)
                    or 0
                )

            # Include the card itself and all descendants.
            stack = [card]
            while stack:
                b = stack.pop()
                stack.extend(getattr(b, "children", []) or [])

                w = _box_w(b)
                h = _box_h(b)
                if w <= 0 and h <= 0:
                    continue

                x = float(getattr(b, "position_x", 0) or 0)
                y = float(getattr(b, "position_y", 0) or 0)
                min_x0 = min(min_x0, x)
                min_y0 = min(min_y0, y)
                max_x1 = max(max_x1, x + w)
                max_y1 = max(max_y1, y + h)

            card_w = max(0.0, max_x1 - min_x0)
            card_h = max(0.0, max_y1 - min_y0)
            if card_w <= 0 or card_h <= 0:
                return None

            # Include the template's body padding on all sides.
            w = card_w + (padding * 2)
            # Slight extra bottom space to account for shadows/border-radius bleed.
            h = card_h + (padding * 2) + 10
                
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


# Shared instance for convenient imports - lazy initialization
# Do not initialize at import time to avoid startup crashes
_render_service_proxy = None

def __getattr__(name):
    """Lazy proxy for render_service attribute access."""
    if name == "render_service":
        return get_render_service()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
