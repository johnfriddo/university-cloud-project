"""La casella: gli avvisi scritti dal notification-service.

Sola lettura. Questo servizio non scrive mai qui — un avviso lo produce
l'evento che l'ha causato, non chi chiede di vederlo — e il controllo di
proprietà è lo stesso di ovunque: ogni query filtra sull'identificativo di chi chiama.

In locale questo è l'unico canale che raggiunge davvero una persona: nello
stack non c'è nessun server di posta, quindi senza questo endpoint il lavoro
del notification-service sarebbe visibile soltanto nel suo registro.
"""

import datetime as dt
import logging

from flask import Blueprint, current_app, g, jsonify, request

from app.auth.security import require_auth
from app.db.pool import get_pool
from app.errors import error_response

log = logging.getLogger(__name__)

bp = Blueprint("notifications", __name__)


@bp.get("/notifications")
@require_auth
def list_notifications():
    """Una pagina degli avvisi di chi chiama, dal più recente.

    Con `since`, soltanto quelli scritti dopo quell'istante. È il conto che
    mostra il contrassegno nell'intestazione: il client ricorda quando ha
    guardato l'ultima volta e chiede quanto è successo da allora.
    """
    settings = current_app.config["SETTINGS"]

    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = int(request.args.get("page_size", settings.default_page_size))
    except ValueError:
        return error_response(400, "invalid_pagination", "Parametri di paginazione non validi")
    page_size = max(1, min(page_size, settings.max_page_size))

    conditions = ["n.user_id = %s"]
    params: list = [g.current_user_id]

    raw_since = request.args.get("since")
    if raw_since:
        since = _parse_instant(raw_since)
        if since is None:
            return error_response(400, "invalid_since", "Data 'since' non valida")
        conditions.append("n.created_at > %s")
        params.append(since)

    where = " AND ".join(conditions)

    with get_pool().connection() as conn:
        total = conn.execute(
            f"SELECT count(*) AS total FROM notifications n WHERE {where}", params
        ).fetchone()["total"]

        rows = conn.execute(
            f"""
            SELECT n.id, n.asset_id, n.event, n.subject, n.body,
                   n.sent_at, n.created_at, a.filename, a.status
              FROM notifications n
              JOIN assets a ON a.id = n.asset_id
             WHERE {where}
             ORDER BY n.created_at DESC
             LIMIT %s OFFSET %s
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()

    return jsonify(
        {
            "items": [
                {
                    "id": str(row["id"]),
                    "asset_id": str(row["asset_id"]),
                    "event": row["event"],
                    "subject": row["subject"],
                    "body": row["body"],
                    "filename": row["filename"],
                    "asset_status": row["status"],
                    # Consegnato al suo canale, non necessariamente letto da qualcuno:
                    # in locale quel canale è il registro del notification-service.
                    "sent_at": None if row["sent_at"] is None else row["sent_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
        }
    )


def _parse_instant(raw: str) -> dt.datetime | None:
    """Un istante in formato ISO 8601, inteso sempre come UTC quando non dice altro."""
    try:
        parsed = dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed
