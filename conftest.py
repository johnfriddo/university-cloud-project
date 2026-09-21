"""Rende importabile il codice dei servizi dai test.

Ogni servizio è un'applicazione a sé, non un pacchetto installato: `app`,
`worker` e `notifier` vivono dentro le rispettive cartelle. Qui quelle cartelle
vengono messe davanti al percorso di ricerca dei moduli, così i test importano
il codice **montato dal disco** invece della copia installata nell'immagine.

È la ragione per cui si può modificare un test e rilanciarlo subito, senza
ricostruire niente.
"""

import sys
from pathlib import Path

REPO = Path(__file__).parent

SERVICES = (
    "common",
    "api-service",
    "worker-service",
    "notification-service",
)

for service in SERVICES:
    sys.path.insert(0, str(REPO / "services" / service))
