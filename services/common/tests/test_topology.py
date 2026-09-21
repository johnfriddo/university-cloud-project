"""La topologia che i servizi dichiarano al broker.

Non serve un broker vero: `declare_topology` riceve un canale e gli dà ordini,
quindi basta un canale finto che prende nota di quello che gli viene chiesto.
È il modo per provare una regola — «la coda dei lavori manda i rifiutati alla
propria coda di scarto» — senza accendere niente.
"""

import pytest

from media_common.queue import Topology, declare_topology

pytestmark = pytest.mark.unit


class CanaleFinto:
    """Prende nota degli ordini ricevuti, invece di eseguirli."""

    def __init__(self):
        self.exchanges: dict[str, str] = {}
        self.queues: dict[str, dict] = {}
        self.bindings: list[tuple[str, str, str | None]] = []

    def exchange_declare(self, exchange, exchange_type=None, durable=None, **_):
        self.exchanges[exchange] = exchange_type

    def queue_declare(self, queue, durable=None, arguments=None, **_):
        self.queues[queue] = arguments or {}

    def queue_bind(self, queue, exchange, routing_key=None, **_):
        self.bindings.append((queue, exchange, routing_key))


@pytest.fixture
def canale():
    canale = CanaleFinto()
    declare_topology(canale, Topology())
    return canale


def test_i_due_smistamenti_hanno_il_tipo_giusto(canale):
    # Diretto per i lavori: un'immagine va elaborata una volta sola, da uno
    # qualsiasi dei worker. Fanout per gli eventi: chi pubblica non sa chi
    # ascolta, e ogni ascoltatore riceve la sua copia.
    assert canale.exchanges["media.jobs"] == "direct"
    assert canale.exchanges["media.events"] == "fanout"


def test_ogni_coda_di_lavoro_ha_la_sua_coda_dei_rifiutati(canale):
    assert canale.queues["media.process"]["x-dead-letter-exchange"] == "media.jobs.dlx"
    assert canale.queues["notifications"]["x-dead-letter-exchange"] == "media.events.dlx"


def test_i_rifiutati_dei_lavori_e_degli_eventi_non_finiscono_insieme(canale):
    # Due tipi di guasto diversi non devono finire nello stesso mucchio.
    assert ("media.process.dlq", "media.jobs.dlx", None) in canale.bindings
    assert ("notifications.dlq", "media.events.dlx", None) in canale.bindings


def test_la_coda_dei_lavori_ascolta_la_chiave_giusta(canale):
    assert ("media.process", "media.jobs", "process") in canale.bindings


def test_le_code_di_scarto_non_hanno_a_loro_volta_uno_scarto(canale):
    # Altrimenti un messaggio rifiutato rimbalzerebbe all'infinito fra code.
    assert canale.queues["media.process.dlq"] == {}
    assert canale.queues["notifications.dlq"] == {}
