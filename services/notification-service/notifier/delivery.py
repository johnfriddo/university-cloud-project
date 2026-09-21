"""Come viene formulato un avviso, e dove viene consegnato.

Il testo e il canale sono tenuti separati di proposito. In locale non c'è
nessun server di posta — e aggiungerne uno non fa parte dello stack — quindi
consegnare significa scrivere il messaggio dove si possa leggere: il registro,
accanto alla riga nel database. Su AWS lo stesso messaggio va a SNS e diventa una email vera.

La Fase 7 ha aggiunto il canale SNS e non ha toccato nient'altro: la
composizione, il registro e i tentativi restano dove sono. Sceglie NOTIFICATION_CHANNEL.
"""

import logging
import unicodedata
from typing import Protocol

log = logging.getLogger(__name__)

# SNS accetta l'oggetto di una email solo se è ASCII, su una riga sola e più
# corto di 100 caratteri; qualunque altra cosa viene rifiutata con InvalidParameter.
SNS_SUBJECT_MAX = 99

EVENT_PROCESSED = "asset.processed"
EVENT_FAILED = "asset.failed"

# Gli eventi che questo servizio sa formulare. L'exchange è di tipo fanout:
# qualunque altra cosa è affare di qualcun altro, non un errore.
KNOWN_EVENTS = (EVENT_PROCESSED, EVENT_FAILED)

VARIANT_NAMES = {
    "thumb": "miniatura",
    "medium": "versione media",
    "large": "versione grande con filigrana",
}


class Channel(Protocol):
    """Qualunque cosa sappia consegnare un messaggio: il registro in locale, SNS su AWS."""

    def send(self, *, recipient: str, subject: str, body: str) -> None: ...


class LogChannel:
    """Il canale locale: il messaggio viene scritto nel registro, per intero.

    Non deve dichiarare più di quello che fa. All'indirizzo stampato qui non
    arriva niente — nello stack locale non c'è nessun server di posta — e il
    registro lo dice esattamente. Quello che questo dimostra è che l'evento è
    stato consumato, il destinatario risolto e il messaggio composto: l'ultimo
    passo, quello che lo mette davanti a una persona, è quello che fornisce AWS.
    """

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        log.info(
            "notifica pronta per %s — in locale il canale è il log, "
            "nessuna email viene spedita\n--- %s ---\n%s\n---",
            recipient,
            subject,
            body,
        )


class SnsChannel:
    """Il canale di AWS: il messaggio viene pubblicato su un argomento SNS.

    Un argomento è una diffusione: chi è iscritto riceve ciò che viene
    pubblicato. Il destinatario viaggia perciò come attributo del messaggio, ed è
    su quello che filtrerebbe un'iscrizione per utente (una filter policy di SNS
    su `recipient`). In questo progetto c'è un'iscrizione sola — l'indirizzo dato
    a Terraform — e riceve ogni avviso.

    Un guasto solleva un errore: il consumatore ritenta, e dopo l'ultimo
    tentativo l'evento va nella coda dei rifiutati, come ogni altro guasto
    infrastrutturale.
    """

    def __init__(self, topic_arn: str, client=None):
        # `client` serve ai test. La regione arriva dall'ambiente:
        # su EKS il meccanismo IRSA mette AWS_REGION in ogni pod.
        if client is None:
            import boto3

            client = boto3.client("sns")
        self._client = client
        self._topic_arn = topic_arn

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        self._client.publish(
            TopicArn=self._topic_arn,
            Subject=sns_subject(subject),
            Message=body,
            MessageAttributes={
                "recipient": {"DataType": "String", "StringValue": recipient},
            },
        )
        log.info("notifica per %s pubblicata su SNS", recipient)


def sns_subject(subject: str) -> str:
    """L'oggetto riscritto in modo che SNS lo accetti, lasciandolo leggibile.

    Gli accenti cadono invece delle lettere («è» diventa «e»), le virgolette
    basse diventano virgolette semplici, e tutto ciò che resta fuori dall'ASCII sparisce.
    """
    text = subject.replace("«", '"').replace("»", '"')
    text = " ".join(text.split())  # a capo e sequenze di spazi
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text[:SNS_SUBJECT_MAX] or "Notifica"


def compose(event: str, payload: dict) -> tuple[str, str]:
    """Oggetto e corpo del messaggio, nella lingua dell'utente."""
    filename = payload.get("filename") or "la tua immagine"

    if event == EVENT_PROCESSED:
        subject = f"«{filename}» è pronta"
        body = (
            f"L'elaborazione di «{filename}» è terminata.\n\n"
            f"{_variant_list(payload.get('variants'))}\n\n"
            "Le trovi nella tua libreria."
        )
        return subject, body

    subject = f"«{filename}» non è stata elaborata"
    reason = payload.get("error") or "Motivo non specificato"
    body = (
        f"L'elaborazione di «{filename}» non è riuscita.\n\n"
        f"Motivo: {reason}\n\n"
        "Puoi rimetterla in coda dalla libreria con il pulsante Riprova."
    )
    return subject, body


def _variant_list(variants) -> str:
    """Le versioni prodotte, chiamate come le vede l'utente nella libreria."""
    if not variants:
        return "Sono state generate le versioni derivate."

    names = [VARIANT_NAMES.get(str(kind), str(kind)) for kind in variants]
    return "Versioni generate: " + ", ".join(names) + "."
