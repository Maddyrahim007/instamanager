"""
InstaManager — Media Processing Engine.

Uses Pillow to resize, crop, and compress images to meet
Instagram's upload specifications.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image

logger = logging.getLogger("instamanager")

# ── Instagram specs ──────────────────────────────────────────────────────────
FEED_MAX_SIZE = (1080, 1350)
STORY_SIZE = (1080, 1920)
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB


def _fit_image(
    img: Image.Image,
    target_size: tuple[int, int],
    crop: bool = True,
) -> Image.Image:
    """Resize and optionally crop an image to fit `target_size`.

    If crop=True, the image is resized so the smallest dimension matches,
    then center-cropped. Otherwise it's resized to fit within the box.
    """
    tw, th = target_size

    if crop:
        # Resize so the shortest side matches, then center-crop
        aspect = img.width / img.height
        target_aspect = tw / th

        if aspect > target_aspect:
            # Image is wider — fit height, crop width
            new_h = th
            new_w = int(new_h * aspect)
        else:
            # Image is taller — fit width, crop height
            new_w = tw
            new_h = int(new_w / aspect)

        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Center crop
        left = (new_w - tw) // 2
        top = (new_h - th) // 2
        img = img.crop((left, top, left + tw, top + th))
    else:
        img.thumbnail(target_size, Image.LANCZOS)

    return img


def _compress_to_jpeg(img: Image.Image, max_bytes: int = MAX_FILE_SIZE) -> bytes:
    """Convert to JPEG and reduce quality until under max_bytes."""
    # Convert RGBA → RGB
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    quality = 95
    while quality >= 30:
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
        quality -= 5

    # Last resort — return whatever we have
    return data


def process_for_feed(input_path: str, output_dir: Path) -> str:
    """Process an image for Instagram feed posting.

    - Resizes to max 1080×1350
    - Converts to JPEG
    - Ensures file is under 8 MB

    Args:
        input_path: Path to the source image.
        output_dir: Directory to save processed image.

    Returns:
        Path to the processed image file.
    """
    img = Image.open(input_path)
    logger.info("Processing feed image: %s (%dx%d)", input_path, img.width, img.height)

    # Only resize if larger than target
    if img.width > FEED_MAX_SIZE[0] or img.height > FEED_MAX_SIZE[1]:
        img = _fit_image(img, FEED_MAX_SIZE, crop=False)

    data = _compress_to_jpeg(img)
    out_path = output_dir / f"feed_{Path(input_path).stem}.jpg"
    out_path.write_bytes(data)
    logger.info("Feed image saved: %s (%d bytes)", out_path, len(data))
    return str(out_path)


def process_for_story(input_path: str, output_dir: Path) -> str:
    """Process an image for Instagram story posting.

    - Resizes/crops to 1080×1920
    - Converts to JPEG
    - Ensures file is under 8 MB

    Args:
        input_path: Path to the source image.
        output_dir: Directory to save processed image.

    Returns:
        Path to the processed image file.
    """
    img = Image.open(input_path)
    logger.info("Processing story image: %s (%dx%d)", input_path, img.width, img.height)

    img = _fit_image(img, STORY_SIZE, crop=True)
    data = _compress_to_jpeg(img)
    out_path = output_dir / f"story_{Path(input_path).stem}.jpg"
    out_path.write_bytes(data)
    logger.info("Story image saved: %s (%d bytes)", out_path, len(data))
    return str(out_path)


def process_media(
    input_path: str,
    output_dir: Path,
    post_type: Literal["feed", "story", "reel", "carousel"] = "feed",
) -> str:
    """Route to the correct processor based on post type.

    Reels and carousels use feed dimensions by default.

    Args:
        input_path: Path to source media.
        output_dir: Directory for processed output.
        post_type: One of feed, story, reel, carousel.

    Returns:
        Path to the processed media file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if post_type == "story":
        return process_for_story(input_path, output_dir)
    else:
        # feed, reel, carousel all use feed dimensions
        return process_for_feed(input_path, output_dir)
