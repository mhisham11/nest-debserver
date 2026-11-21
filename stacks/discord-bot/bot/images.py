import asyncio
import base64
import io
import logging
import discord
from PIL import Image
from bot.config import ConfigManager

logger = logging.getLogger("bot.images")

MAX_DIMENSION = 1568


def _downscale_and_encode(raw_bytes: bytes) -> str:
    with Image.open(io.BytesIO(raw_bytes)) as img:
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        longest = max(width, height)
        if longest > MAX_DIMENSION:
            scale = MAX_DIMENSION / float(longest)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(out.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


async def extract_image_data_urls(
    attachments: list[discord.Attachment],
    config_mgr: ConfigManager,
) -> list[str]:
    limits = config_mgr.get().limits
    image_attachments = [
        a
        for a in attachments
        if a.content_type and a.content_type.lower().startswith("image/")
    ]

    data_urls: list[str] = []
    for att in image_attachments[: limits.max_images_per_message]:
        if att.size > limits.max_image_bytes:
            logger.info("Skipping attachment %s: size %d exceeds max_image_bytes", att.id, att.size)
            continue
        try:
            raw = await att.read()
            if len(raw) > limits.max_image_bytes:
                continue
            data_url = await asyncio.to_thread(_downscale_and_encode, raw)
            data_urls.append(data_url)
        except Exception as exc:
            logger.warning("Failed to process image attachment %s: %s", att.id, type(exc).__name__)
            continue

    return data_urls
