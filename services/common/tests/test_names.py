"""Il nome con cui il browser salva un file scaricato.

Prova d'esempio della tappa A: serve anche a dimostrare che l'impalcatura
funziona, cioè che un test riesce a importare il codice di un servizio senza
che sia acceso niente.
"""

import pytest

from media_common.storage import content_disposition

pytestmark = pytest.mark.unit


def test_nome_semplice_resta_intatto():
    header = content_disposition("tramonto.jpg")

    assert 'filename="tramonto.jpg"' in header
    assert header.startswith("attachment;")


def test_nome_con_accenti_viaggia_in_due_forme():
    # La forma semplice perde l'accento perché deve restare ASCII; quella
    # codificata lo conserva, ed è quella che i browser moderni leggono.
    header = content_disposition("città al tramonto.jpg")

    assert 'filename="citta_al_tramonto.jpg"' in header
    assert "filename*=UTF-8''citt%C3%A0%20al%20tramonto.jpg" in header


def test_le_virgolette_non_rompono_l_intestazione():
    # Una virgoletta non protetta chiuderebbe il valore a metà, e il resto del
    # nome diventerebbe sintassi.
    header = content_disposition('foto "strana".png')

    assert 'filename="foto__strana_.png"' in header
    assert header.count('"') == 2


def test_un_nome_senza_caratteri_utili_ha_un_ripiego():
    assert 'filename="immagine"' in content_disposition("...")
