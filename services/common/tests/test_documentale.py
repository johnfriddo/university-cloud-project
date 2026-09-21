"""Quale documentale: MongoDB in locale, DynamoDB su AWS.

La scelta è una variabile sola, e ciascuno dei due chiede soltanto le proprie.
È la stessa lezione delle credenziali dell'object storage: pretendere MONGO_URL
anche su AWS avrebbe impedito ai servizi di partire proprio là.
"""

import pytest

from media_common.documents import DocumentStoreSettings
from media_common.env import MissingConfiguration

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def ambiente_pulito(monkeypatch):
    for nome in ("METADATA_BACKEND", "METADATA_TABLE", "MONGO_URL", "MONGO_DATABASE"):
        monkeypatch.delenv(nome, raising=False)


def test_senza_indicazioni_si_usa_mongodb_come_in_locale(monkeypatch):
    monkeypatch.setenv("MONGO_URL", "mongodb://media:x@mongo:27017/media")

    impostazioni = DocumentStoreSettings.from_env()

    assert impostazioni.backend == "mongo"
    assert impostazioni.mongo_url == "mongodb://media:x@mongo:27017/media"


def test_con_mongodb_l_indirizzo_resta_obbligatorio():
    with pytest.raises(MissingConfiguration) as errore:
        DocumentStoreSettings.from_env()

    assert "MONGO_URL" in str(errore.value)


def test_con_dynamodb_l_indirizzo_di_mongo_non_serve(monkeypatch):
    monkeypatch.setenv("METADATA_BACKEND", "dynamodb")
    monkeypatch.setenv("METADATA_TABLE", "media-platform-metadata")

    impostazioni = DocumentStoreSettings.from_env()

    assert impostazioni.backend == "dynamodb"
    assert impostazioni.table == "media-platform-metadata"


def test_con_dynamodb_la_tabella_e_obbligatoria(monkeypatch):
    monkeypatch.setenv("METADATA_BACKEND", "dynamodb")

    with pytest.raises(MissingConfiguration) as errore:
        DocumentStoreSettings.from_env()

    assert "METADATA_TABLE" in str(errore.value)


def test_un_documentale_sconosciuto_viene_rifiutato_all_avvio(monkeypatch):
    # Un errore di battitura in un values.yaml: meglio fermarsi subito che
    # ripiegare in silenzio su MongoDB, che su AWS non c'è.
    monkeypatch.setenv("METADATA_BACKEND", "dynamo")

    with pytest.raises(MissingConfiguration) as errore:
        DocumentStoreSettings.from_env()

    assert "dynamodb" in str(errore.value)
