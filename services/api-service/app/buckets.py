"""Il comando autonomo per i bucket: `python -m app.buckets`.

Crea i due bucket che l'applicazione si aspetta, poi esce. Come `app.migrate`,
gira come container a sé, breve, prima che i servizi partano: in compose come
servizio usa e getta, in Kubernetes come Job, mentre su AWS gli stessi due
bucket li crea Terraform.

Legge soltanto le variabili dell'object storage, non l'intera configurazione:
questo comando non ha alcun bisogno di un database o di un broker, e chiederne
gli indirizzi lo farebbe fallire per motivi che non c'entrano col suo lavoro.

L'attesa è deliberata. L'archivio e questo comando partono nello stesso
momento, e l'archivio è il più lento dei due; uscire alla prima connessione
rifiutata sposterebbe solo il nuovo tentativo su chi riavvia il container.
"""

import logging
import os
import sys
import time

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from media_common.env import storage_credentials

log = logging.getLogger("buckets")

WAIT_TIMEOUT_SECONDS = 180
WAIT_INTERVAL_SECONDS = 3


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Tutte e tre vuote su AWS, e nessuna delle tre è un errore: l'indirizzo lo
    # conosce già la libreria (è S3 vero, non MinIO) e le credenziali arrivano
    # dal ruolo assunto dal pod. `required` impedirebbe al comando di partire
    # proprio nell'ambiente per cui è stato pensato.
    access_key, secret_key = storage_credentials("S3_ACCESS_KEY", "S3_SECRET_KEY")

    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_INTERNAL", "").strip() or None,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name=os.getenv("S3_REGION", "us-east-1"),
        config=BotoConfig(
            s3={"addressing_style": os.getenv("S3_ADDRESSING_STYLE", "path")},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=5,
            retries={"max_attempts": 1},
        ),
    )

    buckets = [
        os.getenv("S3_BUCKET_ORIGINALS", "originals"),
        os.getenv("S3_BUCKET_DERIVED", "derived"),
    ]

    if not _wait_for_storage(client):
        log.error("object storage irraggiungibile dopo %s secondi", WAIT_TIMEOUT_SECONDS)
        return 1

    for bucket in buckets:
        _create(client, bucket)

    log.info("bucket pronti: %s", ", ".join(buckets))
    return 0


def _wait_for_storage(client) -> bool:
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        try:
            client.list_buckets()
            return True
        except (ClientError, BotoCoreError) as exc:
            if time.monotonic() >= deadline:
                log.error("ultimo errore: %s", exc)
                return False
            log.info("object storage non ancora pronto, riprovo")
            time.sleep(WAIT_INTERVAL_SECONDS)


def _create(client, bucket: str) -> None:
    """Crea il bucket, e non dice niente se esiste già.

    Rieseguire questo comando dev'essere innocuo: gira di nuovo a ogni
    installazione, e su un sistema già installato i bucket ci sono tutti.
    """
    try:
        client.create_bucket(Bucket=bucket)
        log.info("creato: %s", bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            log.info("già presente: %s", bucket)
            return
        raise


if __name__ == "__main__":
    sys.exit(main())
