"""Gli endpoint di vitalità e di prontezza.

La vitalità dimostra solo che il processo è vivo: se fallisce, Kubernetes riavvia
il pod. La prontezza interroga invece i servizi da cui dipende, così un pod che
non raggiunge il database esce dai destinatari del Service invece di essere
riavviato in continuazione. Ogni controllo ha un tempo massimo breve, altrimenti
una dipendenza bloccata bloccherebbe la sonda stessa.
"""

from flask import Blueprint, current_app, jsonify

from app.db.pool import get_pool
from app.messaging import get_queue
from app.storage import get_storage

CHECK_TIMEOUT_SECONDS = 3

bp = Blueprint("health", __name__)


@bp.get("/health/live")
def live():
    settings = current_app.config["SETTINGS"]
    return jsonify(status="ok", service=settings.service_name, version=settings.version)


@bp.get("/health/ready")
def ready():
    settings = current_app.config["SETTINGS"]
    # MongoDB è deliberatamente assente. Questo servizio gli serve per una cosa sola —
    # la ricerca sui dati tecnici e la scheda dei metadati di una singola immagine —
    # ed entrambe si degradano da sole: la scheda torna vuota, la ricerca risponde
    # 503. Metterlo qui toglierebbe invece dalla rotazione l'intera API, caricamento
    # e libreria compresi, per qualcosa che la maggior parte delle richieste non
    # tocca mai. Il worker, che senza non può scrivere i dati EXIF, lo controlla.
    checks = {
        "postgres": _check_postgres(),
        "rabbitmq": _check_rabbitmq(),
        "object_storage": _check_object_storage(),
    }
    everything_ok = all(check["ok"] for check in checks.values())
    body = jsonify(
        status="ready" if everything_ok else "not-ready",
        service=settings.service_name,
        checks=checks,
    )
    return body, 200 if everything_ok else 503


def _check_postgres() -> dict:
    try:
        # Passa dalla riserva di connessioni che usano gli endpoint: così la sonda
        # dimostra anche che una connessione è davvero disponibile, non solo che il
        # database è acceso da qualche parte.
        with get_pool().connection(timeout=CHECK_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as exc:  # una sonda di prontezza non deve mai sollevare un errore
        return {"ok": False, "error": str(exc)}


def _check_rabbitmq() -> dict:
    try:
        # Attraverso l'adapter: la sonda verifica proprio la connessione che usa la
        # pubblicazione, e la ripara se nel frattempo è andata a male.
        get_queue().check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_object_storage() -> dict:
    try:
        # Attraverso l'adapter, come ogni altra chiamata: la sonda controlla proprio il
        # client che usano gli endpoint, non un secondo client costruito per l'occasione.
        get_storage().check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
