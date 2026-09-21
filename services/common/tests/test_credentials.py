"""Le credenziali dell'object storage: obbligatorie in locale, assenti su AWS.

Nasce da un difetto vero, trovato rileggendo il codice prima della Fase 7.
`S3_ACCESS_KEY` e `S3_SECRET_KEY` erano dichiarate obbligatorie, e su AWS non
esistono: il pod assume un ruolo (IRSA) e la libreria trova da sé le chiavi
temporanee. Così com'era, il servizio non sarebbe partito proprio
nell'ambiente verso cui il progetto sta andando.
"""

import pytest

from media_common.env import MissingConfiguration, storage_credentials

pytestmark = pytest.mark.unit


def test_le_due_chiavi_insieme_vengono_restituite(monkeypatch):
    monkeypatch.setenv("ACCESSO", "  minioadmin  ")
    monkeypatch.setenv("SEGRETO", "una-password")

    # Gli spazi ai lati spariscono: in un file .env ci finiscono per sbaglio.
    assert storage_credentials("ACCESSO", "SEGRETO") == ("minioadmin", "una-password")


def test_nessuna_delle_due_e_lecito_ed_e_il_caso_di_aws(monkeypatch):
    monkeypatch.delenv("ACCESSO", raising=False)
    monkeypatch.delenv("SEGRETO", raising=False)

    # Due stringhe vuote, non un errore: chi le riceve passa `None` alla
    # libreria, che a quel punto cerca le credenziali nell'ambiente.
    assert storage_credentials("ACCESSO", "SEGRETO") == ("", "")


def test_due_variabili_vuote_valgono_come_assenti(monkeypatch):
    monkeypatch.setenv("ACCESSO", "   ")
    monkeypatch.setenv("SEGRETO", "")

    assert storage_credentials("ACCESSO", "SEGRETO") == ("", "")


@pytest.mark.parametrize(
    ("impostata", "mancante"),
    [("ACCESSO", "SEGRETO"), ("SEGRETO", "ACCESSO")],
)
def test_una_sola_delle_due_e_un_errore_e_nomina_quella_che_manca(
    monkeypatch, impostata, mancante
):
    """Quasi sempre è un nome scritto male in un Secret.

    Lasciarla passare significherebbe far fallire la prima chiamata
    all'archivio con un messaggio della libreria che non nomina nessuna
    variabile: il momento peggiore per scoprirlo.
    """
    monkeypatch.setenv(impostata, "un valore")
    monkeypatch.delenv(mancante, raising=False)

    with pytest.raises(MissingConfiguration) as errore:
        storage_credentials("ACCESSO", "SEGRETO")

    assert mancante in str(errore.value)
