"""Il canale SNS, e il suo vincolo sull'oggetto del messaggio.

SNS accetta come oggetto di un'email solo testo ASCII, su una riga, sotto i
100 caratteri. I nostri oggetti hanno le virgolette «» e le lettere accentate:
spediti così, AWS li rifiuterebbe tutti, e ogni avviso finirebbe nella coda dei
rifiutati dopo tre tentativi.
"""

import pytest

from notifier.delivery import SnsChannel, sns_subject

pytestmark = pytest.mark.unit


class ClientFinto:
    def __init__(self):
        self.pubblicati = []

    def publish(self, **messaggio):
        self.pubblicati.append(messaggio)


def test_l_oggetto_perde_accenti_e_virgolette_ma_resta_leggibile():
    assert sns_subject("«città.jpg» è pronta") == '"citta.jpg" e pronta'


def test_l_oggetto_sta_su_una_riga_e_sotto_i_cento_caratteri():
    oggetto = sns_subject("«" + "x" * 150 + "»\nè pronta")

    assert "\n" not in oggetto
    assert len(oggetto) < 100


def test_il_messaggio_va_sull_argomento_con_il_destinatario_come_attributo():
    client = ClientFinto()
    canale = SnsChannel(topic_arn="arn:aws:sns:eu-central-1:1:avvisi", client=client)

    canale.send(recipient="marco@example.com", subject="«a.jpg» è pronta", body="Corpo.")

    pubblicato = client.pubblicati[0]
    assert pubblicato["TopicArn"] == "arn:aws:sns:eu-central-1:1:avvisi"
    assert pubblicato["Message"] == "Corpo."
    # Il corpo resta intatto: il vincolo riguarda solo l'oggetto.
    assert pubblicato["MessageAttributes"]["recipient"]["StringValue"] == "marco@example.com"
