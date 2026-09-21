"""Registrazione, accesso e identità di chi chiama."""

import logging
import re

import psycopg
from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth.security import require_auth
from app.auth.tokens import create_access_token
from app.db.pool import get_pool
from app.errors import error_response

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
# Il calcolo dell'impronta è lento di proposito, e lavora sull'intera password:
# senza un tetto, un megabyte di testo spedito come password comprerebbe a chi
# lo manda parecchi secondi del processore del server al prezzo di una richiesta.
MAX_PASSWORD_LENGTH = 128

# Confrontarsi con questa costa quanto confrontarsi con un'impronta vera. Si usa
# quando l'indirizzo non esiste, così un indirizzo sbagliato e una password
# sbagliata impiegano lo stesso tempo a rispondere: altrimenti il solo tempo di
# risposta direbbe a un attaccante quali indirizzi sono registrati.
_DUMMY_PASSWORD_HASH = generate_password_hash("password-that-nobody-uses")


@bp.post("/auth/register")
def register():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")

    if not EMAIL_PATTERN.match(email):
        return error_response(400, "invalid_email", "Indirizzo email non valido")
    if len(password) < MIN_PASSWORD_LENGTH:
        return error_response(
            400,
            "weak_password",
            f"La password deve avere almeno {MIN_PASSWORD_LENGTH} caratteri",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        return error_response(
            400,
            "password_too_long",
            f"La password non può superare {MAX_PASSWORD_LENGTH} caratteri",
        )

    # La password non viene mai salvata, solo una sua trasformazione a senso unico.
    # Anche leggendo l'intera tabella non c'è modo di tornare al testo originale.
    password_hash = generate_password_hash(password)

    try:
        with get_pool().connection() as conn:
            user = conn.execute(
                """
                INSERT INTO users (email, password_hash)
                VALUES (%s, %s)
                RETURNING id, email, created_at
                """,
                (email, password_hash),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        # L'unicità dell'indirizzo è garantita dal database, non da una SELECT
        # preventiva: due registrazioni simultanee non possono passare entrambe.
        return error_response(409, "email_taken", "Questa email è già registrata")

    log.info("registrato nuovo utente %s", user["id"])
    return jsonify(_public_user(user)), 201


@bp.post("/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")

    # Rifiutata prima di calcolarne l'impronta: nessuna password salvata può essere
    # così lunga, quindi la risposta è la stessa e il processore non ci viene speso.
    if len(password) > MAX_PASSWORD_LENGTH:
        return error_response(401, "invalid_credentials", "Email o password non corretti")

    with get_pool().connection() as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = %s", (email,)
        ).fetchone()

    stored_hash = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
    if not check_password_hash(stored_hash, password) or user is None:
        # Un messaggio solo per entrambi i casi: distinguerli permetterebbe a chiunque
        # di scoprire quali indirizzi hanno un account.
        return error_response(401, "invalid_credentials", "Email o password non corretti")

    settings = current_app.config["SETTINGS"]
    token = create_access_token(user["id"], settings.jwt_secret, settings.jwt_ttl_seconds)
    return jsonify(
        access_token=token,
        token_type="Bearer",
        expires_in=settings.jwt_ttl_seconds,
    )


@bp.get("/auth/me")
@require_auth
def me():
    with get_pool().connection() as conn:
        user = conn.execute(
            "SELECT id, email, created_at FROM users WHERE id = %s",
            (g.current_user_id,),
        ).fetchone()

    if user is None:
        # Il token è valido ma l'account non esiste più.
        return error_response(401, "unknown_user", "Utente non trovato")

    return jsonify(_public_user(user))


def _public_user(row) -> dict:
    """Dà forma alla riga di un utente per il client: l'impronta non esce dal servizio."""
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "created_at": row["created_at"].isoformat(),
    }
