"""Costruzione e accesso all'adapter condiviso della coda."""

from flask import current_app

from media_common import JobQueue
from media_common.queue import Topology


def build_queue(settings) -> JobQueue:
    return JobQueue(
        url=settings.rabbitmq_url,
        topology=Topology(
            jobs_exchange=settings.jobs_exchange,
            jobs_routing_key=settings.jobs_routing_key,
            job_queue=settings.job_queue,
            dlx_exchange=settings.dlx_exchange,
            dlq_queue=settings.dlq_queue,
            events_exchange=settings.events_exchange,
            notifications_queue=settings.notifications_queue,
        ),
    )


def get_queue() -> JobQueue:
    return current_app.config["QUEUE"]
