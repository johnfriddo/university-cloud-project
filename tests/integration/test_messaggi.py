"""Messaggi che il sistema da solo non produrrebbe mai.

Un messaggio illeggibile, un evento consegnato due volte: li mettiamo in coda a
mano, attraverso l'API di gestione del broker, per vedere come reagiscono i
consumatori. Non spengono niente, quindi girano insieme agli altri test di
integrazione.
"""

import json

import pytest

pytestmark = pytest.mark.integration


def test_un_messaggio_illeggibile_finisce_nella_coda_dei_rifiutati(broker):
    prima = broker.messaggi("media.process.dlq")

    broker.pubblica("media.process", "questo non è JSON")

    # Nessun tentativo in più: nessuna ripetizione renderà comprensibile un
    # messaggio scritto male. Finisce subito da parte, dove si può esaminare.
    assert broker.attendi_messaggi("media.process.dlq", prima + 1) == prima + 1


def test_un_lavoro_senza_identificativo_finisce_nella_coda_dei_rifiutati(broker):
    prima = broker.messaggi("media.process.dlq")

    broker.pubblica("media.process", json.dumps({"storage_key": "x", "user_id": "y"}))

    assert broker.attendi_messaggi("media.process.dlq", prima + 1) == prima + 1


def test_lo_stesso_evento_consegnato_due_volte_produce_un_solo_avviso(
    api, token, immagine_pronta, avvisi, broker, foto, file_finto, carica, attendi
):
    asset_id = immagine_pronta("una-volta-sola.jpg", foto)
    assert len(avvisi(token, asset_id)) == 1

    # Il broker può consegnare due volte lo stesso messaggio: basta che un
    # consumatore muoia dopo aver lavorato e prima di confermare. Lo
    # simuliamo ripubblicando due volte l'evento.
    evento = json.dumps({
        "event": "asset.processed",
        "asset_id": asset_id,
        "user_id": "ignorato: il destinatario si legge dall'immagine",
        "filename": "una-volta-sola.jpg",
        "variants": ["thumb", "medium", "large"],
    })
    broker.pubblica("notifications", evento)
    broker.pubblica("notifications", evento)

    # Verificare che qualcosa *non* accada è delicato: quanto aspettare? Un
    # segnalino risolve. La coda è una sola e il consumatore la legge in
    # ordine: quando l'avviso del segnalino compare, i due duplicati pubblicati
    # prima sono per forza già stati trattati.
    segnalino = carica(token, "segnalino.jpg", file_finto)
    attendi(token, segnalino)
    assert avvisi(token, segnalino), "il segnalino non è mai stato elaborato"

    assert len(avvisi(token, asset_id)) == 1


def test_un_evento_per_un_immagine_inesistente_viene_scartato_senza_errori(
    broker, token, carica, attendi, avvisi, file_finto
):
    prima = broker.messaggi("notifications.dlq")

    # Un evento sopravvissuto all'immagine che lo riguarda: non c'è più
    # nessuno da avvisare. Non è un guasto da ritentare, né un messaggio
    # rotto da mettere da parte.
    broker.pubblica("notifications", json.dumps({
        "event": "asset.processed",
        "asset_id": "00000000-0000-0000-0000-000000000000",
        "user_id": "00000000-0000-0000-0000-000000000000",
        "filename": "sparita.jpg",
    }))

    segnalino = carica(token, "segnalino.jpg", file_finto)
    attendi(token, segnalino)
    assert avvisi(token, segnalino)

    assert broker.messaggi("notifications.dlq") == prima
