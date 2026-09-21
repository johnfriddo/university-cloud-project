# frontend

Single-Page Application Angular servita da Nginx. Gestisce autenticazione,
upload multiplo, polling sullo stato dei job e libreria filtrabile.

## Linguaggio visivo

Sfondo bianco, testo e linee nere, nessun colore e nessuna emoji. Le distinzioni
le fanno la tipografia e la forma:

| Stato | Marcatore |
|---|---|
| Pronta | `□` quadrato vuoto |
| Errore | `■` quadrato pieno |
| In lavorazione o in attesa | `▪` quadrato piccolo |

Carattere di sistema, nessun font scaricato da internet: l'applicazione deve
funzionare anche in un cluster senza uscita sulla rete. Bordi da un pixel,
niente ombre, niente angoli arrotondati.

## Rotte

| Rotta | Accesso | Contenuto |
|---|---|---|
| `/accedi` | solo senza sessione | login e registrazione, stesso modulo |
| `/libreria` | solo con sessione | caricamento, griglia, filtri |
| `/immagini/:id` | solo con sessione | anteprima, versioni scaricabili, dati tecnici |
| `/avvisi` | solo con sessione | i messaggi del notification-service |
| qualsiasi altra | — | rimanda a `/libreria` |

Le pagine sono caricate su richiesta: chi è già dentro non scarica mai il codice
della pagina di accesso.

## Filtri

Stanno **nell'indirizzo**, non dentro il componente:
`/libreria?camera=Canon&status=DONE&page=2`. Una vista filtrata diventa così un
collegamento condivisibile, il pulsante «indietro» del browser fa quello che
l'utente si aspetta e ricaricare la pagina non azzera nulla. Ne segue anche una
sola strada verso i dati: l'indirizzo cambia, la pagina lo legge e carica.

`filename`, `status`, `from` e `to` li risolve PostgreSQL; `camera`, `lens`,
`focal_length`, `iso_min` e `iso_max` li risolve MongoDB, che restituisce gli
identificativi corrispondenti. È l'unico punto dove i due database lavorano
insieme sulla stessa domanda, e la Fase 2 lo aveva già predisposto.

Le miniature vengono ricordate per identificativo. I link firmati sono
temporanei, quindi ogni ricarica produrrebbe un indirizzo diverso per la stessa
immagine — e un indirizzo diverso significa scaricarla di nuovo. Durante il
polling si tiene quello già a schermo; solo una navigazione vera ne chiede di
nuovi.

## Comunicazione con l'API

Il frontend chiama `/api` in modo **relativo** e non sa dove si trovi l'API:
Nginx inoltra `/api` all'`api-service`, e nel cluster lo stesso lavoro lo fa
l'Ingress (l'ALB su AWS). Per questo non serve nessuna configurazione iniettata
a runtime.

### Le due variabili di Nginx

`nginx.conf.template` non è una configurazione finita: l'immagine di Nginx ne
sostituisce i segnaposto all'avvio, leggendo l'ambiente. Serve perché
l'indirizzo del DNS interno cambia fra Docker e Kubernetes, e un'immagine che
nomina quello di Docker è un'immagine che funziona solo in Docker.

| Variabile | In Docker | Nel cluster | A cosa serve |
|---|---|---|---|
| `DNS_RESOLVER` | `127.0.0.11` | l'indirizzo di `kube-dns` | risolvere il nome dell'api-service, e rifarlo ogni dieci secondi |
| `API_UPSTREAM` | `http://api-service:8000` | uguale | dove mandare `/api` |

I valori di Docker sono scritti come predefiniti nel `Dockerfile`, così
l'immagine da sola resta utilizzabile; nel cluster li sovrascrive il chart.
Nginx risolve solo i nomi che esistono come variabili d'ambiente, quindi le
sue (`$uri`, `$host`, `$scheme`) restano intatte.

Sul cluster questo inoltro non viene mai usato, perché `/api` lo porta
all'api-service l'Ingress prima che la richiesta arrivi qui. Resta corretto
lo stesso: una configurazione che «tanto non si usa» è una trappola per chi
la troverà.

Un interceptor aggiunge il token alle chiamate verso `/api` **e solo a quelle**:
l'upload va direttamente all'object storage con un link già firmato, e
un'intestazione `Authorization` in più farebbe vedere allo storage due
meccanismi di autenticazione in conflitto.

Sempre l'interceptor, davanti a un `401`, chiude la sessione e riporta al login,
invece di lasciar fallire una per una tutte le chiamate successive.

## Caricamento

Ogni file percorre tre passi, uno solo dei quali tocca i nostri servizi con dei
byte — e non è il file:

1. `POST /api/assets` registra l'immagine e restituisce un link firmato;
2. `PUT` diretto all'object storage, con il `Content-Type` che la firma copre;
3. `POST /api/assets/{id}/complete` conferma e mette il job in coda.

Tre file per volta (`CONCURRENCY` in `core/upload.service.ts`). Il carico è a
raffica per definizione — duecento foto insieme — e duecento richieste
simultanee saturerebbero le connessioni del browser rendendo ogni singolo
caricamento più lento, oltre a lasciare senza spazio le chiamate all'API.

Un file rifiutato non ferma gli altri: registra il proprio motivo e la raffica
prosegue. Formato e dimensione sono controllati anche qui, prima di disturbare
l'API, ma il limite che conta resta quello del server.

> **Debito dichiarato.** Se il `PUT` fallisce, la riga su PostgreSQL resta
> registrata senza file: **Riprova** riparte dal passo 1 e ne crea una nuova.
> Servirà un lavoro periodico che ripulisca gli asset mai confermati.

Il numero d'ordine delle righe a schermo è un contatore, non
`crypto.randomUUID()`: quella funzione esiste solo in contesto sicuro e nel
cluster della Fase 5 si arriverà via http su un indirizzo IP, dove è assente.

## Avanzamento

L'elaborazione avviene in un worker: nessuno può rispondere «è pronta?» dentro
la richiesta che l'ha avviata. La pagina lo richiede da sé, ogni due secondi, a
`GET /api/assets/status?ids=…` — che restituisce **soltanto** stato, errore e
data. Ricaricare l'intera libreria a ogni giro rifirmerebbe tutti i link di
download, e siccome una firma nuova è un URL diverso, il browser riscaricherebbe
ogni miniatura due secondi dopo la precedente.

Quando qualcosa arriva a destinazione la pagina ricarica **una volta**: le
varianti e i link non stanno in quella risposta ridotta. Al massimo una
ricarica ogni due secondi, anche con duecento immagini in corso.

L'orologio si ferma da solo in tre casi:

| Situazione | Comportamento |
|---|---|
| Non resta nulla di non terminale | smette e basta |
| La scheda passa in secondo piano | salta i giri, riprende quando torna visibile |
| Cinque minuti senza un solo cambiamento | si arrende e lo dice |

L'ultimo caso non è teorico: un job morto mentre il database era irraggiungibile
non lascia né un messaggio in coda né qualcuno che possa segnare l'errore.
Quell'immagine resterebbe non terminale per sempre, e senza limite tutte le
schede aperte interrogherebbero l'API ogni due secondi all'infinito. Quando il
polling si arrende, **Riprova** compare anche sulle righe non terminali: se è
troppo presto è l'`api-service` a rifiutare, spiegando il perché.

## Avvisi

In locale non esiste un server di posta, quindi `/avvisi` è **l'unico canale che
raggiunge davvero una persona**: il notification-service scrive il messaggio nel
suo log e nella tabella, e questa pagina lo rende leggibile. Su AWS lo stesso
testo esce anche da SNS, e i due canali convivono — quello che insegue l'utente
fuori dall'applicazione e questo, per quando rientra.

Il conteggio dei nuovi non è una colonna del database: il browser ricorda in
`localStorage` quando ha aperto l'elenco l'ultima volta e chiede all'API quanti
ne sono arrivati dopo (`?since=`). «Letto» è una proprietà di una persona
davanti a uno schermo, non della notifica, e una colonna avrebbe richiesto un
endpoint che la scrive — per un numerino. Il prezzo è che il conteggio è per
browser: aprire gli avvisi sul portatile non li segna letti sul telefono.

## Il token

Sta in `localStorage`, così una ricarica della pagina non fa perdere la sessione.
All'avvio ne viene letta la scadenza — il contenuto di un JWT è leggibile da
chiunque, non serve nessun segreto — e un token scaduto viene buttato senza
nemmeno provarci.

> **Scelta dichiarata.** `localStorage` espone il token a un eventuale attacco
> XSS. L'alternativa robusta è un cookie `HttpOnly`, che però richiederebbe
> all'API di gestire i cookie e la protezione CSRF. Per un progetto didattico il
> compromesso è accettabile, ma è bene saperlo.

## Sviluppo

L'immagine è costruita in due stadi: Node compila, Nginx serve. Nell'immagine
finale non restano né Node né le dipendenze di build.

```bash
cd deploy/compose
docker compose up --build frontend
```

Per lavorare con la ricompilazione automatica serve Node 24 o superiore:

```bash
cd services/frontend
npm install
npm start          # http://localhost:4200
```

In quel caso le chiamate a `/api` vanno inoltrate: aggiungi un proxy di sviluppo
oppure lavora attraverso il container, che è la strada già pronta.

## Controlli attivi

`strict` di TypeScript e `strictTemplates` di Angular sono entrambi attivi: gli
errori di tipo, anche dentro i template, fermano la build invece di presentarsi
come pagina bianca nel browser.
