"""L'adapter del broker dei messaggi.

La topologia che dichiara è quella su cui tutto il sistema è d'accordo:

    media.jobs (direct) --process--> media.process ---(se rifiutato)--> DLQ
    media.events (fanout) --------> notifications

Dichiararla a ogni connessione è deliberato e innocuo: RabbitMQ ignora una
dichiarazione che coincide con ciò che esiste già, e in cambio nessun servizio
può partire contro un broker che non è mai stato preparato.

Il costruttore prende valori semplici invece di un oggetto di configurazione:
il pacchetto è condiviso e non deve dipendere da come un servizio la organizza.
"""

import json
import logging
import threading
from dataclasses import dataclass

import pika

log = logging.getLogger(__name__)

SOCKET_TIMEOUT_SECONDS = 5

# L'attesa fra due tentativi di raggiungere il broker: raddoppia ogni volta, fino a qui.
RECONNECT_FIRST_DELAY_SECONDS = 1.0
RECONNECT_MAX_DELAY_SECONDS = 30.0

# «Il broker adesso non c'è». Aspettare può rimediare a questi.
#
# OSError non è facoltativo. Quando il nome del broker non risolve ancora — la
# situazione normale in Kubernetes, dove ogni pod parte nello stesso momento e
# la voce del DNS può arrivare dopo — l'errore che emerge è socket.gaierror,
# che non appartiene affatto alla libreria del client.
CONNECTION_LOST = (
    pika.exceptions.AMQPConnectionError,
    # Uso di un canale morto insieme alla sua connessione.
    pika.exceptions.ChannelWrongStateError,
    OSError,
)

# «Il broker c'è e ha detto no». Aspettare non rimedia: una password sbagliata
# resta sbagliata, e riprovare all'infinito nasconderebbe un errore di
# configurazione dietro un servizio che sembra soltanto in attesa.
REFUSED = (
    pika.exceptions.ProbableAuthenticationError,
    pika.exceptions.ProbableAccessDeniedError,
)


@dataclass(frozen=True)
class Topology:
    """Nomi di exchange e code. I valori predefiniti sono quelli della specifica."""

    jobs_exchange: str = "media.jobs"
    jobs_routing_key: str = "process"
    job_queue: str = "media.process"
    dlx_exchange: str = "media.jobs.dlx"
    dlq_queue: str = "media.process.dlq"
    events_exchange: str = "media.events"
    notifications_queue: str = "notifications"
    # Gli eventi rifiutati hanno una coda dei rifiutati propria, separata da quella
    # dei job: due tipi diversi di fallimento non devono finire nello stesso mucchio.
    events_dlx_exchange: str = "media.events.dlx"
    notifications_dlq_queue: str = "notifications.dlq"


class JobQueue:
    def __init__(self, url: str, topology: Topology | None = None):
        self._url = url
        self.topology = topology or Topology()

        # Una connessione di pika non va condivisa fra thread. I worker sincroni di
        # gunicorn servono una richiesta alla volta, quindi il lucchetto costa poco.
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None

    def publish_job(self, asset_id, storage_key: str, user_id, attempt: int = 1) -> None:
        """Pubblica il job di elaborazione per un'immagine.

        Torna solo quando il broker ha confermato di aver preso il messaggio: un
        errore qui significa che il job **non** è stato accodato, e chi chiama non
        deve dire il contrario all'utente.

        `attempt` viaggia in un'intestazione. Il broker non conta quante volte un
        worker ha restituito un messaggio, quindi chi ritenta deve portarsi dietro il
        conteggio: è l'unico posto che sopravvive alla morte del worker.
        """
        body = json.dumps(
            {
                "asset_id": str(asset_id),
                "storage_key": storage_key,
                "user_id": str(user_id),
            }
        ).encode()

        properties = pika.BasicProperties(
            content_type="application/json",
            # Il messaggio sopravvive al riavvio del broker, insieme alla coda durevole
            # in cui sta. Senza questo, un guasto perderebbe il lavoro in attesa.
            delivery_mode=pika.DeliveryMode.Persistent,
            # Permette al worker di riconoscere un messaggio riconsegnato.
            message_id=str(asset_id),
            headers={"attempt": attempt},
        )

        self._publish(
            exchange=self.topology.jobs_exchange,
            routing_key=self.topology.jobs_routing_key,
            body=body,
            properties=properties,
        )
        log.info("job pubblicato per l'asset %s (tentativo %s)", asset_id, attempt)

    def publish_event(self, event: str, payload: dict) -> None:
        """Pubblica un evento di completamento sull'exchange fanout.

        Fanout: si possono aggiungere consumatori più avanti senza che chi pubblica
        sappia niente di loro.
        """
        body = json.dumps({"event": event, **payload}).encode()
        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
        )
        self._publish(
            exchange=self.topology.events_exchange,
            routing_key="",
            body=body,
            properties=properties,
            mandatory=False,
        )

    def check(self) -> None:
        """Solleva un errore se il broker è irraggiungibile. Usato dalle sonde di prontezza."""
        with self._lock:
            self._ensure_channel()

    def close(self) -> None:
        with self._lock:
            self._close()

    def _publish(self, *, exchange, routing_key, body, properties, mandatory=True) -> None:
        with self._lock:
            # Una connessione in cache può essere morta dall'ultima pubblicazione, e il
            # guasto emerge solo all'uso: un tentativo in più la ricostruisce.
            for attempt in (1, 2):
                try:
                    channel = self._ensure_channel()
                    channel.basic_publish(
                        exchange=exchange,
                        routing_key=routing_key,
                        body=body,
                        properties=properties,
                        mandatory=mandatory,
                    )
                    return
                except (pika.exceptions.AMQPError, OSError) as exc:
                    self._close()
                    if attempt == 2:
                        raise
                    log.warning("publish fallito (%s), riprovo con una nuova connessione", exc)

    def _ensure_channel(self):
        if self._channel is not None and self._channel.is_open:
            return self._channel

        self._connection = self.connect()
        self._channel = self._connection.channel()
        declare_topology(self._channel, self.topology)
        # Trasforma basic_publish in una chiamata sincrona: fallisce ad alta voce se il
        # broker non ha accettato il messaggio, invece di perderlo in silenzio.
        self._channel.confirm_delivery()
        log.info("connessione al broker stabilita")
        return self._channel

    def connect(self):
        """Una connessione nuova al broker. Il consumatore apre la propria."""
        parameters = pika.URLParameters(self._url)
        parameters.socket_timeout = SOCKET_TIMEOUT_SECONDS
        parameters.blocked_connection_timeout = SOCKET_TIMEOUT_SECONDS
        return pika.BlockingConnection(parameters)

    def connect_patiently(self, stop: threading.Event):
        """Una connessione, aspettando il broker per tutto il tempo che serve.

        Un consumatore senza broker non può fare niente, ma è una ragione per
        aspettare, non per uscire. Uscire consegna il problema a chi riavvia il
        processo, e Kubernetes risponde alle uscite ripetute con pause sempre più
        lunghe — fino a cinque minuti — quindi un broker in ritardo di dieci secondi
        terrebbe il servizio giù molto più a lungo di quanto sia mancato il broker.

        L'attesa raddoppia a ogni tentativo, così un broker che resta via un'ora non
        viene martellato una volta al secondo. La interrompe subito `stop`: uno
        spegnimento chiesto durante l'attesa non deve scontare la pausa.

        Restituisce None quando `stop` è stato chiesto prima di riuscire a connettersi.
        Solleva subito un errore se il broker rifiuta le credenziali.
        """
        delay = RECONNECT_FIRST_DELAY_SECONDS
        attempts = 0

        while not stop.is_set():
            try:
                connection = self.connect()
            except REFUSED:
                log.error("il broker rifiuta le credenziali: riprovare non servirebbe")
                raise
            except CONNECTION_LOST as exc:
                attempts += 1
                log.warning(
                    "broker non raggiungibile (tentativo %s, %s), riprovo fra %.0f s",
                    attempts,
                    type(exc).__name__,
                    delay,
                )
                stop.wait(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)
                continue

            if attempts:
                log.info("broker raggiunto dopo %s tentativi falliti", attempts)
            return connection

        return None

    def _close(self) -> None:
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except Exception:  # già rotta, non c'è niente di utile da fare
            pass
        finally:
            self._connection = None
            self._channel = None


def close_quietly(connection) -> None:
    """Chiude una connessione che potrebbe essere già morta.

    Dopo che il broker se n'è andato, anche chiudere solleva un errore. Lasciarlo
    passare sostituirebbe l'errore che vale la pena conoscere con uno inutile, di pulizia.
    """
    try:
        if connection is not None and connection.is_open:
            connection.close()
    except Exception:  # già rotta, non è rimasto niente di utile da fare
        pass


def declare_topology(channel, topology: Topology) -> None:
    """Crea exchange e code se non ci sono ancora."""
    # Dove finiscono i job rifiutati. Dichiarata per prima: la coda dei job la indica.
    channel.exchange_declare(topology.dlx_exchange, exchange_type="fanout", durable=True)
    channel.queue_declare(topology.dlq_queue, durable=True)
    channel.queue_bind(topology.dlq_queue, topology.dlx_exchange)

    channel.exchange_declare(topology.jobs_exchange, exchange_type="direct", durable=True)
    channel.queue_declare(
        topology.job_queue,
        durable=True,
        # Un messaggio che il worker rifiuta definitivamente viene instradato qui
        # invece di sparire: resta lì, disponibile per essere esaminato.
        arguments={"x-dead-letter-exchange": topology.dlx_exchange},
    )
    channel.queue_bind(
        topology.job_queue, topology.jobs_exchange, routing_key=topology.jobs_routing_key
    )

    channel.exchange_declare(
        topology.events_dlx_exchange, exchange_type="fanout", durable=True
    )
    channel.queue_declare(topology.notifications_dlq_queue, durable=True)
    channel.queue_bind(topology.notifications_dlq_queue, topology.events_dlx_exchange)

    channel.exchange_declare(topology.events_exchange, exchange_type="fanout", durable=True)
    channel.queue_declare(
        topology.notifications_queue,
        durable=True,
        # Un evento che nessuno sa interpretare, o che è fallito troppe volte,
        # viene conservato qui invece di svanire.
        arguments={"x-dead-letter-exchange": topology.events_dlx_exchange},
    )
    channel.queue_bind(topology.notifications_queue, topology.events_exchange)
