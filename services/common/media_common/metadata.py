"""L'adapter del documentale: i metadati tecnici di ogni immagine.

MongoDB qui, DynamoDB su AWS. Non è importato da media_common/__init__.py di
proposito: pymongo è una dipendenza facoltativa di questo pacchetto, così un
servizio che non tocca i metadati non si porta dietro il driver.

Un documento per immagine, con l'identificativo come chiave. La scrittura è una
sostituzione con inserimento condizionale, che rende innocua la rielaborazione:
il documento viene riscritto, mai duplicato.
"""

import datetime as dt
import logging
import re

from pymongo import ASCENDING, MongoClient

log = logging.getLogger(__name__)

SERVER_SELECTION_TIMEOUT_MS = 5000
COLLECTION_NAME = "asset_metadata"


class MetadataStore:
    def __init__(self, url: str, database: str, collection: str = COLLECTION_NAME):
        self._client = MongoClient(
            url,
            serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            tz_aware=True,
        )
        self._collection = self._client[database][collection]

    def ensure_indexes(self) -> None:
        """La libreria filtra per proprietario, quindi quel campo ha bisogno di un indice.

        Su DynamoDB questo diventa un indice secondario dichiarato in Terraform: il
        bisogno è lo stesso, cambia solo il modo di esprimerlo.
        """
        self._collection.create_index([("user_id", ASCENDING)])

    def save_asset_metadata(
        self,
        *,
        asset_id: str,
        user_id: str,
        mime: str,
        exif: dict,
        dimensions: dict,
        checksum: str,
    ) -> None:
        document = {
            "_id": asset_id,
            "asset_id": asset_id,
            # La ricerca nella libreria è per utente: senza questo campo l'interrogazione
            # dovrebbe chiedere al relazionale quali immagini guardare.
            "user_id": user_id,
            "mime": mime,
            "exif": exif,
            "dimensions": dimensions,
            "checksum": checksum,
            "updated_at": dt.datetime.now(dt.UTC),
        }
        self._collection.replace_one({"_id": asset_id}, document, upsert=True)
        log.info("metadati salvati per l'asset %s (%s campi EXIF)", asset_id, len(exif))

    def get(self, asset_id: str) -> dict | None:
        return self._collection.find_one({"_id": asset_id})

    def delete_asset_metadata(self, asset_id: str) -> None:
        """Dimentica i metadati tecnici di un'immagine.

        Silenziosa quando non c'è niente da dimenticare: un'immagine fallita prima
        che il worker arrivasse ai dati EXIF qui non ha nessun documento, e
        cancellarla due volte dev'essere innocuo quanto cancellarla una volta.
        """
        removed = self._collection.delete_one({"_id": asset_id}).deleted_count
        if removed:
            log.info("metadati rimossi per l'asset %s", asset_id)

    def search_asset_ids(
        self,
        *,
        user_id: str,
        camera: str | None = None,
        lens: str | None = None,
        focal_length: str | None = None,
        iso_min: int | None = None,
        iso_max: int | None = None,
        limit: int,
    ) -> list[str]:
        """Gli identificativi delle immagini dell'utente i cui metadati tecnici
        combaciano con i filtri.

        Restituisce identificativi e non documenti: la libreria viene ordinata e
        paginata dal relazionale, l'unico che sa di stati, date e nomi di file.
        Questa è la lista di identificativi su cui le due metà dell'interrogazione
        vengono unite.
        """
        query: dict = {"user_id": user_id}

        if camera:
            # Confrontato sia con la marca sia con il modello: l'utente scrive «Canon» o
            # «EOS R6» senza preoccuparsi di quale dei due campi lo contenga.
            pattern = _contains(camera)
            query["$or"] = [{"exif.make": pattern}, {"exif.model": pattern}]
        if lens:
            query["exif.lens"] = _contains(lens)
        if focal_length:
            query["exif.focal_length"] = _starts_with(focal_length)
        if iso_min is not None or iso_max is not None:
            bounds = {}
            if iso_min is not None:
                bounds["$gte"] = iso_min
            if iso_max is not None:
                bounds["$lte"] = iso_max
            query["exif.iso"] = bounds

        cursor = self._collection.find(query, {"_id": 1}).limit(limit)
        return [document["_id"] for document in cursor]

    def check(self) -> None:
        """Solleva un errore se il documentale è irraggiungibile. Usato dalle sonde di prontezza."""
        self._client.admin.command("ping")

    def close(self) -> None:
        self._client.close()


def _contains(value: str) -> dict:
    """Un «contiene» che ignora le maiuscole, con il testo dell'utente preso alla lettera.

    re.escape non è facoltativo: una ricerca di «f(1» verrebbe altrimenti compilata
    come espressione regolare, e una scritta ad arte può essere resa abbastanza
    costosa da tenere occupato il database per moltissimo tempo.
    """
    return {"$regex": re.escape(value.strip()), "$options": "i"}


def _starts_with(value: str) -> dict:
    """Lo stesso, ma ancorato all'inizio.

    La focale è salvata come la scrive la fotocamera — «50mm» — e nessuno digita
    l'unità di misura quando cerca. Un semplice «contiene» funzionerebbe, ma una
    ricerca di 50 riporterebbe anche ogni scatto a 150mm, che non è quello che
    l'utente ha chiesto. L'ancoraggio fa sì che «50» trovi solo «50mm» e «50 mm».
    """
    return {"$regex": "^" + re.escape(value.strip()), "$options": "i"}
