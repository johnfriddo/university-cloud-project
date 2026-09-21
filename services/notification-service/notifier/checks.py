"""I controlli di prontezza dei servizi da cui dipende il notification-service."""

import psycopg

CHECK_TIMEOUT_SECONDS = 3


def check_postgres(database_url: str) -> dict:
    try:
        with psycopg.connect(database_url, connect_timeout=CHECK_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_queue(queue) -> dict:
    try:
        # Attraverso l'adapter condiviso: la sonda verifica proprio la connessione
        # che usa il servizio, e la ripara se nel frattempo è andata a male.
        queue.check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
