"""Lettura dei dati tecnici che la fotocamera scrive nel file.

È la parte del worker con più trappole: l'EXIF non contiene numeri normali ma
frazioni, stringhe terminate da zeri e sequenze, e ogni marca scrive un insieme
diverso di campi. Quasi tutti i difetti trovati a mano nella Fase 2 stavano qui.
"""

import io

import pytest
from PIL import Image

from worker.exif import (
    _format_aperture,
    _format_exposure,
    _format_focal_length,
    _format_taken_at,
    _to_number,
    extract_exif,
)

pytestmark = pytest.mark.unit


# --- le frazioni ---------------------

def test_una_frazione_diventa_il_suo_valore():
    # L'EXIF scrive il diaframma f/2.8 come la coppia (28, 10). Letta come una
    # sequenza qualunque darebbe 28, ed è esattamente come f/2.8 era diventato
    # "f/28" prima che il difetto venisse corretto.
    assert _to_number((28, 10)) == pytest.approx(2.8)
    assert _to_number((1, 250)) == pytest.approx(0.004)


def test_una_frazione_con_denominatore_zero_non_fa_esplodere_niente():
    assert _to_number((1, 0)) is None


def test_un_valore_illeggibile_vale_niente():
    assert _to_number("non un numero") is None
    assert _to_number(None) is None


# --- come i valori vengono mostrati -----------------------------------------

def test_il_tempo_di_posa_si_legge_come_una_frazione():
    # 0,004 secondi non dice niente a un fotografo; 1/250 sì.
    assert _format_exposure(0.004) == "1/250"


def test_una_posa_lunga_resta_in_secondi():
    assert _format_exposure(2.0) == "2s"


def test_il_diaframma_ha_il_prefisso_giusto():
    assert _format_aperture(2.8) == "f/2.8"
    # Senza :g sarebbe "f/4.0", che nessuno scrive così.
    assert _format_aperture(4.0) == "f/4"


def test_la_focale_porta_l_unita():
    assert _format_focal_length(35.0) == "35mm"


def test_la_data_di_scatto_diventa_ordinabile():
    # L'EXIF usa i due punti anche nella data: così com'è non si ordina e non si
    # confronta.
    assert _format_taken_at("2026:05:02 09:15:00") == "2026-05-02T09:15:00"


def test_una_data_senza_senso_viene_scartata():
    # Meglio nessun campo che un campo sbagliato: chi filtra per data non deve
    # trovare valori inventati.
    assert _format_taken_at("ieri pomeriggio") is None
    assert _format_taken_at("") is None


# --- lettura da un file vero -------------------------------------------------

def _foto_con_exif() -> bytes:
    """Un JPEG con dentro i dati di scatto, come lo scriverebbe una reflex."""
    immagine = Image.new("RGB", (400, 300), (90, 120, 160))

    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "EOS 5D Mark IV"

    blocco = exif.get_ifd(0x8769)
    blocco[0xA434] = "EF 24-70mm f/2.8L"
    blocco[0x8827] = 1600          # ISO
    blocco[0x829D] = (28, 10)      # diaframma
    blocco[0x829A] = (1, 250)      # tempo di posa
    blocco[0x920A] = (35, 1)       # focale
    blocco[0x9003] = "2026:05:02 09:15:00"

    buffer = io.BytesIO()
    immagine.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def test_i_campi_previsti_vengono_estratti_tutti():
    dati = extract_exif(Image.open(io.BytesIO(_foto_con_exif())))

    assert dati == {
        "make": "Canon",
        "model": "EOS 5D Mark IV",
        "lens": "EF 24-70mm f/2.8L",
        "iso": 1600,
        "aperture": "f/2.8",
        "exposure": "1/250",
        "focal_length": "35mm",
        "taken_at": "2026-05-02T09:15:00",
    }


def test_un_immagine_senza_exif_non_inventa_campi():
    # Normale per un'immagine scaricata dal web o uscita da un programma di
    # fotoritocco. Il documento resta vuoto, non pieno di valori nulli.
    immagine = Image.new("RGB", (50, 50), "white")

    assert extract_exif(immagine) == {}
