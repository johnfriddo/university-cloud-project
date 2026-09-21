"""Lettura delle variabili d'ambiente senza cui un servizio non può partire.

Senza questo, una variabile mancante viene letta come stringa vuota e il
servizio parte lo stesso: supera la sonda di prontezza e si rompe più tardi,
alla prima richiesta che ne ha bisogno — con una traccia di stack invece di una spiegazione.

Conta tanto più quanto la configurazione viaggia lontano dal codice. In
compose arriva da un file .env che sta accanto al sorgente; in Kubernetes
da una ConfigMap e da un Secret, su AWS da Secrets Manager — e là una chiave
scritta male produce un servizio che sembra sano e non lo è.

Fallire all'avvio riduce tutto questo a una riga nel registro, prima che il
processo dichiari di essere pronto.
"""

import os


class MissingConfiguration(RuntimeError):
    """Una variabile che il servizio richiede non è stata fornita."""


def storage_credentials(access_name: str, secret_name: str) -> tuple[str, str]:
    """Le due chiavi dell'object storage, oppure due stringhe vuote.

    Vuote non è un errore: su AWS le credenziali non si passano affatto. Il pod
    assume un ruolo (IRSA) e la libreria trova da sé le chiavi temporanee, quindi
    quelle variabili semplicemente non esistono. Dichiararle con `required`
    impedirebbe al servizio di partire proprio là — ed è esattamente quello che
    sarebbe successo.

    Una sola delle due, invece, **è** un errore, e quasi sempre è un nome scritto
    male. Viene rifiutata qui invece di lasciarla fallire alla prima chiamata
    all'archivio, con un messaggio della libreria che non nomina nessuna variabile.
    """
    access = os.getenv(access_name, "").strip()
    secret = os.getenv(secret_name, "").strip()

    if bool(access) != bool(secret):
        missing, present = (
            (secret_name, access_name) if access else (access_name, secret_name)
        )
        raise MissingConfiguration(
            f"{present} è impostata ma {missing} no: servono entrambe, "
            f"oppure nessuna delle due per usare le credenziali dell'ambiente "
            f"(IRSA, profilo dell'istanza, ~/.aws)"
        )
    return access, secret


def required(name: str, *, min_length: int = 0) -> str:
    """Il valore di una variabile obbligatoria, o un errore chiaro.

    `min_length` serve ai valori che sono chiavi crittografiche e non semplici
    indirizzi. Una chiave corta non impedisce al servizio di funzionare: la rende
    solo più facile da indovinare. Una debolezza così non si annuncia mai, quindi
    va rifiutata all'avvio, altrimenti arriva indisturbata in produzione.
    """
    value = os.getenv(name, "").strip()
    if not value:
        raise MissingConfiguration(
            f"la variabile d'ambiente {name} è obbligatoria e non è impostata"
        )
    if min_length and len(value) < min_length:
        raise MissingConfiguration(
            f"la variabile d'ambiente {name} è troppo corta: "
            f"servono almeno {min_length} caratteri, ne ha {len(value)}"
        )
    return value
