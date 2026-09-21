"""Il consumo degli eventi di completamento.

La disciplina è quella del worker, per le stesse ragioni: conferma manuale, un
numero limitato di tentativi portato in un'intestazione, e una coda dei
rifiutati per ciò che nessuno sa interpretare. Il messaggio è confermato solo
quando l'avviso è registrato, così un guasto a metà costa una ripetizione, mai un silenzio.

Ripetere è sicuro: il vincolo di unicità su (asset_id, event) fa sì che il
secondo passaggio trovi la riga già scritta e non avvisi nessuno due volte.
"""

import json
import logging
import threading
import time

import pika

from media_common.queue import CONNECTION_LOST, close_quietly, declare_topology
from notifier import db, delivery

log = logging.getLogger(__name__)

# Ogni quanto il ciclo si sveglia senza messaggi, per accorgersi di uno spegnimento.
IDLE_TICK_SECONDS = 1.0


class Notifier:
    def __init__(self, settings, queue, channel: delivery.Channel, shutdown: threading.Event):
        self._settings = settings
        self._queue = queue
        self._channel = channel
        self._shutdown = shutdown
        # Lo legge la sonda di prontezza: un servizio che non sta consumando non ha
        # niente da offrire, per quanto sano appaia il suo processo.
        self.consuming = threading.Event()

    def run(self) -> None:
        """Consuma finché non viene chiesto di spegnersi, qualunque cosa faccia il broker.

        Lo stesso ragionamento del worker: un broker irraggiungibile è una ragione per
        aspettare e riconnettersi, non per uscire. Un evento interrotto dalla
        connessione persa non era stato confermato e torna; il vincolo di unicità su
        (asset_id, event) impedisce alla ripetizione di avvisare qualcuno due volte.
        """
        while not self._shutdown.is_set():
            connection = self._queue.connect_patiently(self._shutdown)
            if connection is None:
                break  # spegnimento chiesto mentre si aspettava il broker

            try:
                self._consume(connection)
            except CONNECTION_LOST as exc:
                log.warning("connessione al broker persa (%s), mi riconnetto", type(exc).__name__)
            finally:
                self.consuming.clear()
                close_quietly(connection)

        log.info("consumo terminato")

    def _consume(self, connection) -> None:
        channel = connection.channel()
        declare_topology(channel, self._settings.topology())

        # Un messaggio alla volta. Qui non c'è nessuna raffica da distribuire — un
        # evento è un viaggio al database — e così l'ordine resta leggibile nel registro.
        channel.basic_qos(prefetch_count=1)

        log.info(
            "in ascolto sulla coda '%s' (max %s tentativi)",
            self._settings.notifications_queue,
            self._settings.max_attempts,
        )
        self.consuming.set()

        for method, properties, body in channel.consume(
            self._settings.notifications_queue, inactivity_timeout=IDLE_TICK_SECONDS
        ):
            # Controllato fra un messaggio e l'altro, mai dentro: uno spegnimento lascia
            # finire il messaggio che si ha in mano invece di abbandonarlo a metà.
            if self._shutdown.is_set():
                log.info("arresto richiesto, interrompo il consumo")
                # Annulla la sottoscrizione e restituisce quello che era stato
                # consegnato ma non ancora confermato.
                channel.cancel()
                return
            if method is None:
                continue  # giro a vuoto, nessun messaggio in attesa

            self._handle_safely(channel, method, properties, body)

    def _handle_safely(self, channel, method, properties, body) -> None:
        try:
            self._handle(channel, method, properties, body)
        except CONNECTION_LOST:
            # La connessione non c'è più, e con lei ogni modo di confermare o
            # rifiutare: l'evento torna quando il ciclo qui sopra si riconnette.
            raise
        except Exception:
            # Il ciclo deve sopravvivere proprio a tutto.
            log.exception("errore non gestito nella gestione dell'evento")
            channel.basic_nack(method.delivery_tag, requeue=False)

    def _handle(self, channel, method, properties, body) -> None:
        payload = _parse(body)
        if payload is None:
            # Questo messaggio non lo capirà mai nessuno: alla coda dei rifiutati,
            # dove resta a disposizione invece di rimbalzare per sempre.
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        event = payload.get("event")
        if event not in delivery.KNOWN_EVENTS:
            # L'exchange è di tipo fanout: un evento destinato a qualcun altro non è
            # un errore, e non va trattato come tale.
            log.info("evento '%s' ignorato: non riguarda le notifiche", event)
            channel.basic_ack(method.delivery_tag)
            return

        attempt = _attempt_of(properties)
        if attempt > 1:
            log.info("asset %s: tentativo %s", payload.get("asset_id"), attempt)

        try:
            self._notify(event, payload)
        except Exception:
            log.exception("notifica non riuscita per l'asset %s", payload.get("asset_id"))
            self._retry_or_give_up(channel, method, payload, attempt)
        else:
            channel.basic_ack(method.delivery_tag)

    def _notify(self, event: str, payload: dict) -> None:
        asset_id = payload["asset_id"]
        subject, body = delivery.compose(event, payload)

        with db.connect(self._settings.database_url) as conn:
            owner = db.find_owner(conn, asset_id)
            if owner is None:
                # L'evento è sopravvissuto all'immagine o all'account. Non è un guasto da
                # ritentare: semplicemente non è rimasto nessuno da informare.
                log.warning("asset %s senza proprietario, evento scartato", asset_id)
                return

            recipient = owner["email"]
            notification_id = db.record(
                conn,
                user_id=str(owner["user_id"]),
                asset_id=asset_id,
                event=event,
                recipient=recipient,
                subject=subject,
                body=body,
            )

            if notification_id is None:
                existing = db.find_notification(conn, asset_id, event)
                if existing is None or existing["sent_at"] is not None:
                    log.info("asset %s: notifica '%s' già inviata", asset_id, event)
                    return
                # Registrato prima, mai consegnato: vale un altro tentativo.
                notification_id = str(existing["id"])
                log.info("asset %s: consegna mai riuscita, riprovo", asset_id)

        # Fuori dalla transazione, e di proposito. Il registro viene confermato
        # prima, così un canale che fallisce lascia una riga con sent_at vuoto — la
        # prova che qualcosa era dovuto e non è mai stato consegnato.
        self._channel.send(recipient=recipient, subject=subject, body=body)

        with db.connect(self._settings.database_url) as conn:
            db.mark_sent(conn, notification_id)

    def _retry_or_give_up(self, channel, method, payload: dict, attempt: int) -> None:
        """Ripubblica l'evento con il contatore dei tentativi aumentato, oppure si arrende.

        Il nuovo tentativo è un messaggio **nuovo** e non una rimessa in coda di
        questo, perché il conteggio va scritto da qualche parte che il broker
        conservi: un messaggio rimesso in coda torna identico, senza memoria di aver fallito.

        Viene ripubblicato direttamente sulla coda, non sull'exchange fanout: un
        exchange ne consegnerebbe una copia a ogni altro consumatore collegato, e il
        problema è soltanto nostro.
        """
        asset_id = payload.get("asset_id")

        if attempt >= self._settings.max_attempts:
            log.error(
                "asset %s: esauriti i %s tentativi, l'evento va in dead letter",
                asset_id,
                self._settings.max_attempts,
            )
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        # Attesa lineare semplice, come nel worker: l'attesa è breve e la
        # semplicità vale più di una coda ritardata.
        time.sleep(self._settings.retry_backoff_seconds * attempt)

        channel.basic_publish(
            exchange="",
            routing_key=self._settings.notifications_queue,
            body=json.dumps(payload).encode(),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
                headers={"attempt": attempt + 1},
            ),
        )
        # Solo adesso: il messaggio nuovo esiste, quindi lasciar cadere questo non perde niente.
        channel.basic_ack(method.delivery_tag)


def _attempt_of(properties) -> int:
    headers = getattr(properties, "headers", None) or {}
    try:
        return max(1, int(headers.get("attempt", 1)))
    except (TypeError, ValueError):
        return 1


def _parse(body: bytes) -> dict | None:
    """L'evento, oppure None quando non si riesce proprio a leggerlo."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        log.error("evento illeggibile scartato: %s", body[:200])
        return None

    if not isinstance(payload, dict):
        log.error("evento di forma inattesa scartato: %s", body[:200])
        return None

    # Tutto quello che viene dopo legge questi due senza controllarli di nuovo.
    if not payload.get("asset_id") or not payload.get("user_id"):
        log.error("evento privo di asset_id o user_id, scartato: %s", body[:200])
        return None

    return payload
