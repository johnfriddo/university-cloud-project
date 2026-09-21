"""I controlli di prontezza dei servizi da cui dipende il worker.

Archivio e broker si controllano attraverso gli adapter condivisi, gli stessi
oggetti che usa il percorso di elaborazione: la sonda verifica ciò che fa
davvero il lavoro, non un secondo client costruito per l'occasione.

Ogni controllo ha un tempo massimo breve e non solleva mai un errore: una sonda
che si blocca è peggio di una sonda che segnala un guasto.
"""

import psycopg

CHECK_TIMEOUT_SECONDS = 3


def check_postgres(database_url: str) -> dict:
    try:
        with psycopg.connect(database_url, connect_timeout=CHECK_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_metadata(metadata) -> dict:
    try:
        metadata.check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_queue(queue) -> dict:
    try:
        queue.check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def check_object_storage(storage) -> dict:
    try:
        storage.check()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
