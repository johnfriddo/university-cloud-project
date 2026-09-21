"""Generazione delle tre versioni derivate.

Regole facili da rompere per sbaglio con una modifica innocente: le proporzioni,
il divieto di ingrandire, la trasparenza appoggiata su bianco e la rotazione
registrata dalla fotocamera.
"""

import io

import pytest
from PIL import Image

from worker.images import InvalidImageError, generate_variants, open_image

pytestmark = pytest.mark.unit

FILIGRANA = "media-platform"


def _jpeg(width: int, height: int, colore=(120, 90, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colore).save(buffer, "JPEG")
    return buffer.getvalue()


# --- dimensioni --------------------------------------------------------------

def test_le_tre_versioni_hanno_i_lati_lunghi_previsti():
    varianti = generate_variants(open_image(_jpeg(3000, 2000)), FILIGRANA)

    misure = {v.kind: max(v.width, v.height) for v in varianti}
    assert misure == {"thumb": 300, "medium": 800, "large": 1200}


def test_le_proporzioni_non_vengono_mai_toccate():
    originale = open_image(_jpeg(3000, 2000))  # 3:2
    varianti = generate_variants(originale, FILIGRANA)

    for variante in varianti:
        assert variante.width / variante.height == pytest.approx(3 / 2, rel=0.01)


def test_un_immagine_verticale_resta_verticale():
    varianti = generate_variants(open_image(_jpeg(2000, 3000)), FILIGRANA)

    for variante in varianti:
        assert variante.height > variante.width


def test_un_originale_piccolo_non_viene_ingrandito():
    # Ingrandire non aggiunge dettaglio: aggiunge solo byte e sfocatura.
    varianti = {v.kind: v for v in generate_variants(open_image(_jpeg(200, 150)), FILIGRANA)}

    assert (varianti["large"].width, varianti["large"].height) == (200, 150)
    assert (varianti["medium"].width, varianti["medium"].height) == (200, 150)
    # Nemmeno la miniatura: il lato lungo dell'originale è 200, sotto i 300 a
    # cui punta. Tutte e tre restano identiche all'originale.
    assert (varianti["thumb"].width, varianti["thumb"].height) == (200, 150)


# --- colore e trasparenza ----------------------------------------------------

def test_la_trasparenza_finisce_su_bianco_e_non_su_nero():
    # Il JPEG non ha trasparenza: senza questo passaggio le zone trasparenti
    # diventerebbero nere, che è il difetto classico.
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 400), (255, 0, 0, 0)).save(buffer, "PNG")

    varianti = generate_variants(open_image(buffer.getvalue()), FILIGRANA)
    miniatura = Image.open(io.BytesIO(varianti[0].data))

    rosso, verde, blu = miniatura.convert("RGB").getpixel((5, 5))
    assert (rosso, verde, blu) > (240, 240, 240)


def test_ogni_variante_esce_in_jpeg():
    for variante in generate_variants(open_image(_jpeg(1000, 800)), FILIGRANA):
        assert variante.content_type == "image/jpeg"
        assert Image.open(io.BytesIO(variante.data)).format == "JPEG"


# --- rotazione ---------------------------------------------------------------

def test_la_rotazione_scritta_nell_exif_viene_applicata():
    # Le fotocamere spesso non ruotano il file: annotano "va girata di 90
    # gradi". Senza applicarla, metà delle foto verticali finirebbe coricata.
    immagine = Image.new("RGB", (400, 200), (10, 20, 30))
    exif = immagine.getexif()
    exif[0x0112] = 6  # ruota di 90 gradi in senso orario

    buffer = io.BytesIO()
    immagine.save(buffer, "JPEG", exif=exif)

    raddrizzata = open_image(buffer.getvalue())
    assert (raddrizzata.width, raddrizzata.height) == (200, 400)


# --- filigrana ---------------------------------------------------------------

def test_solo_la_versione_grande_porta_la_filigrana():
    varianti = {v.kind: v for v in generate_variants(open_image(_jpeg(2000, 1500)), FILIGRANA)}

    def pixel_diversi(a, b):
        prima = Image.open(io.BytesIO(a)).convert("RGB")
        dopo = Image.open(io.BytesIO(b)).convert("RGB")
        return sum(1 for x, y in zip(prima.getdata(), dopo.getdata(), strict=True) if x != y)

    senza_filigrana = generate_variants(open_image(_jpeg(2000, 1500)), "")
    grande_pulita = next(v for v in senza_filigrana if v.kind == "large")

    assert pixel_diversi(grande_pulita.data, varianti["large"].data) > 0


# --- file che immagini non sono ---------------------------------------------

def test_un_file_che_non_e_un_immagine_viene_rifiutato_con_un_motivo():
    with pytest.raises(InvalidImageError) as errore:
        open_image(b"questo non e un'immagine" * 10)

    # Il messaggio finisce sotto gli occhi dell'utente nella libreria: deve
    # essere in italiano e comprensibile.
    assert "immagine" in str(errore.value).lower()


def test_un_jpeg_troncato_viene_rifiutato():
    intero = _jpeg(800, 600)

    with pytest.raises(InvalidImageError):
        open_image(intero[: len(intero) // 3])
