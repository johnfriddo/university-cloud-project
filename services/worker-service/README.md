# worker-service

Consuma i job dalla coda e genera le varianti delle immagini con Pillow
(`thumb` 300 px, `medium` 800 px, `large` 1200 px con watermark), estraendo
contestualmente i metadati EXIF, le dimensioni e il checksum SHA-256.

È **stateless**: scala orizzontalmente in base alla lunghezza della coda,
indipendentemente dall'`api-service`. Non espone API: riceve lavoro solo dal
broker, e la porta HTTP serve unicamente alle probe di Kubernetes.

## Metadati tecnici

Un documento per asset sul documentale, con chiave l'id dell'asset:

```json
{
  "_id": "eda0fb2d-…", "asset_id": "eda0fb2d-…", "user_id": "54e5c354-…",
  "mime": "image/jpeg",
  "exif": { "make": "Canon", "model": "EOS R6", "lens": "RF 50mm F1.8 STM",
            "iso": 400, "exposure": "1/250", "aperture": "f/2.8",
            "focal_length": "50mm", "taken_at": "2026-07-15T18:32:10" },
  "dimensions": { "width": 1600, "height": 2400 },
  "checksum": "sha256:…"
}
```

I campi EXIF assenti **non compaiono**, non vengono scritti come `null`: una foto
scaricata dal web produce `"exif": {}` e va bene così. È il motivo per cui questi
dati non stanno nel relazionale.

I valori vengono normalizzati per essere leggibili e confrontabili: le frazioni
EXIF diventano `1/250` e `f/2.8`, la data diventa ISO 8601.

La scrittura è una sostituzione con upsert sulla chiave dell'asset: rielaborare
riscrive il documento, non lo duplica.

## Le varianti

| Variante | Lato lungo | Watermark |
|---|---|---|
| `thumb` | 300 px | no |
| `medium` | 800 px | no |
| `large` | 1200 px | sì |

Chiave sull'object storage: `{user_id}/{asset_id}/{kind}.jpg` nel bucket
`derived`. È deterministica: rielaborare lo stesso asset sovrascrive gli stessi
oggetti invece di lasciare orfani nel bucket.

Scelte della pipeline:

- **Proporzioni mai alterate**: si fissa il lato lungo e l'altro segue.
- **Nessun ingrandimento**: un'immagine più piccola del bersaglio resta com'è.
  Ingrandire non aggiunge dettaglio, aggiunge solo byte.
- **Orientamento EXIF applicato** in apertura: senza, metà delle foto scattate
  col telefono uscirebbero coricate.
- **Uscita sempre JPEG**: gli originali restano intatti nel loro bucket, e le
  derivate di una libreria fotografica non hanno bisogno di trasparenza. I PNG
  con canale alpha vengono appiattiti su bianco.
- **L'originale non viene mai modificato.**

## Come tratta un messaggio

| Situazione | Cosa fa | Perché |
|---|---|---|
| Elaborazione riuscita | `DONE`, messaggio confermato | il lavoro è finito |
| Messaggio illeggibile | rifiutato senza rimessa in coda → DLQ | nessuno saprà mai interpretarlo |
| File assente o illeggibile | `FAILED` con motivo, messaggio confermato | riprovare darebbe lo stesso esito; l'utente ha il pulsante Riprova |
| Database o storage irraggiungibili | messaggio **non** confermato, torna in coda | il lavoro è valido, il guasto è altrove |
| Asset già in stato terminale (`DONE`/`FAILED`) | messaggio confermato senza rielaborare | non c'è più niente da fare: consumo idempotente |
| Asset in `PROCESSING` | **rielaborato** | significa che qualcuno l'aveva preso ed è morto: saltarlo lo lascerebbe bloccato per sempre |

La conferma è sempre **manuale e successiva** al lavoro: un messaggio non
confermato resta del broker, che lo riconsegna a questo worker al riavvio o a
un'altra replica.

## Ritentativi

Solo i guasti infrastrutturali vengono ritentati, fino a `MAX_ATTEMPTS` volte;
poi il messaggio finisce in `media.process.dlq`. Un file illeggibile non consuma
tentativi: fallisce subito e definitivamente.

Il contatore viaggia nell'header `attempt` del messaggio, perché il broker non
tiene conto di quante volte un consumer gli ha restituito un messaggio. Per
questo il ritentativo è una **ripubblicazione** e non una rimessa in coda: un
messaggio rimesso in coda torna identico, senza memoria di aver fallito.

Se il guasto è proprio il database relazionale, alloscadere dei tentativi 
l'asset non può essere marcato `FAILED` e resta `PENDING`.
Il messaggio nella DLQ è in quel caso l'unica traccia dell'accaduto.

## Eventi pubblicati

Su `media.events` (fanout), a elaborazione conclusa:

| Evento | Quando | Campi |
|---|---|---|
| `asset.processed` | varianti generate e stato `DONE` | `asset_id`, `user_id`, `filename`, `status`, `variants` |
| `asset.failed` | asset marcato `FAILED` | `asset_id`, `user_id`, `filename`, `status`, `error` |

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

## Arresto

Su `SIGTERM` il worker smette di prendere nuovi messaggi e lascia finire quello
in mano. È il comportamento che Kubernetes si aspetta durante un aggiornamento o
uno scale down.

## Variabili d'ambiente

| Variabile | Obbligatoria | Default | Descrizione |
|---|---|---|---|
| `SERVICE_NAME` | no | `worker-service` | Nome riportato negli endpoint di health |
| `SERVICE_VERSION` | no | `0.1.0` | Versione riportata negli endpoint di health |
| `HEALTH_PORT` | no | `8000` | Porta del server di health |
| `DATABASE_URL` | sì | — | PostgreSQL: aggiornamento di stato job e varianti |
| `MONGO_URL` | sì | — | MongoDB: scrittura dei metadati EXIF |
| `MONGO_DATABASE` | no | `media` | Database documentale usato |
| `RABBITMQ_URL` | sì | — | Connessione al broker |
| `JOB_QUEUE` | no | `media.process` | Coda da cui consumare i job |
| `PREFETCH_COUNT` | no | `1` | Messaggi non confermati per worker |
| `MAX_ATTEMPTS` | no | `3` | Tentativi su guasto infrastrutturale prima della DLQ |
| `RETRY_BACKOFF_SECONDS` | no | `2` | Attesa prima di un ritentativo, moltiplicata per il numero di tentativo |
| `S3_ENDPOINT_INTERNAL` | no | — | Endpoint object storage; **vuoto su AWS**, dove S3 la libreria lo conosce già |
| `S3_ACCESS_KEY` | no* | — | Credenziale object storage; **assente su AWS**, dove il pod assume un ruolo (IRSA) |
| `S3_SECRET_KEY` | no* | — | Come sopra. *\*Le due chiavi vanno insieme: una sola delle due è rifiutata all'avvio* |
| `S3_REGION` | no | `us-east-1` | Regione |
| `S3_BUCKET_ORIGINALS` | no | `originals` | Bucket da cui legge l'originale |
| `S3_BUCKET_DERIVED` | no | `derived` | Bucket su cui scrive le varianti |
| `WATERMARK_TEXT` | no | `© media platform` | Testo sovrimpresso alla variante `large` |

## Endpoint

| Metodo | Rotta | Risposta |
|---|---|---|
| `GET` | `/health/live` | `200` finché il processo è vivo |
| `GET` | `/health/ready` | `200` se PostgreSQL, MongoDB, RabbitMQ e object storage rispondono, altrimenti `503` |

## Perché `PREFETCH_COUNT=1`

Con un carico a burst il broker deve poter distribuire i messaggi su tutte le
repliche disponibili. Un prefetch alto farebbe accumulare i job su un singolo
worker, lasciando le altre repliche inattive e vanificando l'autoscaling.

## Quante repliche

Sul cluster non lo decide nessuno a mano: lo decide la coda. KEDA la interroga
e chiede una replica ogni 5 messaggi in attesa, da 1 a 3. La configurazione sta
nel chart (`ScaledObject`), non qui: il servizio non sa di essere scalato, e
non deve saperlo.

Misurato su 300 immagini consegnate in blocco:

| | 1 worker | fino a 3 |
|---|---|---|
| Durata | 67,8 s | 37,7 s |
| Immagini al secondo | 4,4 | 8,0 |

Il guadagno è di 1,8 volte e non di 3 perché i nodi hanno due processori
ciascuno, condivisi con i database: si aggiungono worker, non processori. Il
dettaglio è in `docs/registro-cluster.md`.

## Sviluppo locale

```bash
cd deploy/compose
docker compose up --build worker-service
```
