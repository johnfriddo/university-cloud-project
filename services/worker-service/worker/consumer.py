"""Il ciclo di consumo.

La conferma è manuale e arriva **dopo** che il lavoro è finito. È quella sola
scelta a rendere innocuo il guasto di un worker: un messaggio non confermato è
ancora del broker, che lo consegna di nuovo — a questo worker quando riparte, o
a un'altra replica. Confermare alla ricezione sarebbe più semplice e
perderebbe ogni lavoro in corso nell'istante del guasto.

Tre tipi di guasto, tre finali diversi:

- il problema è il file      -> immagine FAILED, messaggio confermato
- il problema è il messaggio -> rifiutato senza rimessa in coda, dritto alla DLQ
- è rotto il mondo intorno   -> ritentato, e dopo l'ultimo tentativo, DLQ
"""

import json
import logging
import threading
import time

from media_common.queue import CONNECTION_LOST, close_quietly, declare_topology
from worker import db
from worker.processor import ProcessingError, process_asset

log = logging.getLogger(__name__)

EVENT_PROCESSED = "asset.processed"
EVENT_FAILED = "asset.failed"

# Ogni quanto il ciclo si sveglia senza messaggi, per accorgersi di uno spegnimento.
IDLE_TICK_SECONDS = 1.0


class Consumer:
    def __init__(self, settings, storage, queue, metadata, shutdown):
        self._settings = settings
        self._storage = storage
        self._queue = queue
        self._metadata = metadata
        self._shutdown = shutdown
        # Lo legge la sonda di prontezza: un worker che ha smesso di consumare non ha
        # niente da offrire, per quanto sano appaia il suo processo.
        self.consuming = threading.Event()

    def run(self) -> None:
        """Consuma finché non viene chiesto di spegnersi, qualunque cosa faccia il broker.

        Che il broker sia irraggiungibile — non ancora acceso all'avvio, oppure
        riavviato mentre il worker gira — è una ragione per aspettare e riconnettersi,
        mai per uscire. Un lavoro interrotto dalla connessione persa non era stato
        confermato, quindi il broker lo riconsegna quando torna: niente si perde, e
        ogni scrittura a valle è idempotente, quindi niente viene nemmeno raddoppiato.
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

        # Mai tenere più messaggi non confermati di così. Con un carico a raffiche il
        # punto è distribuire i lavori fra le repliche, non lasciare che un worker se
        # ne prenoti cento mentre gli altri restano fermi.
        channel.basic_qos(prefetch_count=self._settings.prefetch_count)

        log.info(
            "in ascolto sulla coda '%s' (prefetch %s, max %s tentativi)",
            self._settings.job_queue,
            self._settings.prefetch_count,
            self._settings.max_attempts,
        )
        self.consuming.set()

        for method, properties, body in channel.consume(
            self._settings.job_queue, inactivity_timeout=IDLE_TICK_SECONDS
        ):
            # Controllato fra un messaggio e l'altro, mai dentro: uno spegnimento lascia
            # finire il lavoro che si ha in mano invece di abbandonarlo a metà.
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
            # Non è un problema di questo messaggio: la connessione non c'è più, e con
            # lei ogni modo di confermare o rifiutare. Il broker consegnerà di nuovo il
            # messaggio; il ciclo qui sopra si riconnette.
            raise
        except Exception:
            # Il ciclo deve sopravvivere a qualunque cosa, compreso un guasto mentre
            # registra un guasto.
            log.exception("errore non gestito nella gestione del messaggio")
            channel.basic_nack(method.delivery_tag, requeue=False)

    def _handle(self, channel, method, properties, body) -> None:
        payload = _parse(body)
        if payload is None:
            # Questo messaggio non lo capirà mai nessuno. Rifiutarlo senza rimetterlo in
            # coda lo manda alla coda dei rifiutati, dove resta a disposizione per essere
            # esaminato invece di rimbalzare per sempre.
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        asset_id = payload["asset_id"]
        attempt = _attempt_of(properties)
        if attempt > 1:
            log.info("asset %s: tentativo %s", asset_id, attempt)

        try:
            outcome = self._process(payload)
        except ProcessingError as exc:
            # Il problema è il file: un altro tentativo fallirebbe allo stesso modo.
            # Il motivo finisce sull'immagine, e l'utente si ritrova un pulsante Riprova.
            log.warning("asset %s in errore: %s", asset_id, exc)
            self._fail(asset_id, str(exc))
            channel.basic_ack(method.delivery_tag)
        except Exception:
            # Si è rotto qualcosa intorno a noi: il lavoro in sé è ancora buono.
            log.exception("errore infrastrutturale sull'asset %s", asset_id)
            self._retry_or_give_up(channel, method, payload, attempt)
        else:
            channel.basic_ack(method.delivery_tag)
            if not outcome["skipped"]:
                log.info("asset %s completato", asset_id)
                self._emit(
                    EVENT_PROCESSED,
                    {
                        "asset_id": asset_id,
                        "user_id": payload["user_id"],
                        "filename": outcome["filename"],
                        "status": "DONE",
                        "variants": outcome["variants"],
                    },
                )

    def _process(self, payload: dict) -> dict:
        asset_id = payload["asset_id"]

        with db.connect(self._settings.database_url) as conn:
            asset = db.claim_asset(conn, asset_id)
            if asset is None:
                # Già in uno stato finale: non è rimasto niente da fare, e confermare
                # il messaggio è il finale giusto.
                log.info("asset %s già in stato terminale, salto il messaggio", asset_id)
                return {"skipped": True}

        result = process_asset(
            storage=self._storage,
            settings=self._settings,
            asset_id=asset_id,
            user_id=payload["user_id"],
            storage_key=payload["storage_key"],
        )

        # I metadati tecnici sul documentale. Scritti prima che l'immagine sia
        # marcata DONE: quello stato deve significare «è tutto al suo posto».
        self._metadata.save_asset_metadata(
            asset_id=asset_id,
            user_id=payload["user_id"],
            mime=asset["mime"],
            exif=result["exif"],
            dimensions=result["dimensions"],
            checksum=result["checksum"],
        )

        # Prima i file, poi le righe. Se questa transazione fallisce il messaggio
        # torna indietro e le stesse chiavi deterministiche vengono semplicemente
        # sovrascritte; l'ordine opposto lascerebbe righe che indicano oggetti inesistenti.
        with db.connect(self._settings.database_url) as conn:
            db.save_variants(conn, asset_id, result["variants"])
            db.mark_done(conn, asset_id)

        log.info("asset %s: %s varianti generate", asset_id, len(result["variants"]))
        return {
            "skipped": False,
            "filename": asset["filename"],
            "variants": [variant["kind"] for variant in result["variants"]],
        }

    def _retry_or_give_up(self, channel, method, payload: dict, attempt: int) -> None:
        """Ripubblica il job con il contatore dei tentativi aumentato, oppure si arrende.

        Il nuovo tentativo è un messaggio **nuovo** e non una rimessa in coda di
        questo, perché il conteggio va scritto da qualche parte che il broker
        conservi: un messaggio rimesso in coda torna identico, senza memoria di aver fallito.
        """
        asset_id = payload["asset_id"]

        if attempt >= self._settings.max_attempts:
            log.error(
                "asset %s: esauriti i %s tentativi, il messaggio va in dead letter",
                asset_id,
                self._settings.max_attempts,
            )
            # Per quel che si può: se a rompersi è stato il database, anche questo
            # fallisce, e il messaggio nella DLQ resta la testimonianza di cosa è successo.
            self._fail(asset_id, "Elaborazione non riuscita dopo più tentativi")
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        # Attesa lineare, semplice. Un sistema di produzione userebbe una coda
        # ritardata, così il worker non resta bloccato ad aspettare; qui l'attesa è
        # breve e la semplicità vale di più.
        time.sleep(self._settings.retry_backoff_seconds * attempt)

        self._queue.publish_job(
            asset_id=asset_id,
            storage_key=payload["storage_key"],
            user_id=payload["user_id"],
            attempt=attempt + 1,
        )
        # Solo adesso: il messaggio nuovo esiste, quindi lasciar cadere questo non perde niente.
        channel.basic_ack(method.delivery_tag)

    def _fail(self, asset_id: str, message: str) -> None:
        try:
            with db.connect(self._settings.database_url) as conn:
                asset = db.mark_failed(conn, asset_id, message)
        except Exception:
            log.exception("impossibile registrare l'errore dell'asset %s", asset_id)
            return

        if asset is not None:
            self._emit(
                EVENT_FAILED,
                {
                    "asset_id": asset_id,
                    "user_id": str(asset["user_id"]),
                    "filename": asset["filename"],
                    "status": "FAILED",
                    "error": message,
                },
            )

    def _emit(self, event: str, payload: dict) -> None:
        """Pubblica un evento di completamento, senza mai far fallire il lavoro per questo.

        A questo punto il lavoro è già fatto e registrato. Fallire qui e rifare
        tutto per amore di un avviso sarebbe un pessimo scambio.
        """
        try:
            self._queue.publish_event(event, payload)
        except Exception:
            log.exception(
                "evento '%s' non pubblicato per l'asset %s", event, payload.get("asset_id")
            )


def _attempt_of(properties) -> int:
    headers = getattr(properties, "headers", None) or {}
    try:
        return max(1, int(headers.get("attempt", 1)))
    except (TypeError, ValueError):
        return 1


def _parse(body: bytes) -> dict | None:
    try:
        payload = json.loads(body)
        # Servono tutti e tre: un messaggio che non li ha non è lavorabile.
        payload["asset_id"], payload["storage_key"], payload["user_id"]
        return payload
    except (ValueError, TypeError, KeyError):
        log.error("messaggio illeggibile, lo mando in dead letter: %r", body[:200])
        return None
