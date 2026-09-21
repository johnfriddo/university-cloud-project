"""Quello che il worker fa davvero con un lavoro.

Scarica l'originale, genera le tre varianti e le scrive nel bucket delle
derivate, ed estrae i metadati EXIF.

La distinzione che conta per l'affidabilità è il tipo di eccezione:

- ProcessingError significa che il problema è il file. Dare lo stesso lavoro a
  un altro worker fallirebbe in modo identico, quindi l'immagine viene marcata
  FAILED e il messaggio viene confermato.
- Qualunque altra cosa significa che si è rotto qualcosa intorno a noi. Il
  lavoro è ancora valido, quindi il messaggio torna al broker.
"""

import hashlib
import logging

from worker.exif import extract_exif
from worker.images import InvalidImageError, generate_variants, open_image

log = logging.getLogger(__name__)


class ProcessingError(Exception):
    """Il file non è elaborabile. Riprovare non servirà."""


def process_asset(*, storage, settings, asset_id: str, user_id: str, storage_key: str) -> dict:
    original = _download_original(storage, storage_key)
    checksum = hashlib.sha256(original).hexdigest()

    try:
        image = open_image(original)
    except InvalidImageError as exc:
        raise ProcessingError(str(exc)) from exc

    dimensions = {"width": image.width, "height": image.height}
    exif = extract_exif(image)
    log.info(
        "originale: %sx%s, %s byte, sha256:%s…, EXIF: %s",
        image.width,
        image.height,
        len(original),
        checksum[:12],
        ", ".join(exif) if exif else "nessun campo",
    )

    try:
        variants = generate_variants(image, settings.watermark_text)
    except InvalidImageError as exc:
        raise ProcessingError(str(exc)) from exc

    stored = []
    for variant in variants:
        # Chiave deterministica: rielaborare la stessa immagine sovrascrive gli
        # stessi oggetti invece di lasciare una scia di orfani nel bucket.
        key = f"{user_id}/{asset_id}/{variant.kind}.jpg"
        storage.upload_bytes(storage.derived, key, variant.data, variant.content_type)
        stored.append(
            {
                "kind": variant.kind,
                "storage_key": key,
                "width": variant.width,
                "height": variant.height,
                "size_bytes": len(variant.data),
            }
        )

    return {
        "size_bytes": len(original),
        "checksum": f"sha256:{checksum}",
        "dimensions": dimensions,
        "exif": exif,
        "variants": stored,
    }


def _download_original(storage, storage_key: str) -> bytes:
    try:
        return storage.download_bytes(storage.originals, storage_key)
    except Exception as exc:
        # Oggetto mancante: il caricamento non è mai finito, o il file è stato
        # rimosso. Nessun numero di tentativi lo riporta indietro.
        if _is_missing(exc):
            raise ProcessingError("Il file originale non è più disponibile") from exc
        raise


def _is_missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code")
    return code in ("404", "NoSuchKey", "NotFound")
