"""Manipolazione delle immagini. Niente archivio, niente database, niente coda:
solo pixel in entrata e byte in uscita, ed è ciò che rende questa la parte del
worker più facile da ragionare e da provare.

Tre varianti, dimensionate sul lato lungo così le proporzioni non si toccano mai:

    thumb   300 px   la griglia della libreria
    medium  800 px   la pagina di dettaglio
    large  1200 px   con la filigrana

Tutte vengono scritte in JPEG. Gli originali restano intatti nel loro bucket,
e le derivate di una libreria fotografica non hanno bisogno di trasparenza né
della dimensione senza perdite di un PNG.
"""

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)

JPEG_QUALITY = 85
VARIANT_CONTENT_TYPE = "image/jpeg"


@dataclass(frozen=True)
class VariantSpec:
    kind: str
    long_side: int
    watermark: bool


VARIANT_SPECS = (
    VariantSpec("thumb", 300, watermark=False),
    VariantSpec("medium", 800, watermark=False),
    VariantSpec("large", 1200, watermark=True),
)


@dataclass(frozen=True)
class GeneratedVariant:
    kind: str
    data: bytes
    width: int
    height: int
    content_type: str = VARIANT_CONTENT_TYPE


class InvalidImageError(Exception):
    """I byte non sono un'immagine con cui possiamo lavorare."""


def open_image(data: bytes) -> Image.Image:
    """Decodifica l'originale e lo rimette dritto.

    Un telefono scrive la foto come l'ha letta il sensore e registra la rotazione
    nel campo EXIF dell'orientamento. Senza applicarlo, metà delle foto scattate
    col telefono uscirebbero coricate su un fianco.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except UnidentifiedImageError as exc:
        raise InvalidImageError("Il file non è un'immagine leggibile") from exc
    except Image.DecompressionBombError as exc:
        # Un file piccolo può descrivere un'immagine enorme: decodificarla
        # mangerebbe tutta la memoria del pod.
        raise InvalidImageError("L'immagine è troppo grande da elaborare") from exc
    except OSError as exc:
        raise InvalidImageError("Il file è danneggiato o incompleto") from exc

    return ImageOps.exif_transpose(image)


def generate_variants(image: Image.Image, watermark_text: str) -> list[GeneratedVariant]:
    base = _to_rgb(image)
    return [_build_variant(base, spec, watermark_text) for spec in VARIANT_SPECS]


def _build_variant(base: Image.Image, spec: VariantSpec, watermark_text: str) -> GeneratedVariant:
    resized = _resize_long_side(base, spec.long_side)
    if spec.watermark:
        resized = _apply_watermark(resized, watermark_text)

    buffer = io.BytesIO()
    resized.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = buffer.getvalue()

    log.info(
        "variante %s: %sx%s, %s byte", spec.kind, resized.width, resized.height, len(data)
    )
    return GeneratedVariant(
        kind=spec.kind, data=data, width=resized.width, height=resized.height
    )


def _to_rgb(image: Image.Image) -> Image.Image:
    """Il JPEG non ha canale alfa: la trasparenza viene appiattita sul bianco."""
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _resize_long_side(image: Image.Image, target: int) -> Image.Image:
    long_side = max(image.width, image.height)
    if long_side <= target:
        # Mai ingrandire: gonfiare una foto piccola non aggiunge dettaglio, solo byte.
        return image.copy()

    ratio = target / long_side
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _apply_watermark(image: Image.Image, text: str) -> Image.Image:
    """Scrive il testo su una copia dell'immagine, in basso a destra.

    Disegnato su un livello trasparente a parte e poi composto: dipingere
    direttamente sulla foto darebbe bordi duri e scalettati invece di un segno morbido.
    """
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Proporzionale all'immagine, così il segno sembra lo stesso a ogni dimensione.
    font_size = max(14, image.width // 28)
    font = ImageFont.load_default(size=font_size)

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    margin = max(8, image.width // 60)
    position = (image.width - (right - left) - margin, image.height - (bottom - top) - margin * 2)

    # Un'ombra scura sotto il testo chiaro lo tiene leggibile tanto su un cielo
    # bianco quanto su uno sfondo scuro.
    draw.text((position[0] + 2, position[1] + 2), text, font=font, fill=(0, 0, 0, 120))
    draw.text(position, text, font=font, fill=(255, 255, 255, 190))

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
