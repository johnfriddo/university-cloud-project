"""Una riserva di connessioni per processo, condivisa da ogni richiesta.

Aprire una connessione a PostgreSQL costa una stretta di mano TCP più
l'autenticazione: farlo a ogni richiesta dominerebbe il tempo di risposta degli
endpoint che eseguono una sola piccola query. La riserva ne tiene aperte alcune e le presta.

Mette anche un tetto a quante connessioni il servizio trattiene: un database
gestito ne accetta un numero limitato e, nel cluster, ogni replica attinge
allo stesso budget.
"""

import logging

from flask import current_app
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)


def create_pool(settings) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # Le righe tornano come dizionari: row["email"] invece di row[1].
        kwargs={"row_factory": dict_row},
        # Presta una connessione solo dopo aver controllato che sia ancora viva, così
        # un riavvio del database non emerge come una richiesta fallita.
        check=ConnectionPool.check_connection,
        open=False,
        name="api-service",
    )
    # Non bloccante: il servizio deve partire anche se il database è
    # momentaneamente irraggiungibile. È la sonda di prontezza a tenere lontano
    # il traffico finché non funziona.
    pool.open(wait=False)
    log.info(
        "pool PostgreSQL creato (min=%s, max=%s)",
        settings.db_pool_min_size,
        settings.db_pool_max_size,
    )
    return pool


def get_pool() -> ConnectionPool:
    return current_app.config["DB_POOL"]
