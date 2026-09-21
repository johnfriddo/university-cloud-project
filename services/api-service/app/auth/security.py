"""La guardia applicata a ogni endpoint che lavora sui dati di un utente."""

from functools import wraps

import jwt
from flask import current_app, g, request

from app.auth.tokens import read_user_id
from app.errors import error_response

BEARER_PREFIX = "Bearer "


def require_auth(view):
    """Rifiuta la richiesta se non porta un token valido.

    Quando va a buon fine mette l'identificativo di chi chiama in
    `g.current_user_id`, che ogni query usa poi come filtro: un utente può
    raggiungere soltanto le proprie righe.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith(BEARER_PREFIX):
            return error_response(
                401, "missing_token", "Autenticazione richiesta"
            )

        token = header[len(BEARER_PREFIX) :].strip()
        secret = current_app.config["SETTINGS"].jwt_secret
        try:
            g.current_user_id = read_user_id(token, secret)
        except jwt.ExpiredSignatureError:
            return error_response(
                401, "token_expired", "Sessione scaduta, esegui di nuovo il login"
            )
        except jwt.InvalidTokenError:
            return error_response(401, "invalid_token", "Token non valido")

        return view(*args, **kwargs)

    return wrapper
