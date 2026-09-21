"""Rimozione delle registrazioni che non sono mai diventate immagini: `python -m app.cleanup`.

Un caricamento sono due passi con un client in mezzo. `POST /assets` scrive la
riga e restituisce un link firmato; `POST /assets/{id}/complete` dice che il
file c'è e accoda il job. Qualunque cosa succeda fra i due — una scheda
chiusa, una connessione caduta, un client che chiede un link e se ne va —
lascia una riga che non diventerà mai un'immagine.

Nient'altro nel sistema può decidere che una riga così è abbandonata. Il
worker non la vede mai, perché nessun job è stato pubblicato; l'utente la vede
come un caricamento in attesa per sempre. Solo il tempo trascorso distingue una
registrazione abbandonata da una ancora in corso, e solo un passaggio periodico può agire.

Gira come container a sé, breve, come le migrazioni. Nel cluster diventa un
CronJob di Kubernetes.
"""

import argparse
import logging
import sys

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.storage import build_storage

log = logging.getLogger("cleanup")

# Generoso di proposito: un link di caricamento firmato dura quindici minuti,
# quindi tutto ciò che ha più di un giorno è abbandonato senza alcun dubbio.
DEFAULT_AGE_HOURS = 24


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Elimina le registrazioni mai completate")
    parser.add_argument("--older-than-hours", type=int, default=DEFAULT_AGE_HOURS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="elenca soltanto ciò che verrebbe eliminato",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    storage = build_storage(settings)

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        abandoned = conn.execute(
            """
            SELECT id, original_key, filename, created_at
              FROM assets
             WHERE submitted_at IS NULL
               AND created_at < now() - make_interval(hours => %s)
            """,
            (args.older_than_hours,),
        ).fetchall()

        if not abandoned:
            log.info("nessuna registrazione abbandonata da eliminare")
            return 0

        log.info("registrazioni abbandonate trovate: %s", len(abandoned))
        for row in abandoned:
            log.info("  %s  %s  (%s)", row["id"], row["filename"], row["created_at"])

        if args.dry_run:
            log.info("dry-run: nulla è stato eliminato")
            return 0

        removed_objects = 0
        for row in abandoned:
            # Il file può esistere lo stesso: il caricamento può essere andato a buon
            # fine con la sola conferma mancante. Cancellare la riga senza cancellare
            # l'oggetto lascerebbe byte che nessuno può più raggiungere.
            try:
                if storage.head(storage.originals, row["original_key"]) is not None:
                    storage.delete(storage.originals, row["original_key"])
                    removed_objects += 1
            except Exception:
                log.exception("oggetto %s non eliminato, proseguo", row["original_key"])

        deleted = conn.execute(
            """
            DELETE FROM assets
             WHERE submitted_at IS NULL
               AND created_at < now() - make_interval(hours => %s)
            """,
            (args.older_than_hours,),
        ).rowcount

    log.info("eliminate %s registrazioni e %s file orfani", deleted, removed_objects)
    return 0


if __name__ == "__main__":
    sys.exit(main())
