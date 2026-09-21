"""I passaggi di stato di un'immagine, scritti dal worker.

Una connessione per lavoro, aperta e chiusa attorno a esso. Un lavoro dura
secondi, quindi la stretta di mano è rumore accanto al lavoro stesso, e non
c'è nessuna connessione di lunga durata che si guasti mentre il worker aspetta messaggi.
"""

import logging

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5


def connect(database_url: str):
    """Contesto di connessione: conferma se va bene, annulla in caso di errore."""
    return psycopg.connect(
        database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS, row_factory=dict_row
    )


def claim_asset(conn, asset_id: str) -> dict | None:
    """Porta l'immagine in PROCESSING e la restituisce, o None se non c'è niente da fare.

    Il messaggio si salta solo quando l'immagine è già in uno stato finale.
    PROCESSING **non** è una ragione per saltare: significa che qualcuno ha preso
    il lavoro, e se stiamo rivedendo il messaggio quel qualcuno è morto a metà.
    Trattare PROCESSING come «già fatto» lascerebbe l'immagine piantata lì per sempre, con
    il lavoro non è mai finito e il messaggio è stato confermato.

    Rifare il lavoro è sicuro perché ogni scrittura a valle è idempotente:
    chiavi deterministiche nel bucket, ON CONFLICT sulle varianti, sostituzione
    con inserimento sul documentale.
    """
    return conn.execute(
        """
        UPDATE assets
           SET status = 'PROCESSING', updated_at = now()
         WHERE id = %s AND status IN ('PENDING', 'PROCESSING')
        RETURNING id, mime, filename
        """,
        (asset_id,),
    ).fetchone()


def mark_done(conn, asset_id: str) -> None:
    conn.execute(
        """
        UPDATE assets
           SET status = 'DONE', error_message = NULL, updated_at = now()
         WHERE id = %s
        """,
        (asset_id,),
    )


def save_variants(conn, asset_id: str, variants: list[dict]) -> None:
    """Scrive le righe delle varianti, sovrascrivendo quelle già presenti.

    È ON CONFLICT a rendere sicura la rielaborazione: il vincolo UNIQUE
    (asset_id, kind) rifiuterebbe altrimenti il secondo tentativo, e un lavoro
    fallito a metà non potrebbe mai essere portato a termine.
    """
    for variant in variants:
        conn.execute(
            """
            INSERT INTO variants (asset_id, kind, storage_key, width, height, size_bytes)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id, kind) DO UPDATE
                SET storage_key = EXCLUDED.storage_key,
                    width       = EXCLUDED.width,
                    height      = EXCLUDED.height,
                    size_bytes  = EXCLUDED.size_bytes
            """,
            (
                asset_id,
                variant["kind"],
                variant["storage_key"],
                variant["width"],
                variant["height"],
                variant["size_bytes"],
            ),
        )


def mark_failed(conn, asset_id: str, message: str) -> dict | None:
    return conn.execute(
        """
        UPDATE assets
           SET status = 'FAILED', error_message = %s, updated_at = now()
         WHERE id = %s
        RETURNING id, user_id, filename
        """,
        (message, asset_id),
    ).fetchone()
