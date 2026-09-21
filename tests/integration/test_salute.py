"""Lo stack di prova risponde ed è pronto a lavorare.

Prova d'esempio della tappa A. È anche il test più utile da leggere per primo:
se fallisce questo, ogni altro test di integrazione fallirà per lo stesso
motivo, e conviene guardare qui prima che altrove.
"""

import pytest

pytestmark = pytest.mark.integration


def test_api_viva(api):
    stato, corpo = api("GET", "/api/health/live")

    assert stato == 200
    assert corpo["service"] == "api-service"


def test_api_pronta_con_tutte_le_dipendenze(api):
    stato, corpo = api("GET", "/api/health/ready")

    # Il messaggio d'errore elenca quale dipendenza non risponde: senza, un
    # fallimento qui direbbe solo "503" e toccherebbe indagare a mano.
    assert stato == 200, corpo
    assert corpo["checks"]["postgres"]["ok"]
    assert corpo["checks"]["rabbitmq"]["ok"]
    assert corpo["checks"]["object_storage"]["ok"]


def test_senza_token_le_rotte_private_rifiutano(api):
    stato, _ = api("GET", "/api/assets")

    assert stato == 401
