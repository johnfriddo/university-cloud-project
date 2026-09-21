"""La libreria: filtri, paginazione, e ciò che non deve comparire.

Tutti i test di questo file leggono la stessa libreria, preparata una volta sola
(vedi la fixture `libreria`): una foto Canon, una foto senza EXIF, un file finto
e una registrazione mai completata. I conteggi attesi discendono da lì.
"""

import datetime as dt

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def cerca(api, libreria):
    """La libreria filtrata: restituisce i nomi trovati, in ordine."""

    def esegui(**filtri):
        parametri = "&".join(f"{chiave}={valore}" for chiave, valore in filtri.items())
        stato, pagina = api("GET", f"/api/assets?{parametri}", token=libreria["token"])
        assert stato == 200, pagina
        return sorted(voce["filename"] for voce in pagina["items"])

    return esegui


# --- ciò che compare e ciò che no --------------------------------------------

def test_compaiono_solo_le_immagini_consegnate(cerca):
    # La registrazione mai completata non è un'immagine: non avrà mai una
    # miniatura e nessun worker la prenderà. Era uno dei difetti dell'audit.
    assert cerca() == ["reflex-canon.jpg", "rosso-semplice.jpg", "rotto.jpg"]


def test_le_immagini_fallite_restano_visibili(cerca):
    # Diversamente dalle registrazioni abbandonate: un'immagine fallita va
    # mostrata, perché l'utente deve vedere il motivo e poter premere Riprova.
    assert "rotto.jpg" in cerca()


# --- filtri sul relazionale --------------------------------------------------

def test_filtro_per_nome(cerca):
    assert cerca(filename="canon") == ["reflex-canon.jpg"]


def test_il_filtro_per_nome_ignora_maiuscole_e_minuscole(cerca):
    assert cerca(filename="CANON") == ["reflex-canon.jpg"]


def test_i_caratteri_jolly_nel_nome_sono_presi_alla_lettera(cerca):
    # "%" in SQL significa "qualunque cosa": senza protezione restituirebbe
    # tutta la libreria. Nessun nome contiene davvero un "%".
    assert cerca(filename="%25") == []


def test_filtro_per_stato(cerca):
    assert cerca(status="DONE") == ["reflex-canon.jpg", "rosso-semplice.jpg"]
    assert cerca(status="FAILED") == ["rotto.jpg"]


def test_filtro_per_data(cerca):
    oggi = dt.date.today()
    domani = oggi + dt.timedelta(days=1)

    assert len(cerca(**{"from": oggi.isoformat()})) == 3
    assert cerca(**{"from": domani.isoformat()}) == []


# --- filtri sul documentale --------------------------------------------------

def test_filtro_per_fotocamera(cerca):
    # Questa risposta attraversa entrambi i database: MongoDB trova gli
    # identificativi, PostgreSQL restituisce la pagina.
    assert cerca(camera="Canon") == ["reflex-canon.jpg"]
    assert cerca(camera="EOS") == ["reflex-canon.jpg"]
    assert cerca(camera="Nikon") == []


def test_filtro_per_obiettivo(cerca):
    assert cerca(lens="24-70") == ["reflex-canon.jpg"]


def test_la_focale_si_cerca_dall_inizio(cerca):
    # La foto è a 35mm. Cercare "35" deve trovarla; cercare "5" no — altrimenti
    # una ricerca per 50mm restituirebbe anche le foto a 150mm.
    assert cerca(focal_length="35") == ["reflex-canon.jpg"]
    assert cerca(focal_length="5") == []


def test_filtro_per_sensibilita(cerca):
    assert cerca(iso_min=800, iso_max=3200) == ["reflex-canon.jpg"]
    assert cerca(iso_max=400) == []


def test_filtri_dei_due_database_insieme(cerca):
    assert cerca(camera="Canon", status="DONE") == ["reflex-canon.jpg"]
    assert cerca(camera="Canon", status="FAILED") == []


# --- paginazione -------------------------------------------------------------

def test_la_paginazione_divide_e_conta_giusto(api, libreria):
    token = libreria["token"]

    _, prima = api("GET", "/api/assets?page=1&page_size=2", token=token)
    _, seconda = api("GET", "/api/assets?page=2&page_size=2", token=token)

    assert prima["total"] == 3
    assert prima["pages"] == 2
    assert len(prima["items"]) == 2
    assert len(seconda["items"]) == 1

    # Nessuna immagine compare su due pagine.
    ids_prima = {voce["id"] for voce in prima["items"]}
    ids_seconda = {voce["id"] for voce in seconda["items"]}
    assert not ids_prima & ids_seconda


def test_le_piu_recenti_vengono_prima(api, libreria):
    _, pagina = api("GET", "/api/assets", token=libreria["token"])
    date = [voce["created_at"] for voce in pagina["items"]]

    assert date == sorted(date, reverse=True)


@pytest.mark.parametrize(
    "parametri, codice",
    [
        ("status=QUALUNQUE", "invalid_status"),
        ("from=17-09-2026", "invalid_date"),
        ("iso_min=tanti", "invalid_iso"),
        ("page=prima", "invalid_pagination"),
    ],
)
def test_i_filtri_malformati_vengono_rifiutati_con_un_motivo(api, libreria, parametri, codice):
    stato, corpo = api("GET", f"/api/assets?{parametri}", token=libreria["token"])

    assert stato == 400
    assert corpo["error"]["code"] == codice


# --- l'endpoint del polling --------------------------------------------------

def test_lo_stato_di_piu_immagini_in_una_chiamata(api, libreria):
    ids = ",".join([libreria["canon"], libreria["rotto"]])

    _, corpo = api("GET", f"/api/assets/status?ids={ids}", token=libreria["token"])
    stati = {voce["id"]: voce["status"] for voce in corpo["items"]}

    assert stati == {libreria["canon"]: "DONE", libreria["rotto"]: "FAILED"}


def test_il_polling_non_restituisce_link(api, libreria):
    # È la ragione per cui questo endpoint esiste: un link firmato cambia a ogni
    # firma, e restituirlo ogni due secondi farebbe riscaricare tutte le
    # miniature della griglia.
    _, corpo = api("GET", f"/api/assets/status?ids={libreria['canon']}", token=libreria["token"])

    assert set(corpo["items"][0]) == {"id", "status", "error_message", "updated_at"}


@pytest.mark.parametrize("ids", ["", "non-un-identificativo"])
def test_il_polling_rifiuta_richieste_senza_senso(api, libreria, ids):
    stato, _ = api("GET", f"/api/assets/status?ids={ids}", token=libreria["token"])

    assert stato == 400
