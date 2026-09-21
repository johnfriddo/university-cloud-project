"""Il documentale su AWS: gli stessi cinque metodi di MetadataStore, su DynamoDB.

I servizi non sanno mai quale dei due hanno in mano: `build_metadata_store` ne
sceglie uno da METADATA_BACKEND, ed entrambi rispondono alle stesse chiamate
con le stesse forme. Solo due cose sono davvero diverse, e si risolvono qui.

I numeri. DynamoDB rifiuta i float di Python — vuole Decimal, per evitare gli
arrotondamenti binari — e restituisce ogni numero come Decimal. Convertiti
all'andata e al ritorno, altrimenti l'API comincerebbe a rispondere «400» invece di 400.

La ricerca. MongoDB sa confrontare «l'obiettivo contiene 85mm, comunque sia
scritto» su qualunque campo. DynamoDB no: fuori dalle chiavi offre soltanto
`contains` e `begins_with`, che distinguono le maiuscole e vengono applicati
documento per documento, dopo averli letti. Quindi:

- ogni campo cercabile è salvato anche in minuscolo, e anche il testo scritto
  dall'utente viene messo in minuscolo: è il confronto senza maiuscole che su
  MongoDB faceva un'espressione regolare;
- la lettura passa dall'indice `by_user`, così i filtri si applicano ai
  documenti di un utente invece che all'intera tabella.

Resta vero che si legge ogni documento di quell'utente. È il limite dichiarato
di questo adapter (appendice B della relazione): va bene per una libreria
personale, ed è la ragione per cui un sistema reale gli affiancherebbe un
motore di ricerca.
"""

import datetime as dt
import logging
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

log = logging.getLogger(__name__)

USER_INDEX = "by_user"

# Le copie in minuscolo dei campi cercabili, salvate accanto al documento.
# Il prefisso fa sì che non possano mai scontrarsi con un campo del documento.
SEARCH_FIELDS = {
    "make": "search_make",
    "model": "search_model",
    "lens": "search_lens",
    "focal_length": "search_focal_length",
}
SEARCH_ISO = "search_iso"


class DynamoMetadataStore:
    def __init__(self, table_name: str, region: str | None = None, table=None):
        # `table` serve ai test, che passano una controfigura al posto di AWS.
        # La regione, se non indicata, la trova la libreria da sé: su EKS il
        # meccanismo IRSA mette AWS_REGION in ogni pod.
        self._table = table or boto3.resource("dynamodb", region_name=region or None).Table(
            table_name
        )

    def ensure_indexes(self) -> None:
        """Niente da fare: l'indice è dichiarato in Terraform, insieme alla tabella."""

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
        item = {
            "asset_id": asset_id,
            "user_id": user_id,
            "mime": mime,
            "exif": exif,
            "dimensions": dimensions,
            "checksum": checksum,
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
            **search_attributes(exif),
        }
        # put_item sostituisce l'intero elemento con la stessa chiave: rielaborare
        # riscrive il documento, non lo duplica mai — come replace_one con
        # inserimento condizionale su MongoDB.
        self._table.put_item(Item=to_dynamo(item))
        log.info("metadati salvati per l'asset %s (%s campi EXIF)", asset_id, len(exif))

    def get(self, asset_id: str) -> dict | None:
        item = self._table.get_item(Key={"asset_id": asset_id}).get("Item")
        return from_dynamo(item) if item is not None else None

    def delete_asset_metadata(self, asset_id: str) -> None:
        """Silenziosa quando non c'è niente da cancellare, come la versione per MongoDB."""
        self._table.delete_item(Key={"asset_id": asset_id})

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
        """Gli identificativi delle immagini dell'utente i cui metadati combaciano con i filtri.

        A DynamoDB non si passa nessun `Limit`, di proposito: là significa elementi
        **letti**, non elementi che combaciano, e una pagina di venti elementi letti
        potrebbe non contenerne nessuno buono mentre la successiva li contiene tutti.
        Le pagine si seguono finché non si sono raccolti abbastanza identificativi o
        finché i documenti dell'utente non finiscono.
        """
        request = {
            "IndexName": USER_INDEX,
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "ProjectionExpression": "asset_id",
        }
        condition = search_condition(
            camera=camera,
            lens=lens,
            focal_length=focal_length,
            iso_min=iso_min,
            iso_max=iso_max,
        )
        if condition is not None:
            request["FilterExpression"] = condition

        ids: list[str] = []
        while True:
            page = self._table.query(**request)
            ids.extend(item["asset_id"] for item in page.get("Items", []))
            if len(ids) >= limit or "LastEvaluatedKey" not in page:
                return ids[:limit]
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def check(self) -> None:
        """Solleva un errore se la tabella è irraggiungibile, o se questo ruolo non può usarla."""
        self._table.load()

    def close(self) -> None:
        """Niente da chiudere: la libreria non tiene aperta nessuna connessione fra due chiamate."""


def search_attributes(exif: dict) -> dict:
    """Le copie in minuscolo su cui gira la ricerca. I campi assenti restano assenti."""
    attributes = {}
    for field, stored_as in SEARCH_FIELDS.items():
        value = exif.get(field)
        if isinstance(value, str) and value.strip():
            attributes[stored_as] = value.strip().lower()
    if isinstance(exif.get("iso"), int | float):
        attributes[SEARCH_ISO] = exif["iso"]
    return attributes


def search_condition(*, camera, lens, focal_length, iso_min, iso_max):
    """L'espressione di filtro per i filtri dati, o None quando non ce n'è nessuno.

    Le stesse regole della versione per MongoDB: la fotocamera si confronta con la
    marca **o** con il modello, la focale è ancorata all'inizio («50» trova 50mm,
    non 150mm), i limiti di ISO sono inclusi e ciascuno è facoltativo.
    """
    parts = []
    if camera and camera.strip():
        text = camera.strip().lower()
        parts.append(
            Attr(SEARCH_FIELDS["make"]).contains(text) | Attr(SEARCH_FIELDS["model"]).contains(text)
        )
    if lens and lens.strip():
        parts.append(Attr(SEARCH_FIELDS["lens"]).contains(lens.strip().lower()))
    if focal_length and focal_length.strip():
        parts.append(Attr(SEARCH_FIELDS["focal_length"]).begins_with(focal_length.strip().lower()))
    if iso_min is not None and iso_max is not None:
        parts.append(Attr(SEARCH_ISO).between(iso_min, iso_max))
    elif iso_min is not None:
        parts.append(Attr(SEARCH_ISO).gte(iso_min))
    elif iso_max is not None:
        parts.append(Attr(SEARCH_ISO).lte(iso_max))

    if not parts:
        return None
    condition = parts[0]
    for part in parts[1:]:
        condition = condition & part
    return condition


def to_dynamo(value):
    """I float diventano Decimal, passando dal testo: così 2,8 resta 2,8 e non 2,79999…"""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_dynamo(item) for item in value]
    return value


def from_dynamo(value):
    """Da Decimal a intero quando è intero, a float altrimenti."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_dynamo(item) for item in value]
    return value
