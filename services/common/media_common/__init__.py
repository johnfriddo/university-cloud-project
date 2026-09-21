"""Gli adapter condivisi dai servizi.

Ogni conversazione con l'object storage e con il broker dei messaggi passa da
queste classi. Nessun servizio importa boto3 o pika direttamente, così il
passaggio da MinIO e RabbitMQ a S3 e Amazon MQ resta confinato qui.
"""

from media_common.queue import JobQueue
from media_common.storage import ObjectStorage

__all__ = ["JobQueue", "ObjectStorage"]
