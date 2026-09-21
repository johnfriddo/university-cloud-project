# Chart `media-platform`

La traduzione di `deploy/compose/docker-compose.yml` nel linguaggio di
Kubernetes. Stessi otto componenti, stessi nomi, stesse variabili d'ambiente:
quello che cambia è chi li tiene accesi.

## Installazione

```bash
deploy/cluster/app.sh secrets    # una volta sola: genera le password
deploy/cluster/app.sh install
```

Poi si apre `http://media-platform.test`.

Perché quel nome funzioni serve una riga nel `/etc/hosts` del Mac, con
l'indirizzo del nodo che ospita l'ingresso:

```
192.168.252.3   media-platform.test
```

## Cosa contiene

| Oggetto | Tipo | Perché quel tipo |
|---|---|---|
| `postgres` | StatefulSet + disco | ha uno stato, e il disco non si può condividere fra due pod |
| `mongo` | StatefulSet + disco | idem |
| `rabbitmq` | StatefulSet + disco | i messaggi persistenti devono sopravvivere al riavvio |
| `minio` | StatefulSet + disco | i file sono il suo stato |
| `api-service` | Deployment | non conserva niente: il token è firmato, non memorizzato |
| `worker-service` | Deployment | stateless per progetto, in 5.5 lo scala KEDA |
| `notification-service` | Deployment | stateless |
| `frontend` | Deployment | serve file statici |
| `db-migrate` | Job | applica lo schema una volta sola |
| `create-buckets` | Job | crea i due bucket |
| `cleanup` | CronJob | ogni notte, le registrazioni abbandonate |
| `dlq-watch` | CronJob | ogni 15 minuti, guarda le code dei rifiutati |
| `worker-service` | ScaledObject | quante repliche, in base alla coda |
| `media-platform` | Ingress | un nome di dominio, quattro destinazioni |
| `app-config` | ConfigMap | i valori non segreti |
| `app-secrets` | Secret | le credenziali |

### Perché StatefulSet e non Deployment per gli archivi dati

Non è l'ordine di avvio, che qui non serve: è l'aggiornamento. Un Deployment,
per sostituire un pod, ne accende uno nuovo **prima** di spegnere il vecchio.
Il nuovo chiederebbe lo stesso disco, che il vecchio non ha ancora rilasciato,
e resterebbe bloccato per sempre. Uno StatefulSet spegne prima e riaccende
dopo: l'unico ordine sensato quando c'è un disco solo.

### Perché due Job normali e non due hook di Helm

Un hook `post-install` parte quando Helm ha visto tutto pronto. Ma
l'`api-service` diventa pronto solo quando l'object storage risponde alla
domanda «esiste il bucket `originals`?», e quel bucket lo crea proprio il Job.
Si aspetterebbero a vicenda per sempre.

Nati insieme a tutto il resto, invece, i due Job riprovano — `restartPolicy:
OnFailure` — finché database e archivio non rispondono. Nessuno aspetta
nessuno, e il sistema si mette a posto da solo.

Il nome dei Job contiene il numero di revisione (`db-migrate-1`,
`db-migrate-2`, …) perché un Job, una volta creato, è immutabile: alla seconda
installazione Helm troverebbe un oggetto identico che non può modificare, e si
fermerebbe. Si cancellano da soli dieci minuti dopo la fine.

## Il bilancio della memoria

I due nodi operai hanno 1452 MB ciascuno, di cui circa 500 già occupati da
sistema operativo, kubelet e containerd. Restano **circa 950 MB per nodo**,
1,9 GB in due, e dentro ci deve stare tutto.

| Componente | Richiesta | Tetto | Nota |
|---|---|---|---|
| `postgres` | 128 Mi | 256 Mi | `shared_buffers` abbassato a 64 MB |
| `mongo` | 192 Mi | 320 Mi | cache WiredTiger fissata a 0,25 GB |
| `rabbitmq` | 160 Mi | 300 Mi | |
| `minio` | 160 Mi | 320 Mi | |
| `api-service` | 128 Mi | 256 Mi | gunicorn con 2 processi |
| `worker-service` | 160 Mi | 384 Mi | il più alto: Pillow apre l'immagine intera |
| `notification-service` | 64 Mi | 128 Mi | |
| `frontend` | 32 Mi | 64 Mi | Nginx e file statici |
| **totale** | **1024 Mi** | **2028 Mi** | più 128/256 Mi dell'ingresso |

**Richiesta** e **tetto** rispondono a due domande diverse. La richiesta è
quanta memoria il pod si prenota: Kubernetes usa quella per decidere su quale
nodo metterlo, e non ci mette niente che non ci stia. Il tetto è quanta ne può
usare davvero: oltrepassarlo significa essere uccisi per memoria.

La somma delle richieste (1 GB) sta comoda nei 2,7 GB prenotabili dei due nodi.
La somma dei tetti (2 GB) supera i 1,9 GB realmente liberi: è voluto. Un tetto
è un limite, non una prenotazione, e i componenti non arrivano al massimo tutti
nello stesso istante. Se poi succedesse — è esattamente il terzo criterio di
resa scritto in `docs/registro-cluster.md`, e si vedrebbe come `OOMKilled`.

### Due tarature che non sono opzionali

**MongoDB.** La cache di WiredTiger si dimensiona sulla memoria della
*macchina*, non su quella concessa al container: su un nodo da 1,4 GB ne
chiederebbe centinaia di MB oltre il tetto e il pod verrebbe ucciso appena la
riempie. `--wiredTigerCacheSizeGB 0.25` glielo impedisce.

**PostgreSQL.** `shared_buffers` predefinito è 128 MB, metà del tetto del pod
prima ancora di iniziare a lavorare. Abbassato a 64 MB.

### Perché nessun tetto sulla CPU

Un tetto di CPU non uccide un processo: lo rallenta, fermandolo per frazioni di
secondo ogni volta che supera la quota. Su un cluster dove il problema è la
memoria, aggiungerebbe lentezza senza risolvere niente. Le richieste ci sono
lo stesso, perché servono a distribuire i pod sui nodi.

## Le sonde

Ogni pod risponde a due domande diverse, e la differenza decide cosa fa
Kubernetes:

- **live** — «il processo è vivo?» Se no, il pod viene **riavviato**. Per
  questo non interroga mai le dipendenze: riavviare un servizio non ripara un
  database irraggiungibile, lo farebbe solo girare a vuoto.
- **ready** — «può lavorare?» Interroga database, broker e object storage. Se
  no, il pod resta acceso ma **esce dai destinatari** del Service: nessuna
  richiesta gli arriva finché non torna in sé.

Gli archivi dati fanno eccezione: `mongo` e `rabbitmq` sono controllati sulla
porta e non con i loro comandi diagnostici. Ogni `mongosh` avvia un interprete
JavaScript, ogni `rabbitmq-diagnostics` un secondo nodo Erlang; ripetuti ogni
dieci secondi dentro tetti di 300 MB, spenderebbero in controlli la memoria che
serve al lavoro. Chi ha bisogno di sapere se rispondono davvero — i servizi che
li usano — se lo chiede da sé, con la connessione che usa.

## Le repliche del worker

Le decide la coda, non la CPU. Un worker in attesa di un messaggio non consuma
processore, e quando comincerebbe a consumarlo il carico è già arrivato tutto:
la CPU è una spia che si accende sempre troppo tardi. KEDA legge quanti
messaggi aspettano e passa il numero al normale HorizontalPodAutoscaler.

| Impostazione | Valore | Perché |
|---|---|---|
| minimo | 1 | zero sarebbe possibile, ma la prima immagine dopo una pausa aspetterebbe l'avvio del pod |
| massimo | 3 | oltre, Kubernetes non troverebbe dove mettere la replica |
| messaggi per replica | 5 | 15 in coda chiedono 3 worker |
| prima di scendere | 60 s | finestra di stabilizzazione dell'HPA |

La reazione non è istantanea e non può esserlo: da una replica in su è
l'HorizontalPodAutoscaler a decidere, e chiede a KEDA quanto è lunga la coda
una volta ogni 15 secondi. `pollingInterval` e `cooldownPeriod` di KEDA
servono soltanto ad accendere la prima replica e a spegnere l'ultima, quindi
il chart li scrive solo se il minimo è zero — altrimenti KEDA risponde con un
avviso che dice esattamente questo.

KEDA va installato a parte, una volta per cluster:
`deploy/cluster/cluster.sh keda` (è già dentro `addons`). Costa 63 Mi.

### Due dettagli che non sono ovvi

**Il Deployment non dichiara le repliche.** Con l'autoscaling acceso il campo
`replicas` sparisce dal template. Se restasse, ogni `helm upgrade` riporterebbe
il conteggio al valore scritto nel chart, cancellando in un istante quello che
l'autoscaler aveva deciso guardando la coda.

**KEDA chiama il broker per nome e cognome.** Vive in un namespace suo, e da lì
il nome breve `rabbitmq` non risolve: la scorciatoia vale solo dentro il
namespace che ospita il servizio. Per questo il Secret contiene un secondo
indirizzo, `RABBITMQ_URL_FQDN`, identico al primo ma scritto per intero, che
usa soltanto l'autoscaler.

### Vederlo funzionare

```bash
deploy/cluster/burst.sh 300                    # con l'autoscaler
FIXED_REPLICAS=1 deploy/cluster/burst.sh 300   # senza, per confronto
```

Misurato su questo cluster: **67,8 s con un worker, 37,7 s con tre**. Non tre
volte più veloce, perché i nodi hanno due processori ciascuno e su quegli
stessi processori girano anche i database. Il ragionamento completo sta in
`docs/registro-cluster.md`.

Il conto delle immagini non è a caso: un carico più breve di una ventina di
secondi finisce prima che l'autoscaler possa accorgersene, e le repliche non si
muovono. È nella natura di un autoscaler, non un difetto.

## Le credenziali

Non sono nel chart. `values.yaml` le lascia vuote, e il Secret si rifiuta di
generarsi nominando quella che manca:

```
manca secrets.jwtSecret (chiave di firma dei token): genera le credenziali
con `deploy/cluster/app.sh secrets`
```

`app.sh secrets` scrive `deploy/helm/values.local.yaml` con valori casuali
presi dal generatore del sistema. Il file non entra in git: `.gitignore` lo
esclude, e i permessi lo lasciano leggibile solo al proprietario.

Solo lettere e cifre, di proposito: quelle password finiscono dentro indirizzi
come `postgresql://utente:password@host/db`, dove una barra o una chiocciola
verrebbero lette come parte dell'indirizzo.

Un Secret di Kubernetes è codificato in base64, non cifrato: chi può leggere il
namespace può leggerlo. Su AWS lo sostituisce Secrets Manager.

## L'ingresso

Un solo nome di dominio, quattro destinazioni:

| Rotta | Destinazione |
|---|---|
| `/` | `frontend` |
| `/api` | `api-service` |
| `/originals` | `minio` |
| `/derived` | `minio` |

Le ultime due non sono un'invenzione: MinIO si indirizza come
`endpoint/bucket/chiave`, quindi l'indirizzo di un oggetto del bucket
`originals` comincia davvero per `/originals`.

Tenere tutto sotto lo stesso nome ha un motivo preciso. Il browser considera
due indirizzi «la stessa origine» solo se coincidono schema, nome **e porta**.
In docker compose la pagina veniva da `localhost:8080` e il file andava su
`localhost:9000`: due origini, e senza un permesso esplicito di MinIO (CORS) il
browser avrebbe bloccato l'upload. Qui l'origine è una sola e il permesso non
serve.

### Perché la porta 80 e non la 30080

I link di caricamento e scaricamento sono **firmati**, e la firma copre anche
il nome dell'host. NGINX, quando inoltra la richiesta a MinIO, passa il nome
**senza la porta**. Un link firmato per `media-platform.test:30080`
arriverebbe a MinIO annunciando `media-platform.test`, la firma non
corrisponderebbe più, e ogni upload e ogni download verrebbero rifiutati.

Sulla porta 80 non c'è nessuna porta da perdere. Per questo
`deploy/cluster/cluster.sh` installa il controller con `hostPort` sulla 80 di
un nodo preciso, quello nominato nel `/etc/hosts`. La 30080 resta aperta, ma
come porta di servizio.

### Un solo nome, tre oggetti Ingress

Le annotazioni di NGINX valgono per **l'intero Ingress**, non per la singola
rotta. Un limite di frequenza scritto su un Ingress unico colpirebbe anche
`/derived`, cioè le decine di miniature che il browser chiede tutte insieme
aprendo la libreria.

Il controller offre l'Ingress «componibile»: un **master** che possiede il nome
di dominio, e dei **minion** che portano le rotte, ognuno con le proprie
annotazioni.

| Oggetto | Rotte | Limite |
|---|---|---|
| `media-platform` (master) | nessuna, possiede il nome | — |
| `media-platform-auth` | `/api/auth` | 10 al minuto per indirizzo, più 5 di scorta |
| `media-platform-app` | `/api`, `/originals`, `/derived`, `/` | nessuno |

### Il limite di frequenza, e perché esenta il cluster

Il limite sta solo sulle rotte di registrazione e accesso: è dove si tenta di
indovinare una password. Sul resto il traffico legittimo è a raffiche.

Dimensionarlo ha richiesto una misura, perché la prima idea non funzionava:

| | |
|---|---|
| Richieste su `/api/auth` durante la suite di integrazione | 61 in 31 secondi |
| Ritmo, da un solo indirizzo | **118 al minuto** |

Un limite stretto abbastanza da servire (10-30 al minuto) fermerebbe la nostra
suite; uno che la lascia passare non proteggerebbe da niente. La via d'uscita è
quella che si usa davvero: **si esenta la rete interna**. Un frammento nella
configurazione del controller
(`deploy/cluster/nginx-ingress-values.yaml`) assegna una chiave vuota agli
indirizzi dei pod, e a chiave vuota NGINX non applica nessun limite.

**Con un'insidia che è costata un tentativo.** Perché l'esenzione funzioni, i
test devono entrare *attraverso il Service* dell'ingresso e non attraverso la
porta 80 del nodo: un pod che raggiunge l'indirizzo di un nodo esce dal cluster
e rientra, e per strada il suo indirizzo viene tradotto in quello del nodo.
NGINX vedeva `client: 192.168.252.7` — una macchina, non un pod — e il limite
scattava lo stesso.

Prova, dal Mac:

```
401 401 401 401 401 401 429 429 429 429 429 429 429 429 429
```

### Il tetto sul corpo della richiesta

`nginx.org/client-max-body-size: 32m`. Il predefinito di NGINX è **1 MB**:
senza questa riga ogni caricamento oltre quella soglia verrebbe respinto
dall'ingresso con un `413`, prima ancora di arrivare a MinIO, mentre
l'applicazione ne accetta fino a 25.

## Comandi

```bash
deploy/cluster/app.sh status              # pod, dischi, lavori, memoria
deploy/cluster/app.sh logs worker-service # il registro di un servizio
deploy/cluster/app.sh template            # cosa verrebbe installato, senza installarlo
deploy/cluster/app.sh uninstall           # disinstalla, i dischi restano
deploy/cluster/app.sh purge               # disinstalla e cancella anche i dischi
```

E per verificare che funzioni davvero:

```bash
deploy/cluster/smoke.sh        # un giro completo in 11 passi, come il browser
deploy/cluster/app.sh test     # i 54 test di integrazione, dentro il cluster
deploy/cluster/faults.sh       # cinque guasti veri: pod uccisi mentre si lavora
deploy/cluster/burst.sh        # 300 immagini insieme, per vedere l'autoscaler
```

Helm gira **dentro** la macchina del piano di controllo, non sul Mac: là
`kubectl` e le credenziali del cluster ci sono già. Il chart ci arriva come un
archivio via SSH a ogni comando, così quello che viene installato è sempre
quello che c'è sul disco adesso.
