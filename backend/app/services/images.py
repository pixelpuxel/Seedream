import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/heic": "heic",
    "image/heif": "heif",
}


@dataclass(frozen=True)
class ImageInfo:
    mime_type: str
    extension: str
    width: int
    height: int


def inspect_image(data: bytes, declared_mime: str | None = None) -> ImageInfo:
    if declared_mime and declared_mime.lower() not in ALLOWED_MIME:
        raise ValueError("Nicht unterstützter Bildtyp.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            detected = Image.MIME.get(image.format or "", declared_mime or "")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Die Datei ist kein gültiges Bild.") from exc
    mime = (detected or declared_mime or "").lower()
    if mime not in ALLOWED_MIME:
        raise ValueError("Nicht unterstütztes Bildformat.")
    if width <= 14 or height <= 14 or width * height > 36_000_000 or not (1 / 16 <= width / height <= 16):
        raise ValueError("Das Bild erfüllt die Größen- oder Seitenverhältnisgrenzen von Seedream nicht.")
    return ImageInfo(mime, ALLOWED_MIME[mime], width, height)


def extension_for_mime(mime: str) -> str:
    return ALLOWED_MIME.get(mime, Path("image.bin").suffix.lstrip("."))


def make_thumbnail(data: bytes, max_size: int) -> bytes:
    """Return a display-oriented JPEG without altering the stored original."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image)
                image = background
            elif image.mode == "L":
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, "JPEG", quality=84, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Vorschaubild konnte nicht erstellt werden.") from exc


def prepare_provider_image(data: bytes, max_dimension: int = 6000) -> tuple[bytes, str]:
    """Create a display-oriented JPEG for Ark while preserving the stored original."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            output = io.BytesIO()
            image.save(output, "JPEG", quality=92, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Das Referenzbild konnte nicht für Seedream vorbereitet werden.") from exc
