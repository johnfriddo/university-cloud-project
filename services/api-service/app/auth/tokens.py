"""Creazione e verifica dei token d'accesso.

Un JWT porta con sé l'identificativo dell'utente e una scadenza, firmati con
un segreto che conosce solo il server. Il client non può alterarlo senza
invalidare la firma, e il server non ha bisogno di memorizzare nessuna
sessione: verifica la firma e legge l'identificativo dal token stesso. È
questo che permette all'api-service di avere più repliche senza condividere niente.
"""

import datetime as dt
from uuid import UUID

import jwt

ALGORITHM = "HS256"


def create_access_token(user_id: UUID | str, secret: str, ttl_seconds: int) -> str:
    issued_at = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + dt.timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def read_user_id(token: str, secret: str) -> str:
    """Restituisce l'identificativo dell'utente contenuto in un token valido.

    Altrimenti solleva jwt.ExpiredSignatureError oppure jwt.InvalidTokenError.
    """
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    return payload["sub"]
