"""La configurazione di esecuzione, letta esclusivamente da variabili d'ambiente."""

import os
from dataclasses import dataclass

from media_common import JobQueue
from media_common.env import MissingConfiguration, required
from media_common.queue import Topology
from notifier.delivery import LogChannel, SnsChannel

CHANNELS = ("log", "sns")


@dataclass(frozen=True)
class Settings:
    service_name: str
    version: str
    health_port: int
    database_url: str
    rabbitmq_url: str
    jobs_exchange: str
    jobs_routing_key: str
    job_queue: str
    dlx_exchange: str
    dlq_queue: str
    events_exchange: str
    notifications_queue: str
    events_dlx_exchange: str
    notifications_dlq_queue: str
    max_attempts: int
    retry_backoff_seconds: float
    # «log» in locale, «sns» su AWS. L'argomento serve solo con SNS.
    channel: str
    sns_topic_arn: str

    @classmethod
    def from_env(cls) -> "Settings":
        channel = os.getenv("NOTIFICATION_CHANNEL", "log").strip().lower()
        if channel not in CHANNELS:
            raise MissingConfiguration(
                f"NOTIFICATION_CHANNEL vale «{channel}»: "
                f"i valori ammessi sono {', '.join(CHANNELS)}"
            )
        return cls(
            service_name=os.getenv("SERVICE_NAME", "notification-service"),
            version=os.getenv("SERVICE_VERSION", "0.1.0"),
            health_port=int(os.getenv("HEALTH_PORT", "8000")),
            database_url=required("DATABASE_URL"),
            rabbitmq_url=required("RABBITMQ_URL"),
            jobs_exchange=os.getenv("JOBS_EXCHANGE", "media.jobs"),
            jobs_routing_key=os.getenv("JOBS_ROUTING_KEY", "process"),
            job_queue=os.getenv("JOB_QUEUE", "media.process"),
            dlx_exchange=os.getenv("DLX_EXCHANGE", "media.jobs.dlx"),
            dlq_queue=os.getenv("DLQ_QUEUE", "media.process.dlq"),
            events_exchange=os.getenv("EVENTS_EXCHANGE", "media.events"),
            notifications_queue=os.getenv("NOTIFICATIONS_QUEUE", "notifications"),
            events_dlx_exchange=os.getenv("EVENTS_DLX_EXCHANGE", "media.events.dlx"),
            notifications_dlq_queue=os.getenv("NOTIFICATIONS_DLQ", "notifications.dlq"),
            # Come il worker, fino al numero: i guasti che giustificano un nuovo
            # tentativo sono gli stessi, quelli infrastrutturali, e una regola sola
            # è più facile da ricordare di due.
            max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
            retry_backoff_seconds=float(os.getenv("RETRY_BACKOFF_SECONDS", "2")),
            channel=channel,
            sns_topic_arn=required("SNS_TOPIC_ARN") if channel == "sns" else "",
        )

    def build_channel(self):
        if self.channel == "sns":
            return SnsChannel(topic_arn=self.sns_topic_arn)
        return LogChannel()

    def topology(self) -> Topology:
        return Topology(
            jobs_exchange=self.jobs_exchange,
            jobs_routing_key=self.jobs_routing_key,
            job_queue=self.job_queue,
            dlx_exchange=self.dlx_exchange,
            dlq_queue=self.dlq_queue,
            events_exchange=self.events_exchange,
            notifications_queue=self.notifications_queue,
            events_dlx_exchange=self.events_dlx_exchange,
            notifications_dlq_queue=self.notifications_dlq_queue,
        )

    def build_queue(self) -> JobQueue:
        return JobQueue(url=self.rabbitmq_url, topology=self.topology())
