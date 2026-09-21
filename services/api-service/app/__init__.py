"""La fabbrica dell'applicazione per l'api-service."""

import logging

from flask import Flask
from werkzeug.exceptions import HTTPException

from app.assets.routes import bp as assets_bp
from app.auth.routes import bp as auth_bp
from app.config import Settings
from app.db.pool import create_pool
from app.errors import HTTP_MESSAGES, error_response
from app.health import bp as health_bp
from app.messaging import build_queue
from app.metadata import build_metadata_store
from app.notifications.routes import bp as notifications_bp
from app.storage import build_storage


def create_app() -> Flask:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # La sonda di prontezza apre e chiude una connessione ogni pochi secondi:
    # a livello INFO il client del broker sommergerebbe i log dell'applicazione.
    logging.getLogger("pika").setLevel(logging.WARNING)

    app = Flask(__name__)
    settings = Settings.from_env()
    app.config["SETTINGS"] = settings
    app.config["DB_POOL"] = create_pool(settings)
    app.config["STORAGE"] = build_storage(settings)
    app.config["QUEUE"] = build_queue(settings)
    app.config["METADATA"] = build_metadata_store(settings)

    # Tutto vive sotto /api perché l'Ingress (e l'ALB nella versione cloud)
    # instrada / al frontend e /api qui.
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(assets_bp, url_prefix="/api")
    app.register_blueprint(notifications_bp, url_prefix="/api")

    _register_error_handlers(app)

    return app


def _register_error_handlers(app: Flask) -> None:
    """Fa sì che l'API risponda in JSON anche per i guasti che solleva Flask."""

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        # Copre 404, 405, richieste malformate e qualunque altra cosa sollevi Flask,
        # mantenendo la forma della risposta identica ai nostri errori. Le descrizioni
        # di Flask vengono sostituite: sono in inglese e lasciano trapelare gli interni.
        status = exc.code or 500
        return error_response(
            status,
            (exc.name or "error").lower().replace(" ", "_"),
            HTTP_MESSAGES.get(status, "Richiesta non valida"),
        )

    @app.errorhandler(Exception)
    def unhandled(exc):
        # I dettagli vanno nei log, mai al client: un messaggio d'errore può
        # rivelare la struttura del database o gli indirizzi interni.
        app.logger.exception("errore non gestito: %s", exc)
        return error_response(500, "internal_error", "Errore interno del servizio")
