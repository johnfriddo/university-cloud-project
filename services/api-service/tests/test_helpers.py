"""Le funzioni che ripuliscono e interpretano ciò che arriva dal client.

Sono poche righe ciascuna, ma stanno sul confine fra il mondo esterno e il
nostro: è dove un nome di file può tentare di diventare un percorso e una
ricerca può tentare di diventare un carattere jolly.
"""

import datetime as dt

import pytest

from app.assets.routes import _clean_filename, _escape_like, _megabytes, _parse_date
from app.notifications.routes import _parse_instant

pytestmark = pytest.mark.unit


# --- nome del file -----------------------------------------------------------

def test_un_nome_normale_resta_com_e():
    assert _clean_filename("tramonto.jpg") == "tramonto.jpg"


def test_un_nome_che_tenta_di_essere_un_percorso_perde_il_percorso():
    # Il nome scelto dall'utente è testo arbitrario: non deve mai poter
    # decidere dove finisce un file.
    assert _clean_filename("../../etc/passwd") == "passwd"
    assert _clean_filename("/etc/shadow") == "shadow"
    assert _clean_filename("C:\\Windows\\system32\\calc.exe") == "calc.exe"


def test_un_nome_lunghissimo_viene_accorciato():
    assert len(_clean_filename("a" * 500 + ".jpg")) == 255


def test_gli_spazi_attorno_non_contano():
    assert _clean_filename("  foto.jpg  ") == "foto.jpg"


# --- ricerca per nome --------------------------------------------------------

def test_i_caratteri_jolly_vengono_cercati_alla_lettera():
    # Senza questo, cercare "%" restituirebbe l'intera libreria invece dei soli
    # nomi che contengono davvero quel carattere.
    assert _escape_like("100%") == "100\\%"
    assert _escape_like("foto_1") == "foto\\_1"
    assert _escape_like("a\\b") == "a\\\\b"


def test_un_testo_normale_non_viene_toccato():
    assert _escape_like("tramonto") == "tramonto"


# --- date --------------------------------------------------------------------

def test_una_data_valida_diventa_un_istante_con_fuso():
    letta = _parse_date("2026-09-17")

    assert letta == dt.datetime(2026, 9, 17, tzinfo=dt.UTC)


@pytest.mark.parametrize("scritta", ["17/09/2026", "2026-13-45", "ieri", ""])
def test_una_data_non_valida_vale_niente(scritta):
    assert _parse_date(scritta) is None


def test_un_istante_senza_fuso_e_inteso_in_utc():
    # Il client manda quello che ha: se non dichiara il fuso, interpretarlo
    # nell'ora del server darebbe conteggi diversi a seconda di dove gira.
    assert _parse_instant("2026-09-17T10:00:00") == dt.datetime(2026, 9, 17, 10, tzinfo=dt.UTC)


def test_la_z_finale_e_accettata():
    # È la forma che produce JavaScript con toISOString(), cioè quella che il
    # frontend manda davvero.
    assert _parse_instant("2026-09-17T10:00:00Z") == dt.datetime(2026, 9, 17, 10, tzinfo=dt.UTC)


def test_un_fuso_esplicito_viene_rispettato():
    letto = _parse_instant("2026-09-17T12:00:00+02:00")

    assert letto.astimezone(dt.UTC) == dt.datetime(2026, 9, 17, 10, tzinfo=dt.UTC)


def test_un_istante_illeggibile_vale_niente():
    assert _parse_instant("poco fa") is None


# --- varie -------------------------------------------------------------------

def test_i_byte_diventano_megabyte_nel_messaggio_all_utente():
    assert _megabytes(25 * 1024 * 1024) == 25
