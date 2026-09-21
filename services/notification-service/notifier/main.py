"""Il punto d'ingresso del notification-service.

Consuma gli eventi di completamento dall'exchange fanout media.events, registra
un avviso per il proprietario dell'immagine e lo consegna. Non espone nessuna
API: la porta HTTP esiste soltanto per le sonde.
"""

import logging
import signal
import threading

from notifier import checks
from notifier.config import Settings
from notifier.consumer import Notifier
from notifier.health import start_health_server

log = logging.getLogger("notifier")


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
    queue = settings.build_queue()

    shutdown = threading.Event()
    notifier = Notifier(
        settings=settings,
        queue=queue,
        # Il registro in locale, SNS su AWS: decide NOTIFICATION_CHANNEL, e
        # nient'altro nel servizio sa quale dei due sia.
        channel=settings.build_channel(),
        shutdown=shutdown,
    )

    def readiness() -> dict:
        return {
            "postgres": checks.check_postgres(settings.database_url),
            "rabbitmq": checks.check_queue(queue),
            # «Sto facendo il mio lavoro?», non «il broker è acceso?». Un processo che
            # ha smesso di consumare non serve a nessuno, per quanto vivo sembri.
            "consumer": {"ok": notifier.consuming.is_set()},
        }

    start_health_server(
        port=settings.health_port,
        service=settings.service_name,
        version=settings.version,
        readiness=readiness,
    )

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutdown.set())

    log.info("%s %s avviato", settings.service_name, settings.version)
    try:
        notifier.run()
    finally:
        queue.close()
        log.info("uscita")


if __name__ == "__main__":
    main()
