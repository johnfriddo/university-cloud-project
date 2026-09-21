"""Registrazione, consegna del caricamento, pubblicazione dei job e interrogazioni della libreria.

Il file non passa mai da questo servizio: il client chiede il permesso, carica
direttamente sull'object storage e torna a dire che ha finito. Fare da tramite
per decine di megabyte a immagine trasformerebbe l'api-service in una
strozzatura per un lavoro a cui non aggiunge niente.

Ogni query filtra sull'identificativo di chi chiama. È tutto il modello di
autorizzazione: non esiste un percorso di codice che raggiunga le righe di un altro utente.
"""

import datetime as dt
import logging
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, jsonify, request

from app.assets.serializers import serialize_asset, serialize_variant
from app.auth.security import require_auth
from app.db.pool import get_pool
from app.errors import error_response
from app.messaging import get_queue
from app.metadata import get_metadata_store
from app.storage import get_storage

log = logging.getLogger(__name__)

bp = Blueprint("assets", __name__)

EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_FILENAME_LENGTH = 255
VALID_STATUSES = ("PENDING", "PROCESSING", "DONE", "FAILED")

# Sentinella: distingue «i filtri sono malformati» da «non ci sono filtri».
INVALID_FILTER = object()


@bp.post("/assets")
@require_auth
def create_asset():
    """Registra l'immagine e restituisce un link firmato per caricare il file."""
    settings = current_app.config["SETTINGS"]
    payload = request.get_json(silent=True) or {}

    filename = _clean_filename(str(payload.get("filename") or ""))
    mime = str(payload.get("mime") or "").strip().lower()
    size_bytes = payload.get("size_bytes")

    if not filename:
        return error_response(400, "missing_filename", "Nome del file mancante")
    if mime not in settings.allowed_mime_types or mime not in EXTENSION_BY_MIME:
        return error_response(
            415,
            "unsupported_type",
            "Formato non supportato: sono ammessi JPEG, PNG e WebP",
        )
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        return error_response(400, "invalid_size", "Dimensione del file non valida")
    if size_bytes > settings.max_upload_bytes:
        return error_response(
            413,
            "file_too_large",
            f"Il file supera il limite di {_megabytes(settings.max_upload_bytes)} MB",
        )

    # L'identificativo si genera qui perché fa parte della chiave dell'archivio, che
    # la riga deve già portare. La chiave non contiene mai il nome scelto
    # dall'utente: quel nome è testo suo, e lasciargli disegnare un percorso è un guaio.
    asset_id = uuid4()
    storage_key = f"{g.current_user_id}/{asset_id}.{EXTENSION_BY_MIME[mime]}"

    with get_pool().connection() as conn:
        asset = conn.execute(
            """
            INSERT INTO assets (id, user_id, original_key, filename, mime, size_bytes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, status, created_at
            """,
            (asset_id, g.current_user_id, storage_key, filename, mime, size_bytes),
        ).fetchone()

    storage = get_storage()
    upload_url = storage.presigned_upload_url(
        bucket=storage.originals,
        key=storage_key,
        content_type=mime,
        expires_in=settings.upload_url_ttl_seconds,
    )

    log.info("asset %s registrato per l'utente %s", asset_id, g.current_user_id)
    return (
        jsonify(
            {
                "asset_id": str(asset["id"]),
                "status": asset["status"],
                "created_at": asset["created_at"].isoformat(),
                "upload": {
                    "url": upload_url,
                    "method": "PUT",
                    # Il client deve mandare esattamente questo: il tipo di contenuto fa parte
                    # di ciò che la firma copre.
                    "headers": {"Content-Type": mime},
                    "expires_in": settings.upload_url_ttl_seconds,
                },
            }
        ),
        201,
    )


@bp.post("/assets/<uuid:asset_id>/complete")
@require_auth
def complete_upload(asset_id: UUID):
    """Conferma il caricamento e accoda il job di elaborazione.

    Risponde 202: il lavoro è accettato, non fatto. Tutto quello che viene dopo
    succede nel worker, e il client lo segue interrogando lo stato.
    """
    settings = current_app.config["SETTINGS"]
    storage = get_storage()

    with get_pool().connection() as conn:
        asset = conn.execute(
            """
            SELECT id, original_key, status, submitted_at
              FROM assets
             WHERE id = %s AND user_id = %s
            """,
            (asset_id, g.current_user_id),
        ).fetchone()

    if asset is None:
        # 404 e non 403: un identificativo che appartiene a qualcun altro dev'essere
        # indistinguibile da un identificativo che non esiste.
        return error_response(404, "asset_not_found", "Immagine non trovata")
    # PENDING da solo non significa «non ancora accodato»: è submitted_at a
    # distinguere una registrazione appena fatta da una già consegnata al broker.
    if asset["submitted_at"] is not None or asset["status"] != "PENDING":
        return error_response(
            409, "already_submitted", "Questa immagine è già stata inviata in elaborazione"
        )

    # La dimensione dichiarata era solo un primo filtro: questo è quello vero,
    # misurato su ciò che è davvero arrivato nel bucket.
    stored = storage.head(storage.originals, asset["original_key"])
    if stored is None:
        return error_response(
            409, "upload_missing", "Il file non risulta caricato: riprova l'upload"
        )

    if stored["size_bytes"] > settings.max_upload_bytes:
        storage.delete(storage.originals, asset["original_key"])
        message = f"Il file supera il limite di {_megabytes(settings.max_upload_bytes)} MB"
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE assets
                   SET status = 'FAILED', error_message = %s, updated_at = now()
                 WHERE id = %s
                """,
                (message, asset_id),
            )
        log.warning("asset %s rifiutato: %s byte", asset_id, stored["size_bytes"])
        return error_response(413, "file_too_large", message)

    # Si prende l'immagine prima di pubblicare, così due chiamate che arrivano
    # insieme non possono raggiungere entrambe il broker. La presa viene
    # sciolta se la pubblicazione fallisce.
    with get_pool().connection() as conn:
        claimed = conn.execute(
            """
            UPDATE assets
               SET size_bytes = %s, submitted_at = now(), updated_at = now()
             WHERE id = %s AND submitted_at IS NULL
            RETURNING id
            """,
            (stored["size_bytes"], asset_id),
        ).fetchone()

    if claimed is None:
        return error_response(
            409, "already_submitted", "Questa immagine è già stata inviata in elaborazione"
        )

    try:
        get_queue().publish_job(
            asset_id=asset_id,
            storage_key=asset["original_key"],
            user_id=g.current_user_id,
        )
    except Exception:
        # All'utente non è stato promesso niente: si scioglie la presa, così il client
        # può richiamare questo endpoint quando il broker è tornato.
        log.exception("pubblicazione del job fallita per l'asset %s", asset_id)
        with get_pool().connection() as conn:
            conn.execute(
                "UPDATE assets SET submitted_at = NULL, updated_at = now() WHERE id = %s",
                (asset_id,),
            )
        return error_response(
            503, "queue_unavailable", "Servizio momentaneamente non disponibile, riprova"
        )

    return jsonify({"asset_id": str(asset_id), "status": "PENDING"}), 202


@bp.post("/assets/<uuid:asset_id>/retry")
@require_auth
def retry_asset(asset_id: UUID):
    """Rimette un'immagine in coda.

    Due situazioni lo giustificano. Quella ovvia è FAILED: l'utente ha visto
    l'errore e ha chiesto un altro giro. La seconda è un'immagine che non ha mai
    raggiunto uno stato finale — di solito perché il job ha esaurito i tentativi
    mentre il database era irraggiungibile, quindi nessuno ha potuto nemmeno
    registrare il fallimento. Nel broker non è rimasto nessun messaggio, e nessun
    meccanismo automatico può riportarlo: l'unica via d'uscita è che l'utente richieda.
    """
    settings = current_app.config["SETTINGS"]

    with get_pool().connection() as conn:
        asset = conn.execute(
            """
            SELECT id, original_key, status, submitted_at
              FROM assets
             WHERE id = %s AND user_id = %s
            """,
            (asset_id, g.current_user_id),
        ).fetchone()

    if asset is None:
        return error_response(404, "asset_not_found", "Immagine non trovata")

    refusal = _retry_refusal(asset, settings.stale_job_seconds)
    if refusal is not None:
        return refusal

    storage = get_storage()
    if storage.head(storage.originals, asset["original_key"]) is None:
        return error_response(
            409, "upload_missing", "Il file originale non è più disponibile"
        )

    # La stessa presa di complete_upload: solo la chiamata che muove davvero
    # l'immagine arriva a pubblicare. La condizione è ripetuta qui, in SQL, così
    # due clic simultanei non possono raggiungere entrambi il broker.
    with get_pool().connection() as conn:
        claimed = conn.execute(
            """
            UPDATE assets
               SET status = 'PENDING', error_message = NULL,
                   submitted_at = now(), updated_at = now()
             WHERE id = %s
               AND (
                     status = 'FAILED'
                     OR (status IN ('PENDING', 'PROCESSING')
                         AND submitted_at IS NOT NULL
                         AND submitted_at < now() - make_interval(secs => %s))
                   )
            RETURNING id
            """,
            (asset_id, settings.stale_job_seconds),
        ).fetchone()

    if claimed is None:
        return error_response(
            409, "not_retryable", "L'immagine non può essere rielaborata in questo momento"
        )

    try:
        get_queue().publish_job(
            asset_id=asset_id,
            storage_key=asset["original_key"],
            user_id=g.current_user_id,
        )
    except Exception:
        log.exception("ripubblicazione del job fallita per l'asset %s", asset_id)
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE assets
                   SET status = 'FAILED', submitted_at = NULL, updated_at = now()
                 WHERE id = %s
                """,
                (asset_id,),
            )
        return error_response(
            503, "queue_unavailable", "Servizio momentaneamente non disponibile, riprova"
        )

    return jsonify({"asset_id": str(asset_id), "status": "PENDING"}), 202


@bp.get("/assets")
@require_auth
def list_assets():
    """La libreria: una pagina delle immagini di chi chiama, dalla più recente."""
    settings = current_app.config["SETTINGS"]

    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = int(request.args.get("page_size", settings.default_page_size))
    except ValueError:
        return error_response(400, "invalid_pagination", "Parametri di paginazione non validi")
    page_size = max(1, min(page_size, settings.max_page_size))

    # Solo ciò che è stato davvero consegnato. Una riga il cui file non è mai
    # arrivato è una registrazione, non un'immagine: mostrarla metterebbe nella
    # libreria qualcosa che non avrà mai una miniatura, che nessun worker prenderà
    # mai in carico e su cui l'utente non può agire. `python -m app.cleanup` le rimuove.
    conditions = ["user_id = %s", "submitted_at IS NOT NULL"]
    params: list = [g.current_user_id]

    status = request.args.get("status")
    if status:
        status = status.upper()
        if status not in VALID_STATUSES:
            return error_response(400, "invalid_status", "Stato non valido")
        conditions.append("status = %s")
        params.append(status)

    filename = request.args.get("filename")
    if filename:
        conditions.append("filename ILIKE %s")
        # L'utente scrive un frammento, non un modello: % e _ vengono protetti così
        # da essere confrontati alla lettera invece di comportarsi da jolly.
        params.append(f"%{_escape_like(filename)}%")

    for key, comparison in (("from", ">="), ("to", "<")):
        raw = request.args.get(key)
        if not raw:
            continue
        parsed = _parse_date(raw)
        if parsed is None:
            return error_response(400, "invalid_date", f"Data '{key}' non valida, usa AAAA-MM-GG")
        if key == "to":
            # Inclusivo su tutto il giorno che l'utente ha chiesto.
            parsed = parsed + dt.timedelta(days=1)
        conditions.append(f"created_at {comparison} %s")
        params.append(parsed)

    # I filtri tecnici vivono nel documentale, tutto il resto nel relazionale. I
    # due si uniscono sugli identificativi: il documentale risponde «quali immagini
    # combaciano», SQL risponde «in quale ordine e quale pagina».
    technical = _technical_filters(request.args)
    truncated = False
    if technical is INVALID_FILTER:
        return error_response(400, "invalid_iso", "Valore ISO non valido")
    if technical:
        try:
            matches = get_metadata_store().search_asset_ids(
                user_id=g.current_user_id, limit=settings.metadata_match_limit, **technical
            )
        except Exception:
            log.exception("ricerca sui metadati non riuscita")
            return error_response(
                503, "metadata_unavailable", "Ricerca sui dati tecnici non disponibile"
            )
        truncated = len(matches) >= settings.metadata_match_limit
        if truncated:
            log.warning("ricerca sui metadati troncata a %s risultati", len(matches))
        conditions.append("id = ANY(%s::uuid[])")
        params.append(matches)

    where = " AND ".join(conditions)

    with get_pool().connection() as conn:
        total = conn.execute(
            f"SELECT count(*) AS total FROM assets WHERE {where}", params
        ).fetchone()["total"]

        rows = conn.execute(
            f"""
            SELECT id, filename, mime, size_bytes, status, error_message,
                   created_at, updated_at
              FROM assets
             WHERE {where}
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()

        variants_by_asset = _load_variants(conn, [row["id"] for row in rows])

    items = [
        serialize_asset(row, variants_by_asset.get(row["id"], [])) for row in rows
    ]
    body = {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }
    if truncated:
        # Dirlo ad alta voce è meglio che restituire in silenzio una risposta parziale
        # come se fosse quella completa.
        body["truncated"] = True
    return jsonify(body)


@bp.get("/assets/status")
@require_auth
def assets_status():
    """Gli stati delle immagini che il client sta guardando, e nient'altro.

    Il frontend interroga ogni due secondi finché tutto ciò che è sullo schermo
    raggiunge uno stato finale. Interrogare l'intera libreria sarebbe sbagliato
    due volte: rifirmerebbe ogni link di scaricamento a ogni giro, e poiché una
    firma nuova produce un indirizzo diverso, ogni miniatura della griglia
    verrebbe riscaricata due secondi dopo l'ultima volta.

    Qui non ci sono link: solo quello che cambia davvero.
    """
    settings = current_app.config["SETTINGS"]

    raw_ids = (request.args.get("ids") or "").split(",")
    ids = []
    for raw in raw_ids:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.append(UUID(raw))
        except ValueError:
            return error_response(400, "invalid_id", "Identificativo non valido")

    if not ids:
        return error_response(400, "missing_ids", "Nessun identificativo indicato")
    if len(ids) > settings.max_page_size:
        return error_response(
            400,
            "too_many_ids",
            f"Massimo {settings.max_page_size} immagini per richiesta",
        )

    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, error_message, updated_at
              FROM assets
             WHERE user_id = %s AND id = ANY(%s)
            """,
            (g.current_user_id, ids),
        ).fetchall()

    return jsonify(
        {
            "items": [
                {
                    "id": str(row["id"]),
                    "status": row["status"],
                    "error_message": row["error_message"],
                    "updated_at": row["updated_at"].isoformat(),
                }
                for row in rows
            ]
        }
    )


@bp.get("/assets/<uuid:asset_id>")
@require_auth
def get_asset(asset_id: UUID):
    """Pagina di dettaglio: l'immagine, le sue varianti e un link all'originale."""
    settings = current_app.config["SETTINGS"]
    storage = get_storage()

    with get_pool().connection() as conn:
        asset = conn.execute(
            """
            SELECT id, filename, mime, size_bytes, status, error_message,
                   original_key, created_at, updated_at
              FROM assets
             WHERE id = %s AND user_id = %s
            """,
            (asset_id, g.current_user_id),
        ).fetchone()

        if asset is None:
            return error_response(404, "asset_not_found", "Immagine non trovata")

        # Il nome dato dall'utente, senza estensione: le varianti sono tutte JPEG
        # qualunque fosse l'originale, quindi «tramonto.png» salva la propria
        # versione media come «tramonto-medium.jpg».
        save_as_base = PurePosixPath(asset["filename"]).stem or "immagine"
        variants_by_asset = _load_variants(conn, [asset["id"]], save_as_base)

    original_url = storage.presigned_download_url(
        storage.originals, asset["original_key"], settings.download_url_ttl_seconds
    )
    # L'originale conserva il nome con cui è stato caricato, estensione compresa.
    original_download_url = storage.presigned_download_url(
        storage.originals,
        asset["original_key"],
        settings.download_url_ttl_seconds,
        save_as=asset["filename"],
    )
    body = serialize_asset(asset, variants_by_asset.get(asset["id"], []), original_url)
    body["original_download_url"] = original_download_url
    body["metadata"] = _technical_metadata(str(asset["id"]))
    return jsonify(body)


@bp.delete("/assets/<uuid:asset_id>")
@require_auth
def delete_asset(asset_id: UUID):
    """Rimuove un'immagine e tutto ciò che le appartiene.

    Tre archivi devono dimenticarla: il relazionale (la riga, e con lei le
    varianti e gli avvisi, che cadono per cascata), l'object storage (l'originale
    e le tre varianti) e il documentale (i metadati tecnici).
    metadata).

    **L'ordine è deliberato.** Prima la riga, poi i file. Se si togliessero prima
    i file e la cancellazione della riga fallisse, la libreria resterebbe a
    mostrare un'immagine il cui file non esiste più — rotta da guardare, e
    impossibile da sistemare dall'interfaccia. Così invece un guasto a metà strada
    lascia nel bucket oggetti che nessuno riferisce: invisibili, e ripulibili.
    """
    settings = current_app.config["SETTINGS"]

    with get_pool().connection() as conn:
        asset = conn.execute(
            """
            SELECT id, status, original_key, submitted_at
              FROM assets
             WHERE id = %s AND user_id = %s
            """,
            (asset_id, g.current_user_id),
        ).fetchone()

        if asset is None:
            # È anche la risposta quando l'immagine appartiene a qualcun altro: dire
            # a uno sconosciuto «esiste ma non è tua» è dirgli che
            # esiste.
            return error_response(404, "asset_not_found", "Immagine non trovata")

        refusal = _delete_refusal(asset, settings.stale_job_seconds)
        if refusal is not None:
            return refusal

        variant_keys = [
            row["storage_key"]
            for row in conn.execute(
                "SELECT storage_key FROM variants WHERE asset_id = %s", (asset_id,)
            ).fetchall()
        ]

        # Varianti e avvisi hanno ON DELETE CASCADE: basta un'istruzione sola.
        conn.execute(
            "DELETE FROM assets WHERE id = %s AND user_id = %s",
            (asset_id, g.current_user_id),
        )

    storage = get_storage()
    for key in variant_keys:
        _forget_quietly(lambda k=key: storage.delete(storage.derived, k), "variante", key)
    _forget_quietly(
        lambda: storage.delete(storage.originals, asset["original_key"]),
        "originale",
        asset["original_key"],
    )
    _forget_quietly(
        lambda: get_metadata_store().delete_asset_metadata(str(asset_id)),
        "metadati",
        str(asset_id),
    )

    log.info("immagine %s eliminata con %s varianti", asset_id, len(variant_keys))
    return "", 204


def _delete_refusal(asset, stale_after_seconds: int):
    """Spiega perché un'immagine non si può rimuovere adesso, o None se si può.

    Un caso solo rifiuta: un'immagine che un worker ha in questo momento fra le
    mani. Cancellarla lì farebbe fallire il worker mentre scrive le varianti di
    una riga che non esiste più, e quel messaggio finirebbe nella coda dei
    rifiutati — molto rumore per un'immagine che l'utente voleva solo togliere.

    L'attesa è breve e finisce da sola: un'immagine piantata in elaborazione
    diventa stantia e viene ripresa.
    """
    if asset["status"] != "PROCESSING" or asset["submitted_at"] is None:
        return None

    waiting_for = dt.datetime.now(dt.UTC) - asset["submitted_at"]
    if waiting_for.total_seconds() < stale_after_seconds:
        return error_response(
            409,
            "still_processing",
            "L'immagine è in elaborazione: riprova fra qualche secondo",
        )
    return None


def _forget_quietly(remove, what: str, reference: str) -> None:
    """Rimuove un resto, e annota un fallimento invece di sollevarlo.

    Quando questi girano la riga è già sparita, quindi per l'utente l'immagine
    è stata cancellata. Trasformare un file rimasto indietro in un errore
    segnalerebbe un fallimento per qualcosa che invece è riuscito.
    """
    try:
        remove()
    except Exception as exc:
        log.warning("non sono riuscito a rimuovere %s %s: %s", what, reference, exc)


def _technical_metadata(asset_id: str) -> dict | None:
    """Dati EXIF, dimensioni e impronta, oppure None quando non ce ne sono ancora.

    Un guasto del documentale degrada questo solo campo invece di far fallire
    l'intera richiesta: la pagina ha comunque l'immagine, le sue varianti e il
    suo stato, che è la maggior parte di ciò per cui l'utente è venuto.
    """
    try:
        document = get_metadata_store().get(asset_id)
    except Exception:
        log.exception("metadati tecnici non leggibili per l'asset %s", asset_id)
        return None

    if document is None:
        # Normale per un'immagine che aspetta ancora di essere elaborata.
        return None
    return {
        "exif": document.get("exif", {}),
        "dimensions": document.get("dimensions", {}),
        "checksum": document.get("checksum"),
    }


def _load_variants(conn, asset_ids: list, save_as_base: str | None = None) -> dict:
    """Le varianti di più immagini in una sola query.

    Una query per immagine significherebbe venti viaggi per disegnare una pagina
    della libreria: il costo crescerebbe con la dimensione della pagina invece di restare costante.

    Con `save_as_base` ogni variante riceve anche un link che salva il file
    invece di mostrarlo, chiamato come l'originale — «tramonto-thumb.jpg». Lo
    chiede solo la pagina di dettaglio: nella libreria i link servono a riempire
    una griglia di miniature, e firmare un secondo indirizzo per ognuna di
    sessanta varianti sarebbe lavoro che nessuno usa.
    """
    if not asset_ids:
        return {}

    settings = current_app.config["SETTINGS"]
    storage = get_storage()

    rows = conn.execute(
        """
        SELECT asset_id, kind, storage_key, width, height, size_bytes
          FROM variants
         WHERE asset_id = ANY(%s)
         ORDER BY asset_id, kind
        """,
        (asset_ids,),
    ).fetchall()

    grouped: dict = {}
    for row in rows:
        url = storage.presigned_download_url(
            storage.derived, row["storage_key"], settings.download_url_ttl_seconds
        )
        download_url = None
        if save_as_base is not None:
            download_url = storage.presigned_download_url(
                storage.derived,
                row["storage_key"],
                settings.download_url_ttl_seconds,
                save_as=f"{save_as_base}-{row['kind']}.jpg",
            )
        grouped.setdefault(row["asset_id"], []).append(
            serialize_variant(row, url, download_url)
        )
    return grouped


def _technical_filters(args) -> dict | object | None:
    """Legge i parametri della ricerca tecnica, o None quando non ce ne sono."""
    filters: dict = {}

    for name in ("camera", "lens", "focal_length"):
        value = (args.get(name) or "").strip()
        if value:
            filters[name] = value

    for name in ("iso_min", "iso_max"):
        raw = args.get(name)
        if raw is None or raw == "":
            continue
        try:
            filters[name] = int(raw)
        except ValueError:
            return INVALID_FILTER

    return filters or None


def _retry_refusal(asset, stale_after_seconds: int):
    """Spiega perché un'immagine non può essere rimandata in lavorazione, o None se può.

    La regola è applicata anche in SQL: questo esiste per dare all'utente un
    motivo invece di un rifiuto generico.
    """
    status = asset["status"]
    if status == "FAILED":
        return None
    if status == "DONE":
        return error_response(409, "already_done", "L'immagine è già stata elaborata")
    if asset["submitted_at"] is None:
        return error_response(
            409, "upload_missing", "Il caricamento non è stato completato: ricarica il file"
        )

    waiting_for = dt.datetime.now(dt.UTC) - asset["submitted_at"]
    if waiting_for.total_seconds() < stale_after_seconds:
        return error_response(
            409, "still_processing", "L'immagine è ancora in elaborazione, attendi"
        )
    return None


def _clean_filename(raw: str) -> str:
    """Conserva il nome che l'utente vede, ripulito da tutto ciò che somiglia a un percorso."""
    name = PurePosixPath(raw.strip().replace("\\", "/")).name
    return name[:MAX_FILENAME_LENGTH]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_date(raw: str) -> dt.datetime | None:
    try:
        day = dt.date.fromisoformat(raw.strip())
    except ValueError:
        return None
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)


def _megabytes(value: int) -> int:
    return value // (1024 * 1024)
