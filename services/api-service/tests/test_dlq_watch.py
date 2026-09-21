"""Dove risponde l'API di gestione del broker, in locale e su Amazon MQ."""

import pytest

from app.dlq_watch import management_base

pytestmark = pytest.mark.unit


def test_in_locale_la_gestione_e_in_chiaro_sulla_porta_15672():
    assert management_base("amqp://media:x@rabbitmq:5672/") == "http://rabbitmq:15672/api"


def test_su_amazon_mq_la_gestione_e_in_https_sullo_stesso_host():
    indirizzo = "amqps://media:x@b-1234.mq.eu-central-1.on.aws:5671"

    assert management_base(indirizzo) == "https://b-1234.mq.eu-central-1.on.aws/api"
