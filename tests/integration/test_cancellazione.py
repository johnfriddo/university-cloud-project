"""Cancellare un'immagine, e tutto quello che le appartiene.

Tre archivi devono dimenticarla: il relazionale (la riga, e con lei le varianti
e gli avvisi), l'object storage (l'originale e le tre varianti) e il
documentale (i metadati tecnici). Un test che si fermasse al «204» non
proverebbe niente: proverebbe che l'API ha risposto, non che le cose sono
sparite.
"""

import pytest

pytestmark = pytest.mark.integration


def test_un_immagine_eliminata_non_e_piu_nel_dettaglio(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("da-eliminare.jpg", foto)

    stato, _ = api("DELETE", f"/api/assets/{asset_id}", token=token)
    assert stato == 204

    stato, corpo = api("GET", f"/api/assets/{asset_id}", token=token)
    assert stato == 404
    assert corpo["error"]["code"] == "asset_not_found"


def test_un_immagine_eliminata_sparisce_dalla_libreria(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("sparisce.jpg", foto)

    _, prima = api("GET", "/api/assets", token=token)
    assert asset_id in [voce["id"] for voce in prima["items"]]

    api("DELETE", f"/api/assets/{asset_id}", token=token)

    _, dopo = api("GET", "/api/assets", token=token)
    assert asset_id not in [voce["id"] for voce in dopo["items"]]


def test_i_file_delle_varianti_spariscono_davvero(api, token, immagine_pronta, foto):
    """La prova che conta: i link firmati, dopo, non trovano più niente.

    I link vengono presi **prima** della cancellazione e sono validi per un
    quarto d'ora: se rispondessero ancora, vorrebbe dire che la riga è sparita
    dal database ma i file sono rimasti nel bucket a occupare spazio.
    """
    asset_id = immagine_pronta("con-varianti.jpg", foto)
    _, dettaglio = api("GET", f"/api/assets/{asset_id}", token=token)

    link = [v["url"] for v in dettaglio["variants"]]
    link.append(dettaglio["original_download_url"])
    assert len(link) == 4

    for indirizzo in link:
        stato, _ = api("GET", url=indirizzo)
        assert stato == 200, "il link doveva funzionare prima della cancellazione"

    api("DELETE", f"/api/assets/{asset_id}", token=token)

    for indirizzo in link:
        stato, _ = api("GET", url=indirizzo)
        assert stato == 404, "il file è ancora nell'archivio dopo la cancellazione"


def test_i_metadati_tecnici_vengono_dimenticati(api, token, immagine_pronta, foto):
    """Il documentale è l'archivio che si dimentica più facilmente di pulire.

    Si verifica per differenza: la ricerca per fotocamera trovava l'immagine,
    dopo la cancellazione non deve trovare più niente di suo.
    """
    asset_id = immagine_pronta("con-exif.jpg", foto)

    _, prima = api("GET", "/api/assets?camera=Canon", token=token)
    assert asset_id in [voce["id"] for voce in prima["items"]]

    api("DELETE", f"/api/assets/{asset_id}", token=token)

    _, dopo = api("GET", "/api/assets?camera=Canon", token=token)
    assert asset_id not in [voce["id"] for voce in dopo["items"]]


def test_non_si_puo_eliminare_l_immagine_di_un_altro(
    api, token, altro_token, immagine_pronta, foto
):
    """E la risposta è «non esiste», non «non è tua».

    Dire a uno sconosciuto «esiste ma non è tua» è dirgli che esiste.
    """
    asset_id = immagine_pronta("non-e-tua.jpg", foto)

    stato, corpo = api("DELETE", f"/api/assets/{asset_id}", token=altro_token)
    assert stato == 404
    assert corpo["error"]["code"] == "asset_not_found"

    # E il proprietario la trova ancora al suo posto.
    stato, _ = api("GET", f"/api/assets/{asset_id}", token=token)
    assert stato == 200


def test_eliminare_due_volte_risponde_che_non_c_e_piu(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("due-volte.jpg", foto)

    assert api("DELETE", f"/api/assets/{asset_id}", token=token)[0] == 204
    assert api("DELETE", f"/api/assets/{asset_id}", token=token)[0] == 404


def test_eliminare_un_identificativo_inventato_non_e_un_errore_del_server(api, token):
    stato, corpo = api(
        "DELETE", "/api/assets/00000000-0000-0000-0000-000000000000", token=token
    )
    assert stato == 404
    assert corpo["error"]["code"] == "asset_not_found"


def test_gli_avvisi_dell_immagine_se_ne_vanno_con_lei(api, token, immagine_pronta, avvisi, foto):
    """Gli avvisi cadono in cascata: nessuno deve parlare di un'immagine che non c'è."""
    asset_id = immagine_pronta("con-avviso.jpg", foto)
    assert avvisi(token, asset_id), "l'avviso doveva esserci prima della cancellazione"

    api("DELETE", f"/api/assets/{asset_id}", token=token)

    _, elenco = api("GET", "/api/notifications", token=token)
    assert asset_id not in [voce["asset_id"] for voce in elenco["items"]]
