"""Guasti veri: container spenti mentre il sistema lavora.

Sono le proprietà che l'architettura promette e che nessuno può controllare a
occhio: un guasto non deve perdere né duplicare il lavoro, e un servizio non
deve crollare perché un altro è in ritardo. In Kubernetes succederà a ogni
installazione e a ogni riavvio di un nodo.

Spengono container dello stack di prova, quindi durano minuti e girano a parte:
    scripts/test.sh faults
"""

import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

CONSUMATORI = ("worker-service", "notification-service")


def test_con_il_worker_spento_i_lavori_aspettano_e_nessuno_si_perde(
    docker, token, carica, attendi, api, foto
):
    docker.spegni("worker-service")

    ids = [carica(token, f"in-attesa-{n}.jpg", foto) for n in range(3)]

    # Nessuno li elabora, ma l'API li ha accettati: stanno nella coda del
    # broker, che li conserva finché qualcuno non torna a prenderli.
    time.sleep(3)
    _, corpo = api("GET", f"/api/assets/status?ids={','.join(ids)}", token=token)
    assert {voce["status"] for voce in corpo["items"]} == {"PENDING"}

    docker.accendi("worker-service")

    for asset_id in ids:
        assert attendi(token, asset_id)["status"] == "DONE"


def test_senza_broker_i_consumatori_aspettano_invece_di_crollare(docker, prontezza):
    riavvii_prima = {servizio: docker.riavvii(servizio) for servizio in CONSUMATORI}

    docker.spegni("rabbitmq")
    time.sleep(10)

    for servizio in CONSUMATORI:
        # Vivo ma non pronto: il processo è sano, solo che non può lavorare.
        # È esattamente la distinzione che Kubernetes userà per non riavviarlo.
        assert docker.in_esecuzione(servizio), f"{servizio} si è spento"
        stato = prontezza(servizio)
        assert stato["stato"] == 503
        assert stato["checks"]["consumer"]["ok"] is False

    docker.accendi("rabbitmq")
    docker.attendi_sano("rabbitmq")

    # L'attesa fra i tentativi raddoppia fino a un massimo di 30 secondi.
    scadenza = time.time() + 60
    while time.time() < scadenza:
        if all(prontezza(servizio)["stato"] == 200 for servizio in CONSUMATORI):
            break
        time.sleep(2)

    for servizio in CONSUMATORI:
        assert prontezza(servizio)["stato"] == 200, f"{servizio} non si è ripreso"
        # Il punto di tutto: nessun crollo, nessun riavvio.
        assert docker.riavvii(servizio) == riavvii_prima[servizio]


def test_il_sistema_lavora_di_nuovo_dopo_il_ritorno_del_broker(
    docker, token, carica, attendi, prontezza, foto
):
    docker.spegni("rabbitmq")
    time.sleep(5)
    docker.accendi("rabbitmq")
    docker.attendi_sano("rabbitmq")

    scadenza = time.time() + 60
    while time.time() < scadenza and prontezza("worker-service")["stato"] != 200:
        time.sleep(2)

    asset_id = carica(token, "dopo-il-guasto.jpg", foto)
    assert attendi(token, asset_id)["status"] == "DONE"


def test_con_il_database_spento_il_lavoro_finisce_nella_coda_dei_rifiutati(
    docker, broker, token, carica, foto
):
    # Il lavoro deve arrivare in coda mentre il database c'è, e venire preso
    # quando il database non c'è più: per questo il worker parte dopo.
    docker.spegni("worker-service")
    carica(token, "senza-database.jpg", foto)

    prima = broker.messaggi("media.process.dlq")
    docker.spegni("postgres")
    docker.accendi("worker-service")

    # Tre tentativi con attesa crescente, poi la resa: il messaggio va da parte
    # invece di rimbalzare per sempre fra il worker e la coda.
    assert broker.attendi_messaggi("media.process.dlq", prima + 1, secondi=120) == prima + 1
