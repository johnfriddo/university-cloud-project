# api-service

API REST del sistema (Python + Flask). È l'unico servizio esposto al browser
tramite l'Ingress: tutte le rotte vivono sotto `/api`.

Registra gli asset su PostgreSQL, firma i link temporanei per l'upload diretto
su object storage e pubblica i job sulla coda. Non elabora mai immagini: risponde
subito con `202` e l'id del job.

## Stato

Fasi 1-4: autenticazione, upload con link firmato, libreria filtrabile,
polling sullo stato, dettaglio con metadati tecnici e lettura degli avvisi.

## Variabili d'ambiente

Le variabili marcate come obbligatorie sono verificate **all'avvio**: se ne manca
una il processo esce subito con il nome della variabile, invece di partire,
superare la readiness e rompersi alla prima richiesta che ne ha bisogno.

| Variabile | Obbligatoria | Default | Descrizione |
|---|---|---|---|
| `SERVICE_NAME` | no | `api-service` | Nome riportato negli endpoint di health |
| `SERVICE_VERSION` | no | `0.1.0` | Versione riportata negli endpoint di health |
| `DATABASE_URL` | sì | — | Connessione PostgreSQL, `postgresql://user:pass@host:5432/db` |
| `MONGO_URL` | sì | — | Connessione al documentale, per leggere i metadati tecnici |
| `MONGO_DATABASE` | no | `media` | Database documentale usato |
| `METADATA_MATCH_LIMIT` | no | `2000` | Tetto agli identificativi restituiti da una ricerca tecnica |
| `RABBITMQ_URL` | sì | — | Connessione al broker, `amqp://user:pass@host:5672/` |
| `S3_ENDPOINT_INTERNAL` | no | — | Endpoint usato dal servizio (`http://minio:9000`); **vuoto su AWS**, dove S3 la libreria lo conosce già |
| `S3_ENDPOINT_PUBLIC` | sì | — | Endpoint su cui si firmano i presigned URL: deve essere raggiungibile **dal browser** |
| `S3_ACCESS_KEY` | no* | — | Credenziale object storage; **assente su AWS**, dove il pod assume un ruolo (IRSA) |
| `S3_SECRET_KEY` | no* | — | Come sopra. *\*Le due chiavi vanno insieme: una sola delle due è rifiutata all'avvio* |
| `S3_REGION` | no | `us-east-1` | Regione, indifferente per MinIO |
| `S3_BUCKET_ORIGINALS` | no | `originals` | Bucket dei file caricati |
| `S3_BUCKET_DERIVED` | no | `derived` | Bucket delle varianti generate |
| `S3_ADDRESSING_STYLE` | no | `path` | `path` per MinIO, `virtual` su AWS |
| `UPLOAD_URL_TTL_SECONDS` | no | `900` | Durata del link firmato per l'upload |
| `DOWNLOAD_URL_TTL_SECONDS` | no | `900` | Durata dei link firmati per il download |
| `STALE_JOB_SECONDS` | no | `600` | Dopo quanto un job non concluso è considerato bloccato e rielaborabile |
| `MAX_UPLOAD_BYTES` | no | `26214400` | Dimensione massima per file (25 MB) |
| `ALLOWED_MIME_TYPES` | no | `image/jpeg,image/png,image/webp` | Formati accettati |
| `JWT_SECRET` | sì | — | Chiave di firma dei token di sessione, **almeno 32 caratteri** |
| `JWT_TTL_SECONDS` | no | `3600` | Durata del token restituito dal login |
| `DB_POOL_MIN_SIZE` | no | `1` | Connessioni PostgreSQL tenute sempre aperte |
| `DB_POOL_MAX_SIZE` | no | `5` | Tetto di connessioni per singola replica |

## Endpoint

| Metodo | Rotta | Auth | Risposta |
|---|---|---|---|
| `GET` | `/api/health/live` | no | `200` finché il processo è vivo |
| `GET` | `/api/health/ready` | no | `200` se PostgreSQL, RabbitMQ e object storage rispondono, altrimenti `503` con il dettaglio per dipendenza |
| `POST` | `/api/auth/register` | no | `201` con l'utente creato; `400` dati non validi; `409` email già registrata |
| `POST` | `/api/auth/login` | no | `200` con `access_token`; `401` credenziali errate |
| `GET` | `/api/auth/me` | sì | `200` con l'utente del token; `401` token mancante, scaduto o non valido |
| `POST` | `/api/assets` | sì | `201` con `asset_id` e link firmato per l'upload; `415` formato non ammesso; `413` file oltre il limite; `400` dati non validi |
| `POST` | `/api/assets/{id}/complete` | sì | `202` job accodato; `409` già inviato o file non caricato; `413` file reale oltre il limite; `404` inesistente; `503` broker non disponibile |
| `POST` | `/api/assets/{id}/retry` | sì | `202` job riaccodato; `409` immagine già completata, caricamento mai concluso o elaborazione ancora in corso; `404` inesistente |
| `GET` | `/api/assets` | sì | `200` con una pagina della libreria |
| `GET` | `/api/assets/status?ids=…` | sì | `200` con i soli stati degli asset indicati; `400` id non validi o troppi |
| `GET` | `/api/assets/{id}` | sì | `200` con dettaglio, varianti, link all'originale e scheda dei metadati tecnici; ogni variante porta anche un link che **salva** il file; `404` inesistente |
| `DELETE` | `/api/assets/{id}` | sì | `204` eliminata; `409` è in elaborazione proprio adesso; `404` inesistente o di un altro |
| `GET` | `/api/notifications` | sì | `200` con una pagina degli avvisi; con `since=<ISO 8601>` solo quelli successivi, per il conteggio dei nuovi; `400` data non valida |

### Polling

Il frontend interroga `/api/assets/status` ogni 2 secondi finché tutti gli asset
visibili non sono in stato terminale. L'endpoint restituisce **solo** id, stato,
messaggio d'errore e data di aggiornamento: nessun link firmato.

Interrogare la libreria completa sarebbe sbagliato due volte. Rifirmerebbe ogni
link di download a ogni giro, e siccome una firma nuova produce un URL diverso,
il browser riscaricherebbe **tutte** le miniature della griglia ogni due secondi.

### Filtri della libreria

| Parametro | Esempio | Effetto |
|---|---|---|
| `page`, `page_size` | `?page=2&page_size=20` | paginazione (`page_size` max 100) |
| `status` | `?status=DONE` | uno fra `PENDING`, `PROCESSING`, `DONE`, `FAILED` |
| `filename` | `?filename=mare` | contiene il testo, `%` e `_` cercati alla lettera |
| `from`, `to` | `?from=2026-09-01&to=2026-09-06` | intervallo di date, estremi inclusi |
| `camera` | `?camera=Canon` | marca o modello contengono il testo |
| `lens` | `?lens=85mm` | obiettivo contiene il testo |
| `focal_length` | `?focal_length=50` | la focale comincia per: `50` trova `50mm`, non `150mm` |
| `iso_min`, `iso_max` | `?iso_min=800` | intervallo di sensibilità |

I primi filtri interrogano il relazionale, gli ultimi quattro il documentale. Si
combinano liberamente.

La libreria elenca solo gli asset effettivamente consegnati
(`submitted_at IS NOT NULL`): una registrazione il cui file non è mai arrivato
non è un'immagine. Vedi la pulizia più sotto.

### Come vengono unite due basi di dati

Il documentale risponde a *"quali asset hanno questi metadati"* e restituisce
solo gli identificativi; il relazionale usa quella lista come ulteriore
condizione e si occupa di ordinamento e paginazione, che sono roba sua.

La lista è limitata a `METADATA_MATCH_LIMIT` identificativi. Se il tetto viene
raggiunto la risposta contiene `"truncated": true`: meglio dichiarare una
risposta parziale che spacciarla per completa.

### Due link per ogni variante

Il dettaglio restituisce, per ogni variante, `url` e `download_url`. Sono lo
stesso oggetto firmato due volte con istruzioni diverse: il primo lo **mostra**
— serve all'anteprima — il secondo lo **salva** con un nome leggibile,
`tramonto-medium.jpg`, ricavato dal nome originale.

L'istruzione viaggia dentro la firma, quindi nessuno può cambiarla, e il nome è
scritto in due forme come prevede lo standard: una semplice per qualunque
browser e una codificata che conserva accenti e spazi.

La libreria non chiede i link di salvataggio: lì servono solo a riempire una
griglia di miniature, e firmare un secondo indirizzo per ognuna delle sessanta
varianti di una pagina sarebbe lavoro che nessuno usa.

### Scheda dei metadati nel dettaglio

```json
"metadata": {
  "exif": { "make": "Canon", "model": "EOS R6", "iso": 400, "exposure": "1/250", … },
  "dimensions": { "width": 1600, "height": 2400 },
  "checksum": "sha256:…"
}
```

Vale `null` se l'asset non è ancora stato elaborato, o se il documentale non
risponde: il dettaglio resta comunque disponibile con immagine, varianti e stato.

Le rotte autenticate vogliono l'header `Authorization: Bearer <token>`.

Tutti gli errori hanno la stessa forma, così il frontend non deve distinguere i
casi: `{"error": {"code": "...", "message": "..."}}`. Il `code` serve al codice,
il `message` è in italiano ed è quello che legge l'utente.

### Esempio

```bash
curl -X POST http://localhost:8080/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"marco@example.com","password":"password-lunga"}'

TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"marco@example.com","password":"password-lunga"}' | jq -r .access_token)

curl http://localhost:8080/api/auth/me -H "Authorization: Bearer $TOKEN"
```

## Come funziona l'upload

```
client                api-service            object storage
  │                        │                        │
  │ POST /api/assets ─────>│                        │
  │  (nome, tipo, peso)    │ registra l'asset       │
  │<──── 201 + link firmato│                        │
  │                        │                        │
  │ PUT del file ──────────────────────────────────>│
  │                        │                        │
```

Il file non passa mai dall'api-service: viaggia dal client all'object storage.
Il servizio si limita a firmare un permesso temporaneo.

A upload concluso il client chiama `/complete`: il servizio verifica sull'object
storage che il file esista davvero, ne misura la dimensione reale, pubblica il
job e risponde `202`. Da lì in poi il lavoro è del worker, e il frontend segue
lo stato in polling.

Il client deve inviare l'header `Content-Type` indicato nella risposta: fa parte
di ciò che la firma copre, e un valore diverso invalida il link.

La chiave dell'oggetto è `{user_id}/{asset_id}.{ext}` e non contiene mai il nome
scelto dall'utente: quel nome viene conservato in `assets.filename` per
mostrarlo, ma non determina nessun percorso.

### Esempio

```bash
RESP=$(curl -s -X POST http://localhost:8080/api/assets \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"filename":"foto.jpg","mime":"image/jpeg","size_bytes":15629}')

URL=$(echo "$RESP" | jq -r .upload.url)
curl -X PUT --upload-file foto.jpg -H 'Content-Type: image/jpeg' "$URL"
```

## Scelte di sicurezza

- La password non viene mai salvata: se ne memorizza solo l'hash (`scrypt`,
  lento di proposito, tramite Werkzeug che Flask già include).
- Login con email inesistente e login con password errata restituiscono lo
  stesso errore e impiegano lo stesso tempo: altrimenti si potrebbe scoprire
  quali indirizzi hanno un account.
- L'unicità dell'email è garantita da un vincolo del database, non da una
  `SELECT` preventiva: due registrazioni simultanee non possono passare entrambe.
- Il token è firmato e scade: il server non conserva nessuna sessione, quindi le
  repliche dell'api-service non hanno stato da condividere.

## Schema del database

Le tabelle sono descritte dai file SQL numerati in `migrations/`, applicati una
sola volta ciascuno e registrati nella tabella `schema_migrations`. Rilanciare le
migrazioni non ha effetti: quelle già applicate vengono saltate.

```bash
docker compose run --rm db-migrate     # dalla cartella deploy/compose
```

In compose il comando gira come servizio a sé (`db-migrate`) che termina prima
che partano gli altri: lo schema viene applicato una volta sola, da un solo
processo, indipendentemente dal numero di repliche. Sul cluster lo stesso comando
diventa un Job di Kubernetes.

Per aggiungere una modifica allo schema si crea un nuovo file (`002_...sql`), mai
si modifica uno già applicato.

## Cancellare un'immagine

`DELETE /api/assets/{id}` la toglie di mezzo per davvero. Tre archivi devono
dimenticarla:

| Archivio | Cosa sparisce |
|---|---|
| PostgreSQL | la riga, e con lei varianti e avvisi, che cadono in cascata |
| Object storage | l'originale e le tre varianti |
| Documentale | i metadati tecnici |

**L'ordine non è casuale: prima la riga, poi i file.** Se si cancellassero
prima i file e la riga poi fallisse, la libreria resterebbe a mostrare
un'immagine il cui file non esiste più — rotta da guardare e impossibile da
sistemare dall'interfaccia. Così, un guasto a metà lascia oggetti senza
padrone in un bucket: invisibili, e ripulibili.

Per la stessa ragione la rimozione dei file **non può far fallire la
richiesta**: quando tocca a loro la riga è già sparita, quindi per l'utente
l'immagine è cancellata. Un file rimasto indietro finisce nel registro come
avviso, non come errore.

Un solo caso viene rifiutato con `409`: un'immagine che un worker ha in mano
proprio adesso. Cancellarla lì lo farebbe fallire mentre scrive le varianti di
una riga che non c'è più, e quel messaggio finirebbe nella coda dei rifiutati —
molto rumore per un'immagine che si voleva solo buttare. L'attesa è breve e
finisce da sola.

Chiedere di cancellare l'immagine di un altro risponde `404`, non `403`: dire a
uno sconosciuto «esiste ma non è tua» è dirgli che esiste.

## Sorveglianza delle code dei rifiutati

```
python -m app.dlq_watch
```

Conta i messaggi fermi in `media.process.dlq` e `notifications.dlq` e li scrive
nel registro. Esce con `1` se non sono vuote, così un CronJob fallito è il
segnale — si vede in `kubectl get jobs` senza leggere niente.

Un messaggio finisce lì quando nessuno è riuscito a capirlo o quando i
tentativi si sono esauriti: è un fatto che vale la pena sapere, e fino a ieri
nel sistema non lo diceva nessuno.

Usa l'interfaccia di gestione del broker, non AMQP: contare i messaggi senza
consumarli è esattamente ciò per cui esiste, e non serve nessuna libreria.

Sul cluster è il CronJob `dlq-watch`, ogni quindici minuti. **È rilevazione,
non allarme**: qualcuno deve leggere il registro, o lanciare
`deploy/cluster/app.sh status`. L'allarme che sveglia una persona è un
CloudWatch alarm sulla coda di SQS, ed è roba della Fase 7.

## Creazione dei bucket

Stessa forma delle migrazioni: un comando a sé che gira una volta e termina.

```bash
docker compose run --rm minio-init     # dalla cartella deploy/compose
```

```
python -m app.buckets
```

Legge **solo** le variabili dell'object storage, non l'intera configurazione:
questo comando non ha niente a che fare con un database o con un broker, e
chiederne l'indirizzo lo farebbe fallire per motivi che non lo riguardano.

Aspetta che l'archivio risponda invece di arrendersi al primo rifiuto — parte
nello stesso istante di MinIO, che è il più lento dei due — e rilanciarlo non
ha effetti: i bucket già esistenti vengono riconosciuti e lasciati stare.

In compose è il servizio `minio-init`, in Kubernetes il Job `create-buckets`, e
su AWS lo stesso compito è di Terraform. Fino alla Fase 5 lo faceva il client
`mc` di MinIO: sostituito per non dover portare a mano un'immagine in più
dentro un cluster senza registro.

## Pulizia delle registrazioni abbandonate

Un caricamento è in due passi con un client in mezzo: `POST /assets` scrive la
riga e restituisce il link firmato, `POST /assets/{id}/complete` dice che il
file c'è. Se il client sparisce fra i due — scheda chiusa, rete caduta, o un
programma che chiede un link e se ne va — resta una riga che non diventerà mai
un'immagine.

Due difese, perché servono entrambe:

- la libreria mostra solo ciò che è stato davvero consegnato
  (`submitted_at IS NOT NULL`): una registrazione non è un'immagine;
- un comando periodico elimina quelle vecchie, insieme all'eventuale file
  rimasto nel bucket senza conferma.

```bash
docker compose run --rm --entrypoint python api-service -m app.cleanup --dry-run
docker compose run --rm --entrypoint python api-service -m app.cleanup
```

Solo il tempo trascorso distingue una registrazione abbandonata da una ancora
in corso: il valore predefinito è 24 ore, mentre un link firmato dura 15
minuti. Sul cluster il comando è il CronJob `cleanup`, ogni notte alle 3.

Qui vivono anche le tabelle che questo servizio non scrive: `notifications` è
riempita dal notification-service e da qui viene solo letta. Lo schema ha un solo
proprietario e una sola storia ordinata, per quanti siano i servizi che ne
toccano le tabelle.

## Sviluppo locale

Il servizio si avvia con il resto dello stack:

```bash
cd deploy/compose
docker compose up --build api-service
```
