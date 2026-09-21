"""Dà alle righe del database la forma del JSON che il frontend consuma."""


def serialize_asset(row, variants: list[dict], original_url: str | None = None) -> dict:
    asset = {
        "id": str(row["id"]),
        "filename": row["filename"],
        "mime": row["mime"],
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "error_message": row["error_message"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "variants": variants,
    }
    if original_url is not None:
        asset["original_url"] = original_url
    return asset


def serialize_variant(row, url: str, download_url: str | None = None) -> dict:
    variant = {
        "kind": row["kind"],
        "width": row["width"],
        "height": row["height"],
        "size_bytes": row["size_bytes"],
        # Firmato e temporaneo, come il link di caricamento: i bucket restano privati
        # e nessuna immagine è raggiungibile indovinandone l'indirizzo.
        "url": url,
    }
    if download_url is not None:
        # Lo stesso oggetto, firmato con l'istruzione di salvarlo con un nome
        # leggibile invece di mostrarlo. Tenuto separato da `url` perché la pagina di
        # dettaglio ha bisogno di entrambi: uno per mostrare l'immagine, uno per il
        # link che la salva.
        variant["download_url"] = download_url
    return variant
