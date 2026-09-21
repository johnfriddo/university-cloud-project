"""L'adattatore DynamoDB, provato senza AWS.

Al posto della tabella vera c'è una controfigura che registra le richieste e
restituisce pagine preparate. Basta per le tre cose che distinguono DynamoDB
da MongoDB e che è facile sbagliare: i numeri, le maiuscole, le pagine.
"""

from decimal import Decimal

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder

from media_common.metadata_dynamo import (
    DynamoMetadataStore,
    from_dynamo,
    search_attributes,
    search_condition,
    to_dynamo,
)

pytestmark = pytest.mark.unit


class TabellaFinta:
    """Registra quel che le si chiede; `pagine` sono le risposte a query()."""

    def __init__(self, pagine=(), oggetto=None):
        self.pagine = list(pagine)
        self.oggetto = oggetto
        self.query_ricevute = []
        self.scritti = []

    def put_item(self, Item):
        self.scritti.append(Item)

    def get_item(self, Key):
        return {"Item": self.oggetto} if self.oggetto is not None else {}

    def query(self, **richiesta):
        self.query_ricevute.append(dict(richiesta))
        return self.pagine.pop(0)


def valori_della_condizione(condizione) -> set:
    costruita = ConditionExpressionBuilder().build_expression(condizione)
    return set(costruita.attribute_value_placeholders.values())


# --- i numeri ----------------------------------------------------------------


def test_i_decimali_arrivano_a_dynamodb_come_decimal_senza_arrotondamenti():
    # f/2.8 passando da un float binario diventerebbe 2.79999…: la conversione
    # passa dal testo apposta.
    assert to_dynamo({"aperture_value": 2.8}) == {"aperture_value": Decimal("2.8")}


def test_i_numeri_tornano_numeri_e_non_testo():
    documento = from_dynamo({"iso": Decimal("400"), "ratio": Decimal("1.5")})

    assert documento == {"iso": 400, "ratio": 1.5}
    assert isinstance(documento["iso"], int)


def test_salvare_e_rileggere_restituisce_lo_stesso_documento():
    tabella = TabellaFinta()
    archivio = DynamoMetadataStore(table_name="t", table=tabella)

    archivio.save_asset_metadata(
        asset_id="a1",
        user_id="u1",
        mime="image/jpeg",
        exif={"make": "Canon", "iso": 400, "focal_length": "50mm"},
        dimensions={"width": 1600, "height": 2400},
        checksum="sha256:abc",
    )
    tabella.oggetto = tabella.scritti[0]
    riletto = archivio.get("a1")

    assert riletto["exif"] == {"make": "Canon", "iso": 400, "focal_length": "50mm"}
    assert riletto["dimensions"] == {"width": 1600, "height": 2400}


# --- le maiuscole --------------------------------------------------------------


def test_i_campi_di_ricerca_sono_salvati_in_minuscolo():
    copie = search_attributes({"make": "Canon", "model": "EOS R6", "lens": "RF 85mm F2"})

    assert copie == {
        "search_make": "canon",
        "search_model": "eos r6",
        "search_lens": "rf 85mm f2",
    }


def test_un_campo_assente_non_diventa_un_campo_di_ricerca():
    # Come nel documento: un dato che la fotocamera non ha scritto non esiste,
    # non vale "vuoto".
    assert search_attributes({}) == {}


def test_anche_il_testo_cercato_diventa_minuscolo():
    condizione = search_condition(
        camera="  CANON ", lens=None, focal_length=None, iso_min=None, iso_max=None
    )

    assert valori_della_condizione(condizione) == {"canon"}


def test_nessun_filtro_nessuna_condizione():
    assert (
        search_condition(camera=None, lens=None, focal_length=None, iso_min=None, iso_max=None)
        is None
    )


# --- le pagine -----------------------------------------------------------------


def test_la_ricerca_legge_solo_i_documenti_dell_utente():
    tabella = TabellaFinta(pagine=[{"Items": []}])
    DynamoMetadataStore(table_name="t", table=tabella).search_asset_ids(user_id="u1", limit=10)

    richiesta = tabella.query_ricevute[0]
    assert richiesta["IndexName"] == "by_user"
    costruita = ConditionExpressionBuilder().build_expression(
        richiesta["KeyConditionExpression"], is_key_condition=True
    )
    assert set(costruita.attribute_value_placeholders.values()) == {"u1"}


def test_la_ricerca_segue_le_pagine_anche_quando_una_e_vuota():
    # In DynamoDB una pagina è "ciò che è stato letto", non "ciò che combacia":
    # una pagina senza risultati non vuol dire che non ce ne siano altri.
    tabella = TabellaFinta(
        pagine=[
            {"Items": [], "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"asset_id": "a1"}], "LastEvaluatedKey": {"k": 2}},
            {"Items": [{"asset_id": "a2"}]},
        ]
    )

    trovati = DynamoMetadataStore(table_name="t", table=tabella).search_asset_ids(
        user_id="u1", lens="85mm", limit=10
    )

    assert trovati == ["a1", "a2"]
    assert tabella.query_ricevute[1]["ExclusiveStartKey"] == {"k": 1}


def test_la_ricerca_si_ferma_quando_ha_raggiunto_il_limite():
    tabella = TabellaFinta(
        pagine=[
            {"Items": [{"asset_id": "a1"}, {"asset_id": "a2"}], "LastEvaluatedKey": {"k": 1}},
            {"Items": [{"asset_id": "a3"}]},
        ]
    )

    trovati = DynamoMetadataStore(table_name="t", table=tabella).search_asset_ids(
        user_id="u1", limit=2
    )

    assert trovati == ["a1", "a2"]
    # La seconda pagina non è mai stata chiesta: ogni lettura si paga.
    assert len(tabella.query_ricevute) == 1
