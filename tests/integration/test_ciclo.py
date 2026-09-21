"""Il viaggio completo di un'immagine, come lo vede l'utente.

Caricamento in tre passi, elaborazione nel worker, tre varianti nell'archivio,
dati EXIF nel documentale, stato nel relazionale, avviso dal
notification-service. Se questo file passa, tutti i pezzi del sistema si
parlano come devono.
"""

import io
import time

import pytest
from PIL import Image

pytestmark = pytest.mark.integration


def test_una_foto_caricata_viene_elaborata_fino_in_fondo(token, carica, attendi, foto):
    asset_id = carica(token, "tramonto.jpg", foto)

    stato = attendi(token, asset_id)

    assert stato["status"] == "DONE"
    assert stato["error_message"] is None


def test_la_conferma_risponde_subito_senza_aspettare_l_elaborazione(api, token, foto):
    # È la promessa dell'architettura asincrona: l'API accetta il lavoro e
    # risponde, l'elaborazione avviene altrove. Se questa risposta aspettasse
    # il worker, duecento foto insieme sarebbero impossibili.
    stato, creato = api(
        "POST", "/api/assets",
        {"filename": "veloce.jpg", "mime": "image/jpeg", "size_bytes": len(foto)},
        token=token,
    )
    invio = creato["upload"]
    api("PUT", raw=foto, headers=invio["headers"], url=invio["url"])

    inizio = time.monotonic()
    stato, corpo = api("POST", f"/api/assets/{creato['asset_id']}/complete", {}, token=token)
    durata = time.monotonic() - inizio

    assert stato == 202
    assert corpo["status"] == "PENDING"
    assert durata < 2, f"la conferma ha impiegato {durata:.1f} s"


def test_nascono_tre_varianti_con_le_misure_previste(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("tramonto.jpg", foto)

    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)
    misure = {v["kind"]: (v["width"], v["height"]) for v in dettaglio["variants"]}

    # L'originale è 1800x1200: il lato lungo scende a 300, 800 e 1200.
    assert misure == {"thumb": (300, 200), "medium": (800, 533), "large": (1200, 800)}


def test_le_varianti_sono_davvero_nell_archivio(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("tramonto.jpg", foto)
    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)

    for variante in dettaglio["variants"]:
        stato, contenuto = api("GET", url=variante["url"])
        assert stato == 200, variante["kind"]

        # Non basta che il link risponda: dentro ci dev'essere un JPEG con le
        # dimensioni dichiarate nel database.
        letta = Image.open(io.BytesIO(contenuto))
        assert letta.format == "JPEG"
        assert letta.size == (variante["width"], variante["height"])


def test_i_dati_exif_arrivano_fino_alla_pagina_di_dettaglio(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("tramonto.jpg", foto)

    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)
    exif = dettaglio["metadata"]["exif"]

    assert exif["make"] == "Canon"
    assert exif["model"] == "EOS 5D Mark IV"
    assert exif["aperture"] == "f/2.8"
    assert exif["exposure"] == "1/250"
    assert exif["iso"] == 1600
    assert dettaglio["metadata"]["dimensions"] == {"width": 1800, "height": 1200}
    assert dettaglio["metadata"]["checksum"].startswith("sha256:")


def test_un_immagine_senza_exif_ha_una_scheda_vuota(api, token, immagine_pronta, foto_senza_exif):
    asset_id = immagine_pronta("rosso.jpg", foto_senza_exif)

    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)

    assert dettaglio["metadata"]["exif"] == {}


def test_ogni_variante_si_scarica_con_un_nome_leggibile(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("città al tramonto.jpg", foto)
    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)

    for variante in dettaglio["variants"]:
        stato, _ = api("GET", url=variante["download_url"])
        assert stato == 200

    # Il nome viaggia dentro l'indirizzo firmato: si può leggere lì, senza dover
    # ispezionare le intestazioni della risposta.
    media = next(v for v in dettaglio["variants"] if v["kind"] == "medium")
    assert "citt%25C3%25A0%2520al%2520tramonto-medium.jpg" in media["download_url"]


def test_l_anteprima_si_mostra_e_non_si_scarica(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("tramonto.jpg", foto)
    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)

    for variante in dettaglio["variants"]:
        assert "response-content-disposition" not in variante["url"]


def test_a_lavoro_finito_arriva_un_avviso(token, immagine_pronta, avvisi, foto):
    asset_id = immagine_pronta("tramonto.jpg", foto)

    trovati = avvisi(token, asset_id)

    assert len(trovati) == 1
    assert trovati[0]["event"] == "asset.processed"
    assert trovati[0]["subject"] == "«tramonto.jpg» è pronta"
    assert trovati[0]["sent_at"] is not None
