"""Il punto d'ingresso del worker-service.

Su SIGTERM il worker smette di prendere messaggi nuovi e lascia finire quello
che ha in mano, così un lavoro non confermato torna al broker invece di
perdersi. È anche quello che Kubernetes si aspetta durante un aggiornamento
graduale o una riduzione di repliche.
"""

import logging
import signal
import threading

from worker import checks
from worker.config import Settings
from worker.consumer import Consumer
from worker.health import start_health_server

log = logging.getLogger("worker")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Il client del broker racconta da sé ogni tentativo di connessione fallito:
    # sette righe di ERRORE e una traccia di stack ogni volta, che a chi legge il
    # registro sembrano un crollo. Mentre il broker è via, è un crollo finto
    # per tentativo. Ogni guasto che conta arriva al nostro codice come eccezione
    # e viene registrato lì, una volta sola, in parole semplici.
    logging.getLogger("pika").setLevel(logging.CRITICAL)

    settings = Settings.from_env()
    storage = settings.build_storage()
    queue = settings.build_queue()
    metadata = settings.build_metadata_store()

    shutdown = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutdown.set())

    # Costruito prima del server di salute, che deve potergli chiedere se sta
    # consumando.
    consumer = Consumer(
        settings=settings,
        storage=storage,
        queue=queue,
        metadata=metadata,
        shutdown=shutdown,
    )

    def readiness() -> dict:
        return {
            "postgres": checks.check_postgres(settings.database_url),
            "rabbitmq": checks.check_queue(queue),
            # «metadata», non «mongo»: su AWS lo stesso controllo interroga DynamoDB.
            "metadata": checks.check_metadata(metadata),
            "object_storage": checks.check_object_storage(storage),
            # «Sto facendo il mio lavoro?», non «il broker è acceso?». La stessa domanda
            # a cui risponde il notification-service.
            "consumer": {"ok": consumer.consuming.is_set()},
        }

    start_health_server(
        port=settings.health_port,
        service=settings.service_name,
        version=settings.version,
        readiness=readiness,
    )

    log.info("%s %s avviato", settings.service_name, settings.version)

    try:
        metadata.ensure_indexes()
    except Exception:
        # Non è fatale: la sonda di prontezza tiene il pod fuori dalla rotazione finché
        # il documentale non risponde, e l'indice viene creato al prossimo avvio.
        log.warning("indici del documentale non creati, riprovo al prossimo avvio")

    try:
        consumer.run()
    finally:
        queue.close()
        metadata.close()
        log.info("uscita")


if __name__ == "__main__":
    main()
