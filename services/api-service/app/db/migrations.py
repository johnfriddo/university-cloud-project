"""Applica i file SQL in migrations/ una volta sola, in ordine di nome.

Creare le tabelle a mano non regge: chiunque cloni il repository, e ogni
ambiente futuro, deve ritrovarsi lo stesso schema senza seguire istruzioni
scritte. Ogni file viene applicato una volta e registrato in
schema_migrations, così rieseguire questo comando non fa niente.
"""

import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

# app/db/migrations.py -> la radice del servizio, che contiene migrations/
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Arbitrario ma fisso: due processi che migrano insieme si scontrerebbero.
# In Kubernetes questo gira come Job, che può essere ritentato: la guardia serve davvero.
ADVISORY_LOCK_KEY = 728_412


def run_migrations(database_url: str) -> list[str]:
    """Applica ogni migrazione in sospeso e restituisce i nomi di quelle applicate."""
    applied_now: list[str] = []

    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        already_applied = {row[0] for row in rows}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already_applied:
                log.info("migrazione %s: già applicata", path.name)
                continue

            log.info("migrazione %s: applico", path.name)
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,)
            )
            applied_now.append(path.name)

        # Tutto quello che c'è sopra condivide una transazione: o arriva il lotto
        # intero o il database resta intatto, mai migrato a metà.
        conn.commit()

    return applied_now
