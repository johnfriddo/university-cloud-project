# Test

Serve solo Docker. Un comando per tutto:

```bash
scripts/test.sh all
```

## I comandi

| Comando | Cosa fa | Quanto dura |
|---|---|---|
| `scripts/test.sh unit` | test di unità dei servizi Python | un paio di secondi |
| `scripts/test.sh frontend` | test di unità del frontend Angular | pochi secondi |
| `scripts/test.sh lint` | controllo dello stile con `ruff` | un secondo |
| `scripts/test.sh integration` | il sistema intero, su uno stack di prova | mezzo minuto |
| `scripts/test.sh faults` | guasti veri: spegne broker, worker, database | un minuto e mezzo |
| `scripts/test.sh all` | lint, unità, frontend e integrazione | un minuto |
| `scripts/test.sh down` | spegne lo stack di prova e ne cancella i dati | — |

Gli argomenti in più vanno a pytest: `scripts/test.sh unit -k exif -v` esegue
solo i test che nominano l'EXIF, con il dettaglio di ognuno.

## Gli stessi test, ma sul cluster

Quando l'applicazione gira su Kubernetes (Fase 5), la suite di integrazione si
può eseguire **da dentro il cluster**, senza cambiare una riga:

| Comando | Cosa fa | Quanto dura |
|---|---|---|
| `deploy/cluster/app.sh test` | i 54 test di integrazione, attraverso l'ingresso | mezzo minuto |
| `deploy/cluster/smoke.sh` | un giro completo in 11 passi, come il browser | un minuto |
| `deploy/cluster/faults.sh` | cinque guasti veri: pod uccisi mentre si lavora | una decina di minuti |
| `deploy/cluster/burst.sh` | 300 immagini insieme, per vedere l'autoscaler | due minuti |
| `deploy/cluster/load.sh` | il test di carico con k6, come da specifica | due minuti |

La differenza non è dove parte il comando ma **cosa attraversa la richiesta**.
Su compose i test parlano direttamente con l'`api-service`; sul cluster passano
per il nome di dominio e per l'ingresso, quindi provano anche le regole di
smistamento, i link firmati e la risoluzione dei nomi — cioè proprio le cose
che in Kubernetes possono rompersi e in Docker no.

I test di guasto sono gli unici che non si riusano: `test_guasti.py` guida
Docker, e ciò che si vuole provare è quello che Kubernetes fa diversamente.
Il sostituto è `deploy/cluster/faults.sh`.

Il test di carico con k6 ha una pagina sua, con i numeri e le trappole del
metodo: [tests/load/README.md](load/README.md).

## Gli stessi comandi, a ogni push

`.github/workflows/verifica.yml` lancia `scripts/test.sh lint`, `unit`,
`frontend` e `integration` su ogni push e su ogni pull request verso `main`,
più i controlli dell'infrastruttura (chart, Terraform, playbook).

**Sono gli stessi comandi, non una copia.** Se la CI ne eseguisse di propri,
prima o poi i due elenchi divergerebbero e «in locale passa» diventerebbe una
frase ricorrente. `scripts/test.sh` gira tutto dentro Docker, quindi si
comporta identico sul portatile e su un runner.

Quello che la CI **non** può fare è il cluster: `smoke.sh`, `app.sh test`,
`faults.sh` e `load.sh` hanno bisogno di tre macchine virtuali e restano
comandi da lanciare a mano.

## Due livelli, per due domande diverse

**Unità** — una funzione da sola. Nessuna rete, nessun database. Rispondono a
domande come «il diaframma f/2.8 viene letto bene dalla coppia (28, 10)?»: non
ha senso accendere PostgreSQL per saperlo.

**Integrazione** — tutto il sistema acceso. Rispondono a domande come «una foto
caricata arriva fino all'avviso?» o «se il broker si spegne, i consumatori
crollano?», a cui nessuna funzione presa da sola sa rispondere.

Ogni test dichiara di che tipo è con un marcatore (`unit`, `integration`, `slow`):
un marcatore scritto male fa fallire subito l'esecuzione, invece di far girare in
silenzio i test sbagliati.

## Dove stanno

```
services/common/tests/          adapter condivisi: nomi dei file, topologia della coda
services/api-service/tests/     token, confini con il client, configurazione
services/worker-service/tests/  EXIF e generazione delle varianti
services/frontend/src/**/*.spec.ts   errori, controlli sul caricamento, sessione
tests/integration/              il sistema intero
```

I test di unità stanno dentro il servizio che provano, perché ne importano il
codice. Quelli di integrazione stanno qui, perché non appartengono a nessun
servizio: provano come stanno insieme.

## Lo stack di prova

I test di integrazione **non usano lo stack di sviluppo**. `scripts/test.sh` ne
accende uno a parte, con un altro nome (`media-platform-test`), porte spostate e
database propri, configurato da `deploy/compose/.env.test`.

Due ragioni:

1. **Non toccano i tuoi dati**, e partono da una situazione nota invece che da
   quello che è rimasto in giro.
2. **I link firmati.** Nello sviluppo valgono per l'indirizzo del browser,
   `localhost:9000`, che dall'interno di un container non esiste: per il
   container `localhost` è sé stesso. Nello stack di prova l'indirizzo pubblico
   dell'archivio è il nome interno `minio`, e i test possono caricare un file
   esattamente come farebbe il browser.

Prima di ogni esecuzione lo stack di prova viene **ricostruito** con il codice
attuale: `compose run` accenderebbe i servizi mancanti ma non aggiornerebbe
quelli già accesi, e i test proverebbero in silenzio la versione di prima.

Il codice dei test è **montato dal disco**: si modifica un test e si rilancia,
senza ricostruire niente. In CI il codice arriverà dal checkout ed è montato allo
stesso identico modo.

## I test sui guasti

`scripts/test.sh faults` spegne e riaccende container dello stack di prova, e
per farlo il container dei test riceve il socket di Docker. È un potere grande,
per questo quei test girano solo quando li si chiede esplicitamente.

Qualunque cosa succeda, alla fine **ogni container spento viene riacceso**: un
test fallito a metà non lascia lo stack con un database spento.

Coprono le promesse dell'architettura che nessuno può controllare a occhio:

| Guasto | Cosa deve succedere |
|---|---|
| worker spento | i lavori aspettano in coda, nessuno si perde |
| broker spento | i consumatori restano vivi ma non pronti, senza riavvii |
| broker che torna | il sistema lavora di nuovo |
| database spento durante un'elaborazione | tre tentativi, poi la coda dei rifiutati |

## Verificare che qualcosa *non* succeda

«Lo stesso evento consegnato due volte non produce due avvisi» è una prova
d'assenza, ed è delicata: quanto aspettare prima di dire che il secondo avviso
non arriverà? Una pausa fissa è troppo lunga o troppo corta a seconda della
macchina.

La soluzione usata qui è un **segnalino**: dopo i duplicati si mette in coda un
messaggio qualsiasi, e si aspetta che *quello* venga trattato. La coda è una e il
consumatore la legge in ordine: quando il segnalino è passato, i duplicati prima
di lui sono per forza già stati gestiti.

## Un test serve se sa fallire

Una suite che passa sempre non prova niente. Il modo di verificarlo è rompere
apposta il codice e controllare che un test se ne accorga. Per esempio, togliendo
dal worker la regola «non ingrandire mai»:

```
assert (1200, 900) == (200, 150)
FAILED test_images.py::test_un_originale_piccolo_non_viene_ingrandito
```

Una foto da 200×150 era stata gonfiata a 1200×900, e il test lo dice con
esattezza.
