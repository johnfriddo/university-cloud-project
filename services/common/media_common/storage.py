"""L'adapter dell'object storage.

Due client, di proposito:

- `_internal` parla all'indirizzo raggiungibile dall'interno del cluster e serve
  alle chiamate che un servizio fa per sé;
- `_public` firma i collegamenti destinati al browser. Quelli devono puntare a
  un indirizzo che il browser sappia risolvere: firmare con l'indirizzo interno
  produrrebbe link validi per noi e per nessuno là fuori.

Il costruttore prende valori semplici invece di un oggetto di configurazione:
questo pacchetto è condiviso, e non deve dipendere da come un singolo servizio
ha scelto di organizzare la propria configurazione.
"""

import logging
import re
import unicodedata
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5


# I caratteri che un nome di file può conservare nella forma semplice
# dell'header. Tutto il resto — un accento, uno spazio, una virgoletta — lì
# viene sostituito, e viaggia intatto nella forma moderna.
_PLAIN_NAME = re.compile(r"[^A-Za-z0-9._-]")


def content_disposition(filename: str) -> str:
    """L'header che dice al browser di salvare il file, e con quale nome.

    Due nomi di proposito, come prescrive lo standard: uno semplice che qualunque
    client capisce, e uno codificato per chi lo sa leggere. Un nome come
    «città al tramonto.jpg» arriverebbe altrimenti storpiato, o romperebbe
    l'header del tutto se contenesse una virgoletta.
    """
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    plain = _PLAIN_NAME.sub("_", ascii_name).strip("._") or "immagine"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(filename, safe='')}"


class ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_internal: str,
        endpoint_public: str,
        access_key: str,
        secret_key: str,
        region: str,
        bucket_originals: str,
        bucket_derived: str,
        addressing_style: str = "path",
    ):
        self.originals = bucket_originals
        self.derived = bucket_derived
        common = {
            "access_key": access_key,
            "secret_key": secret_key,
            "region": region,
            "addressing_style": addressing_style,
        }
        self._internal = self._build_client(endpoint=endpoint_internal, **common)
        self._public = self._build_client(endpoint=endpoint_public, **common)

    @staticmethod
    def _build_client(
        *, endpoint: str, access_key: str, secret_key: str, region: str, addressing_style: str
    ):
        return boto3.client(
            "s3",
            # Vuoto su AWS, dove la libreria conosce già gli indirizzi veri di S3.
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
            config=BotoConfig(
                # MinIO si indirizza come endpoint/bucket/chiave, AWS preferisce
                # bucket.endpoint/chiave. È l'unica differenza fra i due che la
                # libreria non sa dedurre da sola.
                s3={"addressing_style": addressing_style},
                signature_version="s3v4",
                connect_timeout=REQUEST_TIMEOUT_SECONDS,
                read_timeout=REQUEST_TIMEOUT_SECONDS,
                retries={"max_attempts": 2},
            ),
        )

    def presigned_upload_url(
        self, bucket: str, key: str, content_type: str, expires_in: int
    ) -> str:
        """Un collegamento temporaneo che permette di caricare esattamente questo oggetto.

        La firma copre bucket, chiave, metodo e tipo di contenuto: nessuno di questi
        può essere alterato da chi ha il collegamento, e dopo `expires_in` secondi il
        collegamento smette del tutto di funzionare.
        """
        return self._public.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

    def presigned_download_url(
        self, bucket: str, key: str, expires_in: int, *, save_as: str | None = None
    ) -> str:
        """Un collegamento temporaneo per leggere un oggetto.

        Senza `save_as` il browser mostra l'immagine: è ciò che serve alla griglia e
        all'anteprima. Con `save_as`, il browser salva il file con quel nome invece di
        mostrarlo — lo stesso oggetto, un'istruzione diversa.

        L'istruzione viaggia dentro la firma, quindi non può essere manomessa, e
        l'archivio la restituisce come header suo.
        """
        params = {"Bucket": bucket, "Key": key}
        if save_as is not None:
            params["ResponseContentDisposition"] = content_disposition(save_as)

        return self._public.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
            HttpMethod="GET",
        )

    def head(self, bucket: str, key: str) -> dict | None:
        """I metadati di un oggetto, oppure None quando non c'è."""
        try:
            response = self._internal.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return {
            "size_bytes": response["ContentLength"],
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag", "").strip('"'),
        }

    def download_bytes(self, bucket: str, key: str) -> bytes:
        """L'oggetto intero, in memoria.

        Le immagini hanno un tetto di 25 MB e il worker ne elabora una alla volta,
        quindi tenere il file in memoria costa meno di un file temporaneo su disco.
        """
        response = self._internal.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def upload_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self._internal.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)

    def delete(self, bucket: str, key: str) -> None:
        self._internal.delete_object(Bucket=bucket, Key=key)
        log.info("oggetto rimosso: %s/%s", bucket, key)

    def check(self) -> None:
        """Solleva un errore se l'archivio è irraggiungibile. Usato dalle sonde di prontezza."""
        self._internal.head_bucket(Bucket=self.originals)
