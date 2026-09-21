"""Il comando autonomo per le migrazioni: `python -m app.migrate`.

Gira come container a sé, breve, prima che i servizi partano, e poi esce.
Tenerlo fuori dall'avvio dell'applicazione significa che lo schema viene
applicato una volta da un processo solo, quale che sia il numero di repliche —
e nel cluster lo stesso identico comando diventa un Job di Kubernetes.
"""

import logging
import sys

from app.config import Settings
from app.db.migrations import run_migrations

log = logging.getLogger("migrate")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    if not settings.database_url:
        log.error("DATABASE_URL non impostata")
        return 1

    applied = run_migrations(settings.database_url)
    if applied:
        log.info("migrazioni applicate: %s", ", ".join(applied))
    else:
        log.info("nessuna migrazione da applicare, schema già aggiornato")
    return 0


if __name__ == "__main__":
    sys.exit(main())
