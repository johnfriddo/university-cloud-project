"""Estrazione dei metadati tecnici che una fotocamera scrive dentro il file.

È la ragione per cui il sistema ha un documentale. Ogni fotocamera scrive un
insieme diverso di campi: un telefono registra l'obiettivo e la posizione GPS,
una reflex registra la terna dell'esposizione, un'immagine scaricata dal web
spesso non porta niente. Costringere tutto questo in colonne relazionali
significherebbe una tabella con cinquanta colonne quasi sempre vuote, e una
migrazione a ogni nuovo modello di fotocamera.

Si conservano solo i campi che la specifica chiede, e solo quando ci sono: un
campo assente semplicemente non compare nel documento, non è salvato come nullo.
"""

import datetime as dt
import logging
from fractions import Fraction

from PIL import ExifTags, Image

log = logging.getLogger(__name__)

# EXIF li salva come frazione, una coppia (numeratore, denominatore): 1/250 di
# secondo, 28/10 per f/2.8. Letti come semplice sequenza diventerebbero il solo
# numeratore, ed è così che f/2.8 diventa f/28.
RATIONAL_FIELDS = {"exposure", "aperture", "focal_length"}

# I campi del blocco principale: chi ha scattato la foto e con cosa.
BASE_TAGS = {
    "Make": "make",
    "Model": "model",
}

# I campi del sotto-blocco Exif: come è stata scattata la foto.
EXIF_TAGS = {
    "LensModel": "lens",
    "ISOSpeedRatings": "iso",
    "PhotographicSensitivity": "iso",
    "ExposureTime": "exposure",
    "FNumber": "aperture",
    "FocalLength": "focal_length",
    "DateTimeOriginal": "taken_at",
}


def extract_exif(image: Image.Image) -> dict:
    """Restituisce i metadati leggibili come dizionario piatto, eventualmente vuoto."""
    try:
        raw = image.getexif()
    except Exception:  # un blocco malformato non deve far fallire l'intero lavoro
        log.warning("blocco EXIF illeggibile, proseguo senza metadati")
        return {}

    if not raw:
        return {}

    metadata: dict = {}
    _collect(metadata, raw, BASE_TAGS)

    try:
        exif_ifd = raw.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        exif_ifd = {}
    _collect(metadata, exif_ifd, EXIF_TAGS)

    if "exposure" in metadata:
        metadata["exposure"] = _format_exposure(metadata["exposure"])
    if "aperture" in metadata:
        metadata["aperture"] = _format_aperture(metadata["aperture"])
    if "focal_length" in metadata:
        metadata["focal_length"] = _format_focal_length(metadata["focal_length"])
    if "taken_at" in metadata:
        metadata["taken_at"] = _format_taken_at(metadata["taken_at"])
        if metadata["taken_at"] is None:
            del metadata["taken_at"]

    return metadata


def _collect(target: dict, block, wanted: dict) -> None:
    for tag_id, value in block.items():
        # I campi viaggiano come numeri: TAGS trasforma 271 in «Make».
        name = ExifTags.TAGS.get(tag_id)
        field = wanted.get(name) if name else None
        if field is None or field in target:
            continue
        cleaned = _to_number(value) if field in RATIONAL_FIELDS else _clean(value)
        if cleaned not in (None, "", ()):
            target[field] = cleaned


def _to_number(value):
    """Legge un valore che EXIF conserva come frazione."""
    if isinstance(value, tuple) and len(value) == 2:
        numerator, denominator = value
        try:
            return float(numerator) / float(denominator) if denominator else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        # IFDRational sa già come diventare un float.
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean(value):
    """Trasforma quello che restituisce Pillow in qualcosa che un documentale può contenere.

    I valori EXIF arrivano come frazioni, stringhe di byte e tuple: nessuna di
    queste sopravvive intatta a un viaggio attraverso JSON o BSON.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip("\x00").strip() or None
    if isinstance(value, str):
        return value.strip("\x00").strip() or None
    if isinstance(value, tuple):
        # Alcuni campi sono una sequenza: si tiene la prima voce che significa qualcosa.
        cleaned = [_clean(item) for item in value]
        cleaned = [item for item in cleaned if item is not None]
        return cleaned[0] if cleaned else None
    if isinstance(value, (int, float)):
        return value
    # IFDRational e qualunque altra cosa abbia un valore numerico.
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_exposure(value) -> str:
    """1/250 si legge come un tempo di scatto, 0,004 no."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return str(value)
    if seconds <= 0:
        return str(value)
    if seconds >= 1:
        return f"{seconds:g}s"
    fraction = Fraction(seconds).limit_denominator(8000)
    return f"{fraction.numerator}/{fraction.denominator}"


def _format_aperture(value) -> str:
    try:
        return f"f/{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _format_taken_at(value) -> str | None:
    """EXIF scrive 2026:07:15 18:32:10. ISO 8601 è il formato che si ordina e si filtra."""
    try:
        moment = dt.datetime.strptime(str(value).strip(), "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return moment.isoformat()


def _format_focal_length(value) -> str:
    try:
        return f"{float(value):g}mm"
    except (TypeError, ValueError):
        return str(value)
