"""La configurazione di esecuzione, letta esclusivamente da variabili d'ambiente.

Niente è scritto nel codice: docker compose le inietta da un file .env,
Kubernetes da ConfigMap e Secret, AWS da Secrets Manager. Il codice
dell'applicazione non sa mai con quale dei tre sta parlando.
"""

import os
from dataclasses import dataclass

from media_common.documents import DocumentStoreSettings
from media_common.env import MissingConfiguration, required, storage_credentials


@dataclass(frozen=True)
class Settings:
    service_name: str
    version: str
    database_url: str
    # MongoDB in locale, DynamoDB su AWS: lo decide METADATA_BACKEND.
    documents: DocumentStoreSettings
    # Il tetto agli identificativi che il documentale può restituire per una
    # ricerca. I risultati rientrano in una query SQL, e quella lista non può crescere all'infinito.
    metadata_match_limit: int
    rabbitmq_url: str
    jobs_exchange: str
    jobs_routing_key: str
    job_queue: str
    dlx_exchange: str
    dlq_queue: str
    events_exchange: str
    notifications_queue: str
    # Il browser carica direttamente sull'object storage, quindi i link firmati
    # vanno firmati sull'indirizzo che il browser sa raggiungere, che non è quello
    # che i servizi usano dentro il cluster.
    s3_endpoint_internal: str
    s3_endpoint_public: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    s3_bucket_originals: str
    s3_bucket_derived: str
    # MinIO vuole endpoint/bucket/chiave, AWS preferisce bucket.endpoint/chiave.
    s3_addressing_style: str
    # Quanto resta valido un link di caricamento firmato. Abbastanza perché una
    # connessione lenta spinga 25 MB, abbastanza poco perché un link trafugato sia inutile.
    upload_url_ttl_seconds: int
    max_upload_bytes: int
    allowed_mime_types: tuple[str, ...]
    download_url_ttl_seconds: int
    # Passato questo tempo senza raggiungere uno stato finale, un job è considerato
    # piantato e l'utente può rimandarlo in lavorazione.
    stale_job_seconds: int
    default_page_size: int
    max_page_size: int
    jwt_secret: str
    jwt_ttl_seconds: int
    # Un database gestito accetta un numero limitato di connessioni e ogni replica
    # attinge allo stesso budget, quindi la dimensione della riserva è configurabile.
    db_pool_min_size: int
    db_pool_max_size: int

    @classmethod
    def from_env(cls) -> "Settings":
        # Vuote quando le credenziali le fornisce l'ambiente: su AWS il pod
        # assume un ruolo (IRSA) e quelle variabili non esistono.
        s3_access_key, s3_secret_key = storage_credentials("S3_ACCESS_KEY", "S3_SECRET_KEY")
        return cls(
            service_name=os.getenv("SERVICE_NAME", "api-service"),
            version=os.getenv("SERVICE_VERSION", "0.1.0"),
            database_url=required("DATABASE_URL"),
            documents=DocumentStoreSettings.from_env(),
            metadata_match_limit=int(os.getenv("METADATA_MATCH_LIMIT", "2000")),
            rabbitmq_url=required("RABBITMQ_URL"),
            jobs_exchange=os.getenv("JOBS_EXCHANGE", "media.jobs"),
            jobs_routing_key=os.getenv("JOBS_ROUTING_KEY", "process"),
            job_queue=os.getenv("JOB_QUEUE", "media.process"),
            dlx_exchange=os.getenv("DLX_EXCHANGE", "media.jobs.dlx"),
            dlq_queue=os.getenv("DLQ_QUEUE", "media.process.dlq"),
            events_exchange=os.getenv("EVENTS_EXCHANGE", "media.events"),
            notifications_queue=os.getenv("NOTIFICATIONS_QUEUE", "notifications"),
            s3_endpoint_internal=os.getenv("S3_ENDPOINT_INTERNAL", ""),
            s3_endpoint_public=_public_endpoint(),
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            s3_region=os.getenv("S3_REGION", "us-east-1"),
            s3_bucket_originals=os.getenv("S3_BUCKET_ORIGINALS", "originals"),
            s3_bucket_derived=os.getenv("S3_BUCKET_DERIVED", "derived"),
            s3_addressing_style=os.getenv("S3_ADDRESSING_STYLE", "path"),
            upload_url_ttl_seconds=int(os.getenv("UPLOAD_URL_TTL_SECONDS", "900")),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
            allowed_mime_types=tuple(
                mime.strip()
                for mime in os.getenv(
                    "ALLOWED_MIME_TYPES", "image/jpeg,image/png,image/webp"
                ).split(",")
                if mime.strip()
            ),
            download_url_ttl_seconds=int(os.getenv("DOWNLOAD_URL_TTL_SECONDS", "900")),
            stale_job_seconds=int(os.getenv("STALE_JOB_SECONDS", "600")),
            default_page_size=int(os.getenv("DEFAULT_PAGE_SIZE", "20")),
            max_page_size=int(os.getenv("MAX_PAGE_SIZE", "100")),
            # Almeno 32 caratteri: sotto quella lunghezza una firma HMAC-SHA256
            # è considerata debole (RFC 7518).
            jwt_secret=required("JWT_SECRET", min_length=32),
            jwt_ttl_seconds=int(os.getenv("JWT_TTL_SECONDS", "3600")),
            db_pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "5")),
        )


def _public_endpoint() -> str:
    """L'indirizzo su cui vengono firmati i link di caricamento e di scaricamento.

    Obbligatorio quando l'archivio ha un indirizzo interno (MinIO): firmare con
    quello interno produrrebbe link validi per i servizi e per nessun browser, e
    niente protesterebbe fino al primo caricamento. Vuoto è giusto su AWS, dove
    nemmeno l'indirizzo interno esiste e la libreria firma sull'indirizzo vero
    di S3.
    """
    public = os.getenv("S3_ENDPOINT_PUBLIC", "").strip()
    if os.getenv("S3_ENDPOINT_INTERNAL", "").strip() and not public:
        raise MissingConfiguration(
            "S3_ENDPOINT_INTERNAL è impostata ma S3_ENDPOINT_PUBLIC no: con un "
            "archivio interno (MinIO) i link firmati devono puntare a un "
            "indirizzo che il browser sa raggiungere"
        )
    return public
