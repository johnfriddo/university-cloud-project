"""La configurazione obbligatoria, verificata all'avvio.

Era uno dei nove difetti dell'audit: una variabile mancante veniva letta come
stringa vuota e il servizio partiva lo stesso, superava la sonda di prontezza e
si rompeva alla prima richiesta. Questi test impediscono che torni.
"""

import pytest

from app.config import Settings
from media_common.env import MissingConfiguration, required

pytestmark = pytest.mark.unit


def test_una_variabile_presente_viene_letta(monkeypatch):
    monkeypatch.setenv("PROVA_CONFIG", "un-valore")

    assert required("PROVA_CONFIG") == "un-valore"


def test_gli_spazi_attorno_vengono_tolti(monkeypatch):
    monkeypatch.setenv("PROVA_CONFIG", "  un-valore  ")

    assert required("PROVA_CONFIG") == "un-valore"


def test_una_variabile_mancante_ferma_tutto(monkeypatch):
    monkeypatch.delenv("PROVA_CONFIG", raising=False)

    with pytest.raises(MissingConfiguration) as errore:
        required("PROVA_CONFIG")

    # Il messaggio deve dire *quale* variabile manca: è l'unica informazione
    # utile a chi legge il log di un container che non parte.
    assert "PROVA_CONFIG" in str(errore.value)


def test_una_variabile_vuota_conta_come_mancante(monkeypatch):
    # Una variabile impostata a stringa vuota è il caso tipico di un Secret
    # compilato male: sembra presente e non lo è.
    monkeypatch.setenv("PROVA_CONFIG", "   ")

    with pytest.raises(MissingConfiguration):
        required("PROVA_CONFIG")


def test_una_chiave_troppo_corta_viene_rifiutata(monkeypatch):
    # Una firma HMAC-SHA256 con una chiave sotto i 32 caratteri è considerata
    # debole: il servizio funzionerebbe lo stesso, ed è proprio questo il
    # problema — nessuno se ne accorgerebbe.
    monkeypatch.setenv("PROVA_CHIAVE", "troppo-corta")

    with pytest.raises(MissingConfiguration) as errore:
        required("PROVA_CHIAVE", min_length=32)

    assert "32" in str(errore.value)


def test_una_chiave_abbastanza_lunga_passa(monkeypatch):
    monkeypatch.setenv("PROVA_CHIAVE", "x" * 32)

    assert required("PROVA_CHIAVE", min_length=32) == "x" * 32


# --- la configurazione intera, nei due ambienti ------------------------------


def _ambiente_minimo(monkeypatch):
    """Le sole variabili senza cui nessun ambiente può funzionare."""
    for nome, valore in {
        "DATABASE_URL": "postgresql://media:x@postgres:5432/media",
        "MONGO_URL": "mongodb://media:x@mongo:27017/media?authSource=admin",
        "RABBITMQ_URL": "amqp://media:x@rabbitmq:5672/",
        "S3_ENDPOINT_PUBLIC": "http://media-platform.test",
        "JWT_SECRET": "x" * 48,
    }.items():
        monkeypatch.setenv(nome, valore)


def test_la_configurazione_si_costruisce_come_in_locale(monkeypatch):
    _ambiente_minimo(monkeypatch)
    monkeypatch.setenv("S3_ENDPOINT_INTERNAL", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "una-password")

    impostazioni = Settings.from_env()

    assert impostazioni.s3_access_key == "minioadmin"
    assert impostazioni.s3_endpoint_internal == "http://minio:9000"


def test_la_configurazione_si_costruisce_anche_senza_credenziali(monkeypatch):
    """Il caso AWS, ed è il motivo per cui questo test esiste.

    Su EKS le credenziali dell'object storage non si passano: il pod assume un
    ruolo (IRSA) e la libreria trova da sé le chiavi temporanee. Anche
    l'indirizzo sparisce, perché S3 vero la libreria sa già dov'è.

    Finché queste variabili erano dichiarate obbligatorie, il servizio si
    sarebbe rifiutato di partire proprio là.
    """
    _ambiente_minimo(monkeypatch)
    for nome in ("S3_ENDPOINT_INTERNAL", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(nome, raising=False)

    impostazioni = Settings.from_env()

    assert impostazioni.s3_access_key == ""
    assert impostazioni.s3_secret_key == ""
    assert impostazioni.s3_endpoint_internal == ""
    # E tutto il resto resta obbligatorio: l'apertura non è un allentamento
    # generale.
    assert impostazioni.database_url.startswith("postgresql://")


def test_su_aws_non_servono_ne_mongodb_ne_indirizzi_dell_archivio(monkeypatch):
    """La configurazione che il chart passa su EKS: DynamoDB e S3 vero."""
    _ambiente_minimo(monkeypatch)
    monkeypatch.delenv("MONGO_URL", raising=False)
    for nome in ("S3_ENDPOINT_INTERNAL", "S3_ENDPOINT_PUBLIC", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("METADATA_BACKEND", "dynamodb")
    monkeypatch.setenv("METADATA_TABLE", "media-platform-metadata")

    impostazioni = Settings.from_env()

    assert impostazioni.documents.backend == "dynamodb"
    assert impostazioni.s3_endpoint_public == ""


def test_con_minio_l_indirizzo_pubblico_resta_obbligatorio(monkeypatch):
    # Con un indirizzo interno e senza quello pubblico, i link firmati
    # funzionerebbero per i servizi e per nessun browser.
    _ambiente_minimo(monkeypatch)
    monkeypatch.setenv("S3_ENDPOINT_INTERNAL", "http://minio:9000")
    monkeypatch.delenv("S3_ENDPOINT_PUBLIC", raising=False)

    with pytest.raises(MissingConfiguration) as errore:
        Settings.from_env()

    assert "S3_ENDPOINT_PUBLIC" in str(errore.value)


def test_una_sola_credenziale_ferma_comunque_l_avvio(monkeypatch):
    _ambiente_minimo(monkeypatch)
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)

    with pytest.raises(MissingConfiguration) as errore:
        Settings.from_env()

    assert "S3_SECRET_KEY" in str(errore.value)
