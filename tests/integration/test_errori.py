"""Ciò che il sistema deve rifiutare, e come.

Un sistema si giudica almeno quanto da ciò che fa, da ciò che si rifiuta di fare
e da come lo spiega. Ogni rifiuto qui ha un codice preciso e un messaggio in
italiano: il frontend li mostra così come arrivano.
"""

import pytest

pytestmark = pytest.mark.integration


# --- accesso -----------------------------------------------------------------

def test_la_stessa_email_non_si_registra_due_volte(api):
    dati = {"email": "doppia@example.com", "password": "password-lunga-di-prova"}
    api("POST", "/api/auth/register", dati)

    stato, corpo = api("POST", "/api/auth/register", dati)

    assert stato == 409
    assert corpo["error"]["code"] == "email_taken"


def test_una_password_sbagliata_non_dice_se_l_email_esiste(api):
    registrata = "esiste@example.com"
    api("POST", "/api/auth/register", {"email": registrata, "password": "password-lunga-di-prova"})

    sbagliata = {"password": "sbagliata-123"}
    _, email_giusta = api("POST", "/api/auth/login", {"email": registrata, **sbagliata})
    inventata = "mai-vista@example.com"
    _, email_inventata = api("POST", "/api/auth/login", {"email": inventata, **sbagliata})

    # Stessa risposta nei due casi: altrimenti basterebbe provare per scoprire
    # quali indirizzi sono registrati.
    assert email_giusta == email_inventata


@pytest.mark.parametrize(
    "password, codice",
    [("corta", "weak_password"), ("x" * 200, "password_too_long")],
)
def test_le_password_fuori_misura_vengono_rifiutate(api, password, codice):
    dati = {"email": "misura@example.com", "password": password}
    stato, corpo = api("POST", "/api/auth/register", dati)

    assert stato == 400
    assert corpo["error"]["code"] == codice


# --- caricamento -------------------------------------------------------------

def test_un_formato_non_ammesso_viene_rifiutato(api, token):
    stato, corpo = api(
        "POST", "/api/assets",
        {"filename": "disegno.svg", "mime": "image/svg+xml", "size_bytes": 1000},
        token=token,
    )

    assert stato == 415
    assert "JPEG" in corpo["error"]["message"]


def test_un_file_dichiarato_troppo_grande_viene_rifiutato(api, token):
    stato, corpo = api(
        "POST", "/api/assets",
        {"filename": "enorme.jpg", "mime": "image/jpeg", "size_bytes": 26 * 1024 * 1024},
        token=token,
    )

    assert stato == 413
    assert "25 MB" in corpo["error"]["message"]


def test_non_si_conferma_un_file_mai_caricato(api, token):
    _, creato = api(
        "POST", "/api/assets",
        {"filename": "fantasma.jpg", "mime": "image/jpeg", "size_bytes": 1000},
        token=token,
    )

    stato, corpo = api("POST", f"/api/assets/{creato['asset_id']}/complete", {}, token=token)

    assert stato == 409
    assert corpo["error"]["code"] == "upload_missing"


def test_una_seconda_conferma_non_crea_un_secondo_lavoro(api, token, carica, foto):
    asset_id = carica(token, "una-volta.jpg", foto)

    stato, corpo = api("POST", f"/api/assets/{asset_id}/complete", {}, token=token)

    assert stato == 409
    assert corpo["error"]["code"] == "already_submitted"


# --- elaborazione fallita ----------------------------------------------------

def test_un_file_che_non_e_un_immagine_finisce_in_errore_con_un_motivo(
    token, carica, attendi, file_finto
):
    asset_id = carica(token, "finto.jpg", file_finto)

    stato = attendi(token, asset_id)

    assert stato["status"] == "FAILED"
    assert stato["error_message"] == "Il file non è un'immagine leggibile"


def test_riprova_rimette_in_coda_un_immagine_fallita(api, token, carica, attendi, file_finto):
    asset_id = carica(token, "finto.jpg", file_finto)
    attendi(token, asset_id)

    stato, corpo = api("POST", f"/api/assets/{asset_id}/retry", {}, token=token)
    assert stato == 202
    assert corpo["status"] == "PENDING"

    # Il file resta rotto: deve fallire di nuovo, con lo stesso motivo.
    assert attendi(token, asset_id)["status"] == "FAILED"


def test_non_si_riprova_un_immagine_riuscita(api, token, immagine_pronta, foto):
    asset_id = immagine_pronta("riuscita.jpg", foto)

    stato, corpo = api("POST", f"/api/assets/{asset_id}/retry", {}, token=token)

    assert stato == 409
    assert corpo["error"]["code"] == "already_done"


def test_un_errore_arriva_anche_come_avviso(token, carica, attendi, avvisi, file_finto):
    asset_id = carica(token, "finto.jpg", file_finto)
    attendi(token, asset_id)

    trovati = avvisi(token, asset_id)

    assert trovati and trovati[0]["event"] == "asset.failed"
    assert "Riprova" in trovati[0]["body"]


# --- dati altrui -------------------------------------------------------------

def test_un_altro_utente_non_vede_l_immagine(api, token, altro_token, immagine_pronta, foto):
    asset_id = immagine_pronta("privata.jpg", foto)

    stato, _ = api("GET", f"/api/assets/{asset_id}", token=altro_token)

    # 404 e non 403: chi non è il proprietario non deve nemmeno poter dedurre
    # che quell'immagine esista.
    assert stato == 404


def test_un_altro_utente_non_puo_riprovarla_ne_confermarla(
    api, token, altro_token, carica, attendi, file_finto
):
    asset_id = carica(token, "privata.jpg", file_finto)
    attendi(token, asset_id)

    assert api("POST", f"/api/assets/{asset_id}/retry", {}, token=altro_token)[0] == 404
    assert api("POST", f"/api/assets/{asset_id}/complete", {}, token=altro_token)[0] == 404


def test_lo_stato_di_un_immagine_altrui_non_viene_restituito(
    api, token, altro_token, immagine_pronta, foto
):
    asset_id = immagine_pronta("privata.jpg", foto)

    _, corpo = api("GET", f"/api/assets/status?ids={asset_id}", token=altro_token)

    assert corpo["items"] == []


def test_un_token_manomesso_viene_respinto(api, token):
    stato, corpo = api("GET", "/api/assets", token=token + "x")

    assert stato == 401
    assert corpo["error"]["code"] == "invalid_token"
