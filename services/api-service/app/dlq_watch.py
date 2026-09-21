"""Guarda le code dei rifiutati e dice qualcosa: `python -m app.dlq_watch`.

Un messaggio finisce in una coda dei rifiutati quando nessuno è riuscito a
interpretarlo, o quando i tentativi sono esauriti. È un fatto che vale la pena
sapere — e finora niente nel sistema lo diceva ad alta voce. Le code c'erano e nessuno le guardava.

Gira come CronJob e scrive nel registro. È rilevamento, non allarme: qualcuno
deve leggere il registro o guardare il CronJob. Su AWS resta così, dove Amazon
MQ pubblica lo stesso conteggio su CloudWatch e un allarme su quel valore
sarebbe il passo successivo naturale — non fatto qui, e dichiarato come limite.

Codice di uscita 0 quando le code sono vuote, 1 quando non lo sono: un CronJob
la cui ultima esecuzione è fallita si vede in `kubectl get cronjob` senza leggere niente.

L'API di gestione, non AMQP: contare i messaggi senza consumarli è
esattamente ciò per cui esiste, e non richiede nessuna libreria client.
"""

import base64
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from media_common.env import required

log = logging.getLogger("dlq-watch")

REQUEST_TIMEOUT_SECONDS = 10
MANAGEMENT_PORT = 15672


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    broker = _management_client(required("RABBITMQ_URL"))
    queues = [
        os.getenv("DLQ_QUEUE", "media.process.dlq"),
        os.getenv("NOTIFICATIONS_DLQ", "notifications.dlq"),
    ]

    total = 0
    for queue in queues:
        try:
            waiting = broker(queue)
        except Exception as exc:  # un guardiano non deve morire dei propri errori
            log.error("non riesco a leggere la coda %s: %s", queue, exc)
            return 1

        total += waiting
        if waiting:
            # Deliberatamente a livello di avviso e in parole semplici: chi legge
            # questa riga sta cercando qualcosa da fare.
            log.warning(
                "%s: %s messaggi rifiutati in attesa. "
                "Sono lavori che nessuno ha potuto completare: vanno guardati.",
                queue,
                waiting,
            )
        else:
            log.info("%s: vuota", queue)

    if total:
        log.warning("in tutto %s messaggi nelle code dei rifiutati", total)
        return 1

    log.info("nessun messaggio rifiutato")
    return 0


def _management_client(amqp_url: str):
    """Restituisce una funzione che conta i messaggi in attesa in una coda.

    L'indirizzo e le credenziali sono quelli che i servizi usano già:
    l'interfaccia di gestione ascolta su un'altra porta dello stesso broker, con
    gli stessi utenti.
    """
    parts = urllib.parse.urlparse(amqp_url)
    credentials = f"{parts.username}:{parts.password}".encode()
    authorization = "Basic " + base64.b64encode(credentials).decode()
    base = management_base(amqp_url)

    def messages(queue: str) -> int:
        # Il virtual host predefinito è «/», che dentro un percorso va scritto
        # come %2F.
        path = f"/queues/%2F/{urllib.parse.quote(queue, safe='')}"
        request = urllib.request.Request(base + path)
        request.add_header("Authorization", authorization)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as answer:
                return json.loads(answer.read()).get("messages", 0)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # La coda la crea chi la consuma. Prima del primo avvio non esiste,
                # e non è un problema.
                log.info("la coda %s non esiste ancora", queue)
                return 0
            raise

    return messages


def management_base(amqp_url: str) -> str:
    """Dove risponde l'API di gestione del broker che sta dietro `amqp_url`.

    Due broker, due convenzioni:

    - AMQP in chiaro (RabbitMQ nel cluster): HTTP in chiaro sulla porta 15672;
    - AMQP su TLS (Amazon MQ): HTTPS sulla porta standard dello stesso host.
      Amazon MQ espone solo quello, e del resto sarebbe strano raggiungere un
      broker cifrato con un canale di gestione non cifrato.
    """
    parts = urllib.parse.urlparse(amqp_url)
    if parts.scheme == "amqps":
        return f"https://{parts.hostname}/api"
    return f"http://{parts.hostname}:{MANAGEMENT_PORT}/api"


if __name__ == "__main__":
    sys.exit(main())
