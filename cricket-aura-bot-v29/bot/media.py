"""
Helpers for picking and sending your video/GIF clips and banner images.

Supports .mp4, .mov (sent as video) and .gif (sent as animation)
automatically based on file extension -- just drop the file in
media/ and list its path in config.py, no extra setup needed.

If a configured file is missing or Telegram rejects it (bad format,
too large, etc.), we log a clear warning to the console and fall back
to a plain text message instead of crashing the bot.
"""

import logging
import random
from pathlib import Path

from telegram import InputFile
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

GIF_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def pick_clip(clip_list: list) -> Path | None:
    """Pick a random clip from a list of paths that actually exist on disk.
    Logs a warning for any configured path that's missing, so it's easy
    to spot typos/wrong folders in the console."""
    existing = []
    for c in clip_list:
        p = Path(c)
        if p.is_file():
            existing.append(p)
        else:
            logger.warning("Configured media file not found, skipping: %s", p.resolve())
    if not existing:
        return None
    return random.choice(existing)


async def send_clip(bot, chat_id: int, clip_list: list, caption: str, parse_mode="HTML", reply_markup=None):
    """
    Sends a random clip from clip_list with the given caption.
    - .gif files are sent with send_animation (Telegram's native GIF support)
    - .mp4/.mov/.m4v/.webm files are sent with send_video
    If no clip is available (empty list, missing file, or Telegram
    rejects it), sends caption as plain text instead.
    """
    clip = pick_clip(clip_list)
    if clip is None:
        return await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )

    ext = clip.suffix.lower()
    try:
        with open(clip, "rb") as f:
            if ext in GIF_EXTENSIONS:
                return await bot.send_animation(
                    chat_id=chat_id,
                    animation=InputFile(f),
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            else:
                return await bot.send_video(
                    chat_id=chat_id,
                    video=InputFile(f),
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
    except TelegramError as e:
        logger.warning("Telegram rejected media file %s (%s) -- sending text instead.", clip, e)
        return await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )


async def send_banner(bot, chat_id: int, image_path, caption: str, parse_mode="HTML", reply_markup=None):
    """
    Sends a static banner image (jpg/png) with a caption.
    If image_path is None, missing, or Telegram rejects it, sends
    caption as plain text instead (and logs why, to the console).
    """
    if image_path is None:
        return await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )

    path = Path(image_path)
    if not path.is_file():
        logger.warning("Configured banner image not found, sending text instead: %s", path.resolve())
        return await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )

    try:
        with open(path, "rb") as f:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f),
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
    except TelegramError as e:
        logger.warning("Telegram rejected banner image %s (%s) -- sending text instead.", path, e)
        return await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=parse_mode, reply_markup=reply_markup
        )


# ==================== MOTM / Best Batter / Best Bowler card ====================

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps

MOTM_TEMPLATE = Path(__file__).resolve().parent.parent / "media" / "motm_template.png"
FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_BOLD = FONT_DIR / "Lora-Bold.ttf"
FONT_REGULAR = FONT_DIR / "LibreBaskerville-Regular.ttf"

# Precisely measured against motm_template.png (896x1195). If you swap in
# a different template image, these coordinates will need re-measuring.
_MOTM_CIRCLE = (448, 300, 145)     # center_x, center_y, radius
_MOTM_NAME_BOX = (255, 550, 645, 610)  # x0, y0, x1, y1

_BATTER_CIRCLE = (155, 755, 98)
_BATTER_NAME_BOX = (278, 718, 830, 762)
_BATTER_STATS_Y = (798, 830)
_BATTER_STAT_X = {"R": 320, "B": 462, "6s": 610, "4s": 752}

_BOWLER_CIRCLE = (155, 985, 98)
_BOWLER_NAME_BOX = (278, 942, 830, 998)
_BOWLER_STATS_Y = (1038, 1070)
_BOWLER_STAT_X = {"R": 320, "O": 462, "W": 610, "E": 752}

TEXT_COLOR = (28, 22, 18)


def _circular_crop(img: Image.Image, diameter: int) -> Image.Image:
    img = ImageOps.fit(img, (diameter, diameter), method=Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter, diameter], fill=255)
    out = Image.new("RGBA", (diameter, diameter))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int,
              font_path: Path, start_size: int, min_size: int = 14) -> ImageFont.FreeTypeFont:
    size = start_size
    while size > min_size:
        f = ImageFont.truetype(str(font_path), size)
        bbox = draw.textbbox((0, 0), text, font=f)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= max_width and h <= max_height:
            return f
        size -= 2
    return ImageFont.truetype(str(font_path), min_size)


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, box: tuple,
                         font_path: Path, start_size: int, fill=TEXT_COLOR):
    x0, y0, x1, y1 = box
    max_w, max_h = (x1 - x0) - 16, (y1 - y0) - 10
    font = _fit_font(draw, text, max_w, max_h, font_path, start_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    draw.text((cx - w / 2, cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _paste_circle_photo(card: Image.Image, photo_bytes: bytes | None, circle: tuple):
    cx, cy, r = circle
    diameter = r * 2
    if photo_bytes:
        try:
            photo = Image.open(BytesIO(photo_bytes))
            cropped = _circular_crop(photo, diameter)
            card.alpha_composite(cropped, (cx - r, cy - r))
            return
        except Exception as e:
            logger.warning("Failed to process profile photo, using placeholder: %s", e)

    # No photo available (privacy settings / no profile pic) -- draw a
    # simple placeholder disc with the initial letter, styled to blend in.
    placeholder = Image.new("RGBA", (diameter, diameter), (35, 30, 28, 255))
    d = ImageDraw.Draw(placeholder)
    d.ellipse([0, 0, diameter, diameter], fill=(45, 38, 34, 255))
    card.alpha_composite(_circular_crop(placeholder, diameter), (cx - r, cy - r))


async def render_motm_card(
    bot,
    motm_name: str,
    motm_photo_bytes: bytes | None,
    batter_name: str,
    batter_photo_bytes: bytes | None,
    batter_stats: dict,   # {"R": .., "B": .., "6s": .., "4s": ..}
    bowler_name: str,
    bowler_photo_bytes: bytes | None,
    bowler_stats: dict,   # {"R": .., "O": .., "W": .., "E": ..}
) -> BytesIO | None:
    """
    Composes the MOTM / Best Batter / Best Bowler card from motm_template.png,
    overlaying each player's Telegram profile photo (if available) and their
    name + stats. Returns a BytesIO of the final PNG, or None if the base
    template file is missing.
    """
    if not MOTM_TEMPLATE.is_file():
        logger.warning("MOTM template not found at %s -- skipping card image.", MOTM_TEMPLATE)
        return None

    card = Image.open(MOTM_TEMPLATE).convert("RGBA")
    draw = ImageDraw.Draw(card)

    # --- MOTM ---
    _paste_circle_photo(card, motm_photo_bytes, _MOTM_CIRCLE)
    _draw_centered_text(draw, motm_name, _MOTM_NAME_BOX, FONT_BOLD, 42)

    # --- Best Batter ---
    _paste_circle_photo(card, batter_photo_bytes, _BATTER_CIRCLE)
    _draw_centered_text(draw, batter_name, _BATTER_NAME_BOX, FONT_BOLD, 30)
    for key, x in _BATTER_STAT_X.items():
        val = str(batter_stats.get(key, 0))
        f = _fit_font(draw, val, 70, 30, FONT_REGULAR, 26)
        bbox = draw.textbbox((0, 0), val, font=f)
        w = bbox[2] - bbox[0]
        y0, y1 = _BATTER_STATS_Y
        draw.text((x - w / 2, (y0 + y1) / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                   val, font=f, fill=(235, 225, 210))

    # --- Best Bowler ---
    _paste_circle_photo(card, bowler_photo_bytes, _BOWLER_CIRCLE)
    _draw_centered_text(draw, bowler_name, _BOWLER_NAME_BOX, FONT_BOLD, 30)
    for key, x in _BOWLER_STAT_X.items():
        val = str(bowler_stats.get(key, 0))
        f = _fit_font(draw, val, 70, 30, FONT_REGULAR, 26)
        bbox = draw.textbbox((0, 0), val, font=f)
        w = bbox[2] - bbox[0]
        y0, y1 = _BOWLER_STATS_Y
        draw.text((x - w / 2, (y0 + y1) / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                   val, font=f, fill=(235, 225, 210))

    buf = BytesIO()
    card.convert("RGB").save(buf, format="JPEG", quality=92)
    buf.seek(0)
    buf.name = "match_card.jpg"
    return buf


async def fetch_profile_photo_bytes(bot, user_id: int) -> bytes | None:
    """Fetches a user's current Telegram profile photo as raw bytes, or
    None if they don't have one set / it can't be fetched (privacy, etc.)."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos or photos.total_count == 0:
            return None
        file_id = photos.photos[0][-1].file_id  # largest size
        tg_file = await bot.get_file(file_id)
        buf = BytesIO()
        await tg_file.download_to_memory(out=buf)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning("Could not fetch profile photo for user %s: %s", user_id, e)
        return None
