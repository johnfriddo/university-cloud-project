"""I token di sessione.

Qui non si prova una comodità: si prova che nessuno possa entrare fingendo di
essere un altro. Vale la pena che ogni caso sia scritto nero su bianco.
"""

import datetime as dt

import jwt
import pytest

from app.auth.tokens import ALGORITHM, create_access_token, read_user_id

pytestmark = pytest.mark.unit

# Almeno 32 caratteri, come pretende la configurazione vera.
SEGRETO = "segreto-di-prova-lungo-abbastanza-per-hmac"
UTENTE = "11111111-2222-3333-4444-555555555555"


def test_un_token_appena_creato_dice_chi_e():
    token = create_access_token(UTENTE, SEGRETO, ttl_seconds=3600)

    assert read_user_id(token, SEGRETO) == UTENTE


def test_un_token_scaduto_viene_rifiutato():
    token = create_access_token(UTENTE, SEGRETO, ttl_seconds=-1)

    with pytest.raises(jwt.ExpiredSignatureError):
        read_user_id(token, SEGRETO)


def test_un_token_firmato_con_un_altro_segreto_viene_rifiutato():
    # È il caso che conta di più: senza la verifica della firma chiunque
    # potrebbe fabbricarsi un token con l'identificativo che preferisce.
    altro_segreto = "un-altro-segreto-lungo-abbastanza-per-hmac"
    token = create_access_token(UTENTE, altro_segreto, ttl_seconds=3600)

    with pytest.raises(jwt.InvalidSignatureError):
        read_user_id(token, SEGRETO)


def test_un_token_manomesso_viene_rifiutato():
    token = create_access_token(UTENTE, SEGRETO, ttl_seconds=3600)

    with pytest.raises(jwt.InvalidTokenError):
        read_user_id(token + "x", SEGRETO)


def test_un_token_senza_firma_viene_rifiutato():
    # L'attacco classico: si dichiara "algoritmo: nessuno" sperando che il
    # server si fidi. Funziona solo se il server non fissa l'algoritmo.
    falso = jwt.encode({"sub": UTENTE, "exp": 9999999999}, key="", algorithm="none")

    with pytest.raises(jwt.InvalidTokenError):
        read_user_id(falso, SEGRETO)


def test_l_algoritmo_e_fissato_nel_codice():
    assert ALGORITHM == "HS256"


def test_la_scadenza_e_davvero_quella_chiesta():
    token = create_access_token(UTENTE, SEGRETO, ttl_seconds=60)
    claims = jwt.decode(token, SEGRETO, algorithms=[ALGORITHM])

    durata = dt.datetime.fromtimestamp(claims["exp"], dt.UTC) - dt.datetime.fromtimestamp(
        claims["iat"], dt.UTC
    )
    assert durata == dt.timedelta(seconds=60)
