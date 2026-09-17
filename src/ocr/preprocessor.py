"""Stage 1 — file to normalized page images.

Converts a submission file (PDF / PNG / JPG / ...) into an ordered list of
normalized ``PIL.Image`` pages. PDF rendering uses ``pypdfium2`` (self-contained,
no system Poppler dependency). This stage does not extract anything.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from src.config import settings
from src.logging.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_PDF_EXT = {".pdf"}


def load_pages(path: str | Path) -> list[Image.Image]:
    """Load a submission file into an ordered list of normalized RGB page images.

    Args:
        path: Path to a ``.pdf`` or image file.

    Returns:
        Page images in reading order (single-element list for image inputs).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is unsupported.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Submission not found: {p}")

    ext = p.suffix.lower()
    if ext in SUPPORTED_PDF_EXT:
        raw_pages = _render_pdf(p)
    elif ext in SUPPORTED_IMAGE_EXT:
        raw_pages = [Image.open(p)]
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            f"Supported: {sorted(SUPPORTED_PDF_EXT | SUPPORTED_IMAGE_EXT)}"
        )

    pages = [_normalize(img) for img in raw_pages]
    logger.info("Loaded %d page(s) from %s", len(pages), p.name)
    return pages


def _render_pdf(path: Path) -> list[Image.Image]:
    """Render every PDF page to a PIL image at the configured DPI."""
    import pypdfium2 as pdfium

    scale = settings.PDF_RENDER_DPI / 72.0  # pdfium scale is relative to 72 DPI
    pdf = pdfium.PdfDocument(str(path))
    try:
        images: list[Image.Image] = []
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil())
        return images
    finally:
        pdf.close()


def _normalize(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation, convert to RGB, and cap width."""
    img = ImageOps.exif_transpose(img)  # respect camera/scanner rotation
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > settings.MAX_IMAGE_WIDTH:
        ratio = settings.MAX_IMAGE_WIDTH / float(img.width)
        new_size = (settings.MAX_IMAGE_WIDTH, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    return img
