"""Destinatari e registro degli avvisi.

Una connessione per evento, aperta e chiusa attorno a esso: gestire un
messaggio dura millisecondi, quindi la stretta di mano costa poco, e non c'è
nessuna connessione di lunga durata che si guasti mentre il servizio aspetta.
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


def find_owner(conn, asset_id: str) -> dict | None:
    """Di chi è l'immagine, letto dall'immagine stessa.

    Deliberatamente non l'user_id che viaggia nel messaggio: il proprietario è un
    fatto del database, e leggerlo là significa che un evento malformato o vecchio
    non può indirizzare un avviso a qualcun altro. In una query sola si stabilisce anche
    se l'immagine esiste ancora — l'avviso la indica con una chiave esterna, e
    inserirne uno per un'immagine cancellata fallirebbe.

    Restituisce None quando non è rimasto nessuno da informare. Non è un guasto da
    ritentare: l'evento è semplicemente sopravvissuto a ciò di cui parlava.
    """
    return conn.execute(
        """
        SELECT u.id AS user_id, u.email
          FROM assets a
          JOIN users u ON u.id = a.user_id
         WHERE a.id = %s
        """,
        (asset_id,),
    ).fetchone()


def record(conn, *, user_id: str, asset_id: str, event: str, recipient: str,
           subject: str, body: str) -> str | None:
    """Scrive l'avviso, o restituisce None se c'era già.

    Il vincolo di unicità su (asset_id, event) è ciò che rende idempotente il
    consumatore: un messaggio riconsegnato perde la corsa contro la riga già
    scritta, e nessuno viene avvisato due volte della stessa cosa.
    """
    row = conn.execute(
        """
        INSERT INTO notifications (user_id, asset_id, event, recipient, subject, body)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (asset_id, event) DO NOTHING
        RETURNING id
        """,
        (user_id, asset_id, event, recipient, subject, body),
    ).fetchone()
    return None if row is None else str(row["id"])


def find_notification(conn, asset_id: str, event: str) -> dict | None:
    """L'avviso già scritto per questo evento, se c'è.

    Usato subito dopo un conflitto, per distinguere due situazioni diverse: una
    in cui il messaggio era già stato consegnato e non resta niente da fare, e
    una in cui era stato registrato ma la consegna non è mai andata a buon fine —
    che invece vale un altro tentativo.
    """
    return conn.execute(
        "SELECT id, sent_at FROM notifications WHERE asset_id = %s AND event = %s",
        (asset_id, event),
    ).fetchone()


def mark_sent(conn, notification_id: str) -> None:
    """Registra che il messaggio è stato consegnato al suo canale.

    Consegnato al canale, non ricevuto da una persona: in locale il canale è il
    registro, e nella casella di nessuno arriva niente. Scritto dopo la consegna e
    non prima, così una riga con sent_at ancora vuoto è esattamente l'aspetto che
    ha il fallimento di un canale.
    """
    conn.execute(
        "UPDATE notifications SET sent_at = now() WHERE id = %s AND sent_at IS NULL",
        (notification_id,),
    )
