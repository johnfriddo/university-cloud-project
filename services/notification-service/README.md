# notification-service

Consuma gli eventi di fine elaborazione dall'exchange fanout `media.events`,
registra una notifica per il proprietario dell'immagine e la consegna. Non
espone API: la porta HTTP serve solo alle probe.

Il frontend non dipende da questo servizio. Chi guarda la pagina segue
l'avanzamento in polling; questo servizio serve a chi **non** la sta guardando.

## Cosa ascolta

Il worker pubblica due eventi. L'exchange è *fanout*: chi pubblica non sa chi
ascolta, e altri consumatori si possono aggiungere senza toccarlo.

| Evento | Quando | Campi |
|---|---|---|
| `asset.processed` | varianti generate | `asset_id`, `user_id`, `filename`, `variants` |
| `asset.failed` | elaborazione fallita | `asset_id`, `user_id`, `filename`, `error` |

Un evento che non è nessuno dei due viene confermato e ignorato: su un fanout è
normale ricevere roba destinata a qualcun altro, e trattarla come un errore
riempirebbe la coda dei rifiutati di messaggi legittimi.

## La consegna

In locale il canale è **il log**: non c'è un server di posta, e aggiungerne uno
non è nello stack concordato. Il messaggio viene scritto per intero, così una
persona può leggerlo. Su AWS lo stesso messaggio esce da SNS e diventa una email.

Testo e canale sono separati (`delivery.py`): la Fase 7 sostituisce solo
l'implementazione di `Channel`, e composizione, registrazione e tentativi
restano dove sono.

## Affidabilità

Stessa disciplina del worker-service, per gli stessi motivi.

| Situazione | Comportamento |
|---|---|
| Messaggio illeggibile o senza `asset_id` | rifiutato subito, va in `notifications.dlq` |
| Guasto infrastrutturale (database irraggiungibile) | ripubblicato con il contatore alzato, fino a `MAX_ATTEMPTS` |
| Tentativi esauriti | rifiutato, va in `notifications.dlq` |
| Immagine o utente non più esistenti | confermato e scartato: non c'è più nessuno da avvisare |
| Evento già notificato | confermato, nessuna seconda notifica |

Il destinatario è letto dall'immagine, non dallo `user_id` che viaggia nel
messaggio: il proprietario è un fatto del database, e un evento malformato o
vecchio non deve poter indirizzare una notifica a qualcun altro. La stessa
interrogazione stabilisce se l'immagine esiste ancora.

Il contatore dei tentativi viaggia in un'intestazione del messaggio, perché il
broker non conta quante volte un consumatore gli ha restituito qualcosa: chi
ritenta se lo deve portare dietro. Per questo il ritentativo è un messaggio
*nuovo*, pubblicato direttamente sulla coda e non sull'exchange — passare
dall'exchange ne consegnerebbe una copia a ogni altro consumatore legato, e il
problema è soltanto nostro.

L'idempotenza sta nel vincolo di unicità `(asset_id, event)`: un messaggio
riconsegnato trova la riga già scritta e non avvisa nessuno una seconda volta.
Lo stesso asset può però produrre una notifica di errore e poi una di successo,
che è esattamente ciò che accade a un'immagine fallita e poi rielaborata.

`sent_at` viene scritto **dopo** la consegna, non prima: una riga con `sent_at`
vuoto è la traccia di qualcosa che era dovuto e non è mai partito, e al
ritentativo la consegna viene riprovata.

## Se il broker non c'è

Il servizio non esce mai perché il broker manca: aspetta e si riconnette. Vale
sia all'avvio, quando il broker non è ancora pronto, sia mentre lavora, se il
broker si riavvia.

L'attesa raddoppia a ogni tentativo — 1, 2, 4, 8 secondi — fino a un massimo di
30. Nel frattempo `/health/live` risponde `200` e `/health/ready` `503`: il
processo è sano, ma non sta lavorando.

Uscire, invece, scaricherebbe il problema su chi riavvia il processo. In
Kubernetes non esiste `depends_on`, tutti i pod partono insieme, e a uscite
ripetute Kubernetes risponde con pause sempre più lunghe, fino a cinque minuti:
un broker in ritardo di dieci secondi terrebbe il servizio fermo molto più a
lungo.

Unica eccezione: se il broker **risponde ma rifiuta le credenziali**, il
servizio si ferma subito. Una password sbagliata non si aggiusta aspettando.

Un messaggio interrotto dalla connessione persa non era stato confermato, quindi
il broker lo riconsegna: nessun lavoro perso, e l'idempotenza evita i doppioni.

## Variabili d'ambiente

| Variabile | Obbligatoria | Default | Descrizione |
|---|---|---|---|
| `SERVICE_NAME` | no | `notification-service` | Nome riportato negli endpoint di health |
| `SERVICE_VERSION` | no | `0.1.0` | Versione riportata negli endpoint di health |
| `HEALTH_PORT` | no | `8000` | Porta del server di health |
| `DATABASE_URL` | sì | — | PostgreSQL: destinatario dell'evento e registrazione della notifica |
| `RABBITMQ_URL` | sì | — | Connessione al broker |
| `EVENTS_EXCHANGE` | no | `media.events` | Exchange fanout degli eventi |
| `NOTIFICATIONS_QUEUE` | no | `notifications` | Coda legata all'exchange |
| `EVENTS_DLX_EXCHANGE` | no | `media.events.dlx` | Exchange dei messaggi rifiutati |
| `NOTIFICATIONS_DLQ` | no | `notifications.dlq` | Coda dei messaggi rifiutati |
| `MAX_ATTEMPTS` | no | `3` | Tentativi prima della resa, come nel worker |
| `RETRY_BACKOFF_SECONDS` | no | `2` | Attesa lineare fra un tentativo e l'altro |

## Endpoint

| Metodo | Rotta | Risposta |
|---|---|---|
| `GET` | `/health/live` | `200` finché il processo è vivo |
| `GET` | `/health/ready` | `200` se PostgreSQL e RabbitMQ rispondono **e** il consumo è attivo, altrimenti `503` |

La terza condizione non è pignoleria: un processo che ha smesso di consumare non
serve a nessuno, per quanto sano sembri.

## La tabella

`notifications` nasce con la migrazione `003_notifications.sql`, che sta in
`api-service/migrations/`: lo schema ha un solo proprietario e una sola storia
ordinata, indipendentemente da quanti servizi ne toccano le tabelle. Questo
servizio ci scrive, l'api-service potrà solo leggerla.

Il messaggio è salvato per intero, testo compreso. Ricostruirlo più tardi
dall'immagine darebbe parole diverse da quelle consegnate, e la registrazione di
una notifica deve dire ciò che è stato effettivamente detto.

## Sviluppo locale

```bash
cd deploy/compose
docker compose up --build notification-service
docker compose logs -f notification-service
```

Attenzione a un vincolo del broker: gli argomenti di una coda non si cambiano
dopo che la coda esiste. Aggiungere la dead letter queue a `notifications` ha
richiesto di cancellarla una volta (`rabbitmqctl delete_queue notifications`)
dopo averla svuotata. In un ambiente nuovo — il cluster, AWS — nasce già
corretta.
