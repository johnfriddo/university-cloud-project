"""Attrezzi comuni ai test di integrazione.

Solo libreria standard per le richieste: nessun client HTTP da installare. Le
chiamate che i test fanno sono le stesse che farebbe il browser, e vederle
scritte per esteso aiuta a capire cosa il sistema si aspetta davvero.

Ogni test che crea dati lavora con un **utente nuovo**. Costa una registrazione
in più, e in cambio la libreria di quel test contiene solo ciò che ha caricato
lui: i filtri si possono verificare contando i risultati, senza dipendere da
quello che hanno lasciato in giro gli altri test.
"""

import base64
import http.client
import io
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest
from PIL import Image

TIMEOUT_SECONDS = 20
PASSWORD = "password-lunga-di-prova"

# Un'elaborazione dura un paio di secondi; il tetto è largo perché una macchina
# carica può metterci di più, e un test che fallisce per lentezza non dice nulla.
ATTESA_MASSIMA_SECONDI = 60


# --- come si parla con l'API -------------------------------------------------

@pytest.fixture(scope="session")
def base_url() -> str:
    """Dove risponde l'API.

    Dentro la rete di compose il nome del servizio basta. La variabile esiste
    perché lo stesso test possa girare, se serve, contro un indirizzo diverso.
    """
    return os.getenv("API_BASE", "http://api-service:8000").rstrip("/")


@pytest.fixture(scope="session")
def api(base_url):
    """Una chiamata HTTP, e la risposta già interpretata.

    Restituisce sempre `(stato, corpo)` invece di sollevare un'eccezione sugli
    errori: in un test un `409` è un risultato da verificare quanto un `200`, e
    doverlo catturare ogni volta renderebbe i test illeggibili.

    Il corpo è un dizionario se la risposta è JSON, altrimenti i byte grezzi —
    che è quello che serve quando si scarica un'immagine.
    """

    def call(method, path="", body=None, *, token=None, raw=None, headers=None, url=None):
        target = url or f"{base_url}{path}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)

        request = urllib.request.Request(target, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for nome, valore in (headers or {}).items():
            request.add_header(nome, valore)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as risposta:
                return risposta.status, _interpreta(risposta.headers, risposta.read())
        except urllib.error.HTTPError as errore:
            return errore.code, _interpreta(errore.headers, errore.read())

    return call


def _interpreta(headers, payload: bytes):
    if "json" in (headers.get("Content-Type") or ""):
        return json.loads(payload.decode() or "{}")
    return payload


# --- utenti ------------------------------------------------------------------

def _registra_ed_entra(api) -> str:
    """Un account nuovo di zecca, e il token per usarlo."""
    email = f"prova-{uuid.uuid4().hex[:10]}@example.com"

    stato, _ = api("POST", "/api/auth/register", {"email": email, "password": PASSWORD})
    assert stato == 201, "registrazione non riuscita"

    stato, corpo = api("POST", "/api/auth/login", {"email": email, "password": PASSWORD})
    assert stato == 200, "accesso non riuscito"
    return corpo["access_token"]


@pytest.fixture
def token(api) -> str:
    """Un utente nuovo per ogni test che crea dati."""
    return _registra_ed_entra(api)


@pytest.fixture
def altro_token(api) -> str:
    """Un secondo utente, per verificare che non veda i dati del primo."""
    return _registra_ed_entra(api)


# --- immagini di prova -------------------------------------------------------

@pytest.fixture(scope="session")
def foto() -> bytes:
    """Un JPEG con i dati di scatto dentro, come lo scriverebbe una reflex."""
    immagine = Image.new("RGB", (1800, 1200), (70, 110, 150))

    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "EOS 5D Mark IV"

    blocco = exif.get_ifd(0x8769)
    blocco[0xA434] = "EF 24-70mm f/2.8L"
    blocco[0x8827] = 1600
    blocco[0x829D] = (28, 10)
    blocco[0x829A] = (1, 250)
    blocco[0x920A] = (35, 1)
    blocco[0x9003] = "2026:05:02 09:15:00"

    buffer = io.BytesIO()
    immagine.save(buffer, "JPEG", quality=88, exif=exif)
    return buffer.getvalue()


@pytest.fixture(scope="session")
def foto_senza_exif() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 60, 60)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture(scope="session")
def file_finto() -> bytes:
    """Byte che non sono un'immagine, con l'estensione di una."""
    return b"questo non e un'immagine, nemmeno per sbaglio\n" * 20


# --- caricamento -------------------------------------------------------------

def carica_immagine(api, token, nome, contenuto, mime="image/jpeg") -> str:
    """I tre passi di un caricamento, come li fa il browser.

    Registrazione, invio diretto all'archivio con il link firmato, conferma.
    Restituisce l'identificativo dell'immagine.
    """
    stato, creato = api(
        "POST",
        "/api/assets",
        {"filename": nome, "mime": mime, "size_bytes": len(contenuto)},
        token=token,
    )
    assert stato == 201, f"registrazione rifiutata: {creato}"

    invio = creato["upload"]
    stato, _ = api("PUT", raw=contenuto, headers=invio["headers"], url=invio["url"])
    assert stato == 200, "l'archivio ha rifiutato il file"

    stato, corpo = api("POST", f"/api/assets/{creato['asset_id']}/complete", {}, token=token)
    assert stato == 202, f"conferma rifiutata: {corpo}"

    return creato["asset_id"]


def attendi_esito(api, token, asset_id, secondi=ATTESA_MASSIMA_SECONDI) -> dict:
    """Aspetta che un'immagine arrivi a destinazione, in un senso o nell'altro."""
    scadenza = time.time() + secondi
    stato_letto = None

    while time.time() < scadenza:
        _, corpo = api("GET", f"/api/assets/status?ids={asset_id}", token=token)
        voci = corpo.get("items", []) if isinstance(corpo, dict) else []
        if voci:
            stato_letto = voci[0]
            if stato_letto["status"] in ("DONE", "FAILED"):
                return stato_letto
        time.sleep(0.5)

    raise AssertionError(
        f"l'immagine {asset_id} non è arrivata a uno stato terminale "
        f"in {secondi} secondi: {stato_letto}"
    )


def attendi_avviso(api, token, asset_id, secondi=20) -> list[dict]:
    """Gli avvisi di un'immagine.

    Li scrive un altro servizio, dopo il worker: possono arrivare qualche
    istante più tardi dello stato finale, quindi si aspetta invece di leggere
    una volta sola.
    """
    scadenza = time.time() + secondi
    while time.time() < scadenza:
        _, pagina = api("GET", "/api/notifications", token=token)
        avvisi = [a for a in pagina["items"] if a["asset_id"] == asset_id]
        if avvisi:
            return avvisi
        time.sleep(0.5)
    return []


@pytest.fixture
def carica(api):
    return lambda token, nome, contenuto, mime="image/jpeg": carica_immagine(
        api, token, nome, contenuto, mime
    )


@pytest.fixture
def attendi(api):
    return lambda token, asset_id, secondi=ATTESA_MASSIMA_SECONDI: attendi_esito(
        api, token, asset_id, secondi
    )


@pytest.fixture
def immagine_pronta(api, token):
    """Una foto già elaborata, per i test che si limitano a leggere."""

    def esegui(nome, contenuto) -> str:
        asset_id = carica_immagine(api, token, nome, contenuto)
        stato = attendi_esito(api, token, asset_id)
        assert stato["status"] == "DONE", stato
        return asset_id

    return esegui


@pytest.fixture
def avvisi(api):
    """Gli avvisi di un'immagine, aspettando che il servizio li scriva."""
    return lambda token, asset_id, secondi=20: attendi_avviso(api, token, asset_id, secondi)


@pytest.fixture(scope="module")
def libreria(api, foto, foto_senza_exif, file_finto):
    """Una libreria con dentro un po' di tutto, preparata una volta per file.

    I test dei filtri si limitano a leggerla: rifarla per ognuno costerebbe
    un'elaborazione per test senza provare niente di più. Il contenuto è
    scelto perché ogni filtro abbia qualcosa da includere e qualcosa da
    escludere:

    - una foto Canon con EXIF completo, pronta
    - una foto senza EXIF, pronta
    - un file finto, fallito
    - una registrazione il cui file non è mai arrivato
    """
    token = _registra_ed_entra(api)

    canon = carica_immagine(api, token, "reflex-canon.jpg", foto)
    semplice = carica_immagine(api, token, "rosso-semplice.jpg", foto_senza_exif)
    rotto = carica_immagine(api, token, "rotto.jpg", file_finto)

    _, abbandonata = api(
        "POST", "/api/assets",
        {"filename": "mai-arrivata.jpg", "mime": "image/jpeg", "size_bytes": 1000},
        token=token,
    )

    for asset_id in (canon, semplice, rotto):
        attendi_esito(api, token, asset_id)

    return {
        "token": token,
        "canon": canon,
        "semplice": semplice,
        "rotto": rotto,
        "abbandonata": abbandonata["asset_id"],
    }


# --- il broker, dall'interno -------------------------------------------------

class Broker:
    """L'API di gestione di RabbitMQ: contare i messaggi, pubblicarne a mano.

    Serve ai test che devono mettere in coda qualcosa che il sistema da solo
    non produrrebbe mai — un messaggio illeggibile, un evento duplicato — per
    vedere come reagisce.
    """

    def __init__(self, amqp_url: str):
        parti = urllib.parse.urlparse(amqp_url)
        credenziali = f"{parti.username}:{parti.password}".encode()
        self._auth = "Basic " + base64.b64encode(credenziali).decode()
        self._base = f"http://{parti.hostname}:15672/api"

    def _chiama(self, method, path, body=None):
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
        )
        request.add_header("Authorization", self._auth)
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as risposta:
            return json.loads(risposta.read() or b"{}")

    def messaggi(self, coda: str) -> int:
        """Messaggi fermi in una coda, confermati o no."""
        info = self._chiama("GET", f"/queues/%2F/{urllib.parse.quote(coda, safe='')}")
        return info.get("messages", 0)

    def pubblica(self, coda: str, contenuto: str) -> None:
        """Un messaggio grezzo, dritto in una coda, saltando gli exchange."""
        esito = self._chiama(
            "POST",
            "/exchanges/%2F/amq.default/publish",
            {
                "properties": {"delivery_mode": 2, "content_type": "application/json"},
                "routing_key": coda,
                "payload": contenuto,
                "payload_encoding": "string",
            },
        )
        assert esito.get("routed"), f"il broker non ha instradato il messaggio verso {coda}"

    def attendi_messaggi(self, coda: str, almeno: int, secondi: int = 60) -> int:
        """Aspetta che una coda contenga almeno un certo numero di messaggi.

        Le statistiche dell'API di gestione si aggiornano ogni cinque secondi:
        una lettura sola rischierebbe di vedere il passato.
        """
        scadenza = time.time() + secondi
        visti = self.messaggi(coda)
        while visti < almeno and time.time() < scadenza:
            time.sleep(1)
            visti = self.messaggi(coda)
        return visti


@pytest.fixture(scope="session")
def broker() -> Broker:
    return Broker(os.environ["RABBITMQ_URL"])


# --- Docker, dall'interno ----------------------------------------------------

DOCKER_SOCKET = "/var/run/docker.sock"


class _ConnessioneUnix(http.client.HTTPConnection):
    """HTTP su un socket locale: è così che si parla con il motore di Docker."""

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(DOCKER_SOCKET)


class Docker:
    """Spegne, accende e osserva i container dello stack di prova.

    Parla direttamente con il motore di Docker attraverso il suo socket, con la
    sola libreria standard: nessun client da installare.
    """

    def __init__(self, progetto: str):
        self._progetto = progetto
        self.toccati: set[str] = set()

    def _nome(self, servizio: str) -> str:
        return f"{self._progetto}-{servizio}-1"

    def _chiama(self, method, path):
        connessione = _ConnessioneUnix("localhost", timeout=60)
        connessione.request(method, path)
        risposta = connessione.getresponse()
        corpo = risposta.read()
        if risposta.status >= 400 and risposta.status != 304:  # 304: già in quello stato
            raise AssertionError(f"Docker ha risposto {risposta.status} a {path}: {corpo[:200]}")
        return json.loads(corpo) if corpo else {}

    def spegni(self, servizio: str) -> None:
        self.toccati.add(servizio)
        self._chiama("POST", f"/containers/{self._nome(servizio)}/stop?t=10")

    def accendi(self, servizio: str) -> None:
        self._chiama("POST", f"/containers/{self._nome(servizio)}/start")

    def stato(self, servizio: str) -> dict:
        return self._chiama("GET", f"/containers/{self._nome(servizio)}/json")

    def riavvii(self, servizio: str) -> int:
        """Quante volte Docker l'ha dovuto rimettere in piedi dopo un crollo."""
        return self.stato(servizio)["RestartCount"]

    def in_esecuzione(self, servizio: str) -> bool:
        return self.stato(servizio)["State"]["Running"]

    def attendi_sano(self, servizio: str, secondi: int = 120) -> None:
        scadenza = time.time() + secondi
        while time.time() < scadenza:
            salute = self.stato(servizio)["State"].get("Health", {}).get("Status")
            if salute == "healthy":
                return
            time.sleep(1)
        raise AssertionError(f"{servizio} non è tornato in salute in {secondi} secondi")


@pytest.fixture
def docker():
    """Il telecomando di Docker, con una regola: alla fine riaccende tutto.

    Un test di guasto che fallisce a metà lascerebbe altrimenti lo stack di
    prova con un database spento, e ogni test successivo fallirebbe per un
    motivo che non ha niente a che fare con lui.
    """
    if not os.path.exists(DOCKER_SOCKET):
        pytest.skip("serve il socket di Docker: si lanciano con scripts/test.sh faults")

    telecomando = Docker(os.getenv("TEST_PROJECT", "media-platform-test"))
    yield telecomando

    for servizio in telecomando.toccati:
        telecomando.accendi(servizio)
    for servizio in telecomando.toccati:
        telecomando.attendi_sano(servizio)


def pronto(servizio: str) -> dict:
    """La sonda di prontezza di un consumatore, letta come la legge Kubernetes."""
    try:
        with urllib.request.urlopen(f"http://{servizio}:8000/health/ready", timeout=5) as r:
            return {"stato": r.status, **json.loads(r.read())}
    except urllib.error.HTTPError as errore:
        return {"stato": errore.code, **json.loads(errore.read())}


@pytest.fixture
def prontezza():
    return pronto
