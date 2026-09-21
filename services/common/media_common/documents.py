"""Con quale documentale parla un servizio: MongoDB in locale, DynamoDB su AWS.

La scelta è una variabile d'ambiente sola, METADATA_BACKEND, letta una volta
all'avvio. Da lì in poi sono le stesse cinque chiamate su entrambi gli adapter.

Ogni adapter viene importato solo se scelto. pymongo è una dipendenza
facoltativa di questo pacchetto, e un servizio configurato per DynamoDB non
deve fallire all'importazione perché manca un driver che non userà mai.
"""

import os
from dataclasses import dataclass

from media_common.env import MissingConfiguration, required

BACKENDS = ("mongo", "dynamodb")


@dataclass(frozen=True)
class DocumentStoreSettings:
    backend: str
    mongo_url: str = ""
    mongo_database: str = "media"
    table: str = ""

    @classmethod
    def from_env(cls) -> "DocumentStoreSettings":
        """Legge e verifica le variabili del documentale scelto, e soltanto quelle.

        MONGO_URL è obbligatoria con MongoDB e non significa niente con DynamoDB, e
        per METADATA_TABLE vale l'opposto. Pretenderle entrambe impedirebbe al
        servizio di partire su AWS — lo stesso errore che fecero un tempo le
        credenziali dell'archivio; non pretenderne nessuna lascerebbe passare un refuso.
        """
        backend = os.getenv("METADATA_BACKEND", "mongo").strip().lower()
        if backend not in BACKENDS:
            raise MissingConfiguration(
                f"METADATA_BACKEND vale «{backend}»: i valori ammessi sono {', '.join(BACKENDS)}"
            )
        if backend == "dynamodb":
            return cls(backend=backend, table=required("METADATA_TABLE"))
        return cls(
            backend=backend,
            mongo_url=required("MONGO_URL"),
            mongo_database=os.getenv("MONGO_DATABASE", "media"),
        )

    def build(self):
        if self.backend == "dynamodb":
            from media_common.metadata_dynamo import DynamoMetadataStore

            return DynamoMetadataStore(table_name=self.table)

        from media_common.metadata import MetadataStore

        return MetadataStore(url=self.mongo_url, database=self.mongo_database)
