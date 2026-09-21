"""La configurazione di esecuzione, letta esclusivamente da variabili d'ambiente."""

import os
from dataclasses import dataclass

from media_common import JobQueue, ObjectStorage
from media_common.documents import DocumentStoreSettings
from media_common.env import required, storage_credentials
from media_common.queue import Topology


@dataclass(frozen=True)
class Settings:
    service_name: str
    version: str
    health_port: int
    database_url: str
    # MongoDB in locale, DynamoDB su AWS: lo decide METADATA_BACKEND.
    documents: DocumentStoreSettings
    rabbitmq_url: str
    jobs_exchange: str
    jobs_routing_key: str
    job_queue: str
    dlx_exchange: str
    dlq_queue: str
    events_exchange: str
    notifications_queue: str
    s3_endpoint_internal: str
    s3_endpoint_public: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    s3_addressing_style: str
    s3_bucket_originals: str
    s3_bucket_derived: str
    watermark_text: str
    # Quanti messaggi il broker consegna a un singolo worker per volta. Resta basso
    # di proposito: con un carico a raffiche il punto è distribuire i lavori su
    # molte repliche, non lasciare che una replica accaparri la coda.
    prefetch_count: int
    # I tentativi dopo un guasto infrastrutturale, prima che il job venga
    # parcheggiato nella coda dei rifiutati, come chiede la specifica.
    max_attempts: int
    retry_backoff_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        # Vuote quando le credenziali le fornisce l'ambiente: su AWS il pod
        # assume un ruolo (IRSA) e quelle variabili non esistono.
        s3_access_key, s3_secret_key = storage_credentials("S3_ACCESS_KEY", "S3_SECRET_KEY")
        return cls(
            service_name=os.getenv("SERVICE_NAME", "worker-service"),
            version=os.getenv("SERVICE_VERSION", "0.1.0"),
            health_port=int(os.getenv("HEALTH_PORT", "8000")),
            database_url=required("DATABASE_URL"),
            documents=DocumentStoreSettings.from_env(),
            rabbitmq_url=required("RABBITMQ_URL"),
            jobs_exchange=os.getenv("JOBS_EXCHANGE", "media.jobs"),
            jobs_routing_key=os.getenv("JOBS_ROUTING_KEY", "process"),
            job_queue=os.getenv("JOB_QUEUE", "media.process"),
            dlx_exchange=os.getenv("DLX_EXCHANGE", "media.jobs.dlx"),
            dlq_queue=os.getenv("DLQ_QUEUE", "media.process.dlq"),
            events_exchange=os.getenv("EVENTS_EXCHANGE", "media.events"),
            notifications_queue=os.getenv("NOTIFICATIONS_QUEUE", "notifications"),
            s3_endpoint_internal=os.getenv("S3_ENDPOINT_INTERNAL", ""),
            s3_endpoint_public=os.getenv("S3_ENDPOINT_PUBLIC", ""),
            s3_access_key=s3_access_key,
            s3_secret_key=s3_secret_key,
            s3_region=os.getenv("S3_REGION", "us-east-1"),
            s3_addressing_style=os.getenv("S3_ADDRESSING_STYLE", "path"),
            s3_bucket_originals=os.getenv("S3_BUCKET_ORIGINALS", "originals"),
            s3_bucket_derived=os.getenv("S3_BUCKET_DERIVED", "derived"),
            watermark_text=os.getenv("WATERMARK_TEXT", "© media platform"),
            prefetch_count=int(os.getenv("PREFETCH_COUNT", "1")),
            max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("RETRY_BACKOFF_SECONDS", "2")),
        )

    def build_storage(self) -> ObjectStorage:
        return ObjectStorage(
            endpoint_internal=self.s3_endpoint_internal,
            endpoint_public=self.s3_endpoint_public,
            access_key=self.s3_access_key,
            secret_key=self.s3_secret_key,
            region=self.s3_region,
            bucket_originals=self.s3_bucket_originals,
            bucket_derived=self.s3_bucket_derived,
            addressing_style=self.s3_addressing_style,
        )

    def build_queue(self) -> JobQueue:
        return JobQueue(url=self.rabbitmq_url, topology=self.topology())

    def build_metadata_store(self):
        return self.documents.build()

    def topology(self) -> Topology:
        return Topology(
            jobs_exchange=self.jobs_exchange,
            jobs_routing_key=self.jobs_routing_key,
            job_queue=self.job_queue,
            dlx_exchange=self.dlx_exchange,
            dlq_queue=self.dlq_queue,
            events_exchange=self.events_exchange,
            notifications_queue=self.notifications_queue,
        )
