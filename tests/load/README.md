# Test di carico

```bash
deploy/cluster/load.sh                          # 200 immagini in un minuto: la specifica
deploy/cluster/load.sh oltre                    # 600 al minuto: abbastanza da far scattare l'autoscaler
FIXED_REPLICAS=1 deploy/cluster/load.sh oltre   # lo stesso carico con l'autoscaler fermo
```

Serve `k6` (`brew install k6`) e il cluster acceso con l'applicazione
installata.

## Cosa misura, e chi misura cosa

Il carico lo genera **k6** (`carico.js`): ogni iterazione fa i tre passi veri di
un caricamento — registra l'immagine, spedisce i byte all'object storage con il
link firmato, conferma — e una su dieci resta ad aspettare che l'elaborazione
finisca.

Ma k6 sa solo quanto ci mette una richiesta. **Quanti messaggi aspettano in
coda e quanti worker ci sono dall'altra parte non li può vedere**, e sono
esattamente i due numeri che la specifica chiede. Li campiona `load.sh` dal
cluster mentre k6 gira, e stampa le due viste accanto.

| Misura | Chi la prende | Cosa dice |
|---|---|---|
| `presa_in_carico` | k6 | quanto aspetta l'utente davanti allo schermo |
| `elaborazione_completa` | k6 | da quando carica a quando l'immagine è pronta |
| coda e repliche | `load.sh` | come reagisce il sistema dall'altra parte |
| durata del lotto | `load.sh`, dal database | l'unica misura confrontabile fra due esecuzioni |

## Il risultato principale: al carico della specifica, un worker basta

200 immagini al minuto, cioè quanto chiede `docs/Progetto.md`:

```
tempo    in coda    repliche   pronte
0s       1          1          1
23s      1          1          1
44s      2          1          1
62s      0          1          1
```

| | |
|---|---|
| Immagini | 201, **zero fallite** |
| Presa in carico | mediana **36 ms**, p95 69 ms |
| Elaborazione completa | mediana **2,05 s** |
| Repliche del worker | sempre 1 |

**La coda non supera mai 2 messaggi e l'autoscaler non scatta.** Non è un
difetto: significa che un solo worker consuma più in fretta di quanto quel
carico produca. È il risultato giusto, e va letto così — il sistema sta
comodamente sopra il carico richiesto.

Per vedere l'autoscaling all'opera bisogna chiedere di più.

## Il confronto: 600 al minuto, con e senza autoscaler

| | 1 worker fisso | autoscaling fino a 3 |
|---|---|---|
| Durata del lotto | **154,0 s** | **113,4 s** |
| Immagini al secondo | 3,8 | **5,0** |
| Coda massima | 391 | 391 |
| Immagini fallite | 0 | 0 |

Un miglioramento di **1,36 volte**. È meno del triplo, e meno anche
dell'1,8 misurato da `burst.sh`: le ragioni sono due, e nessuna è un guasto.

1. **I nodi hanno due processori ciascuno**, condivisi con PostgreSQL, MongoDB,
   MinIO e il broker. Si aggiungono worker, non processori.
2. **Qui il generatore di carico compete con il sistema.** k6 manda 600
   caricamenti al minuto attraverso lo stesso ingresso e interroga lo stato da
   150 VU: parte della capacità se ne va a servire il test. `burst.sh` carica
   i file *prima* di far partire il cronometro, quindi misura il worker con
   meno rumore intorno.

## Due attrezzi, e perché non uno solo

| | Cosa risponde |
|---|---|
| `deploy/cluster/load.sh` (k6) | «il sistema regge il carico previsto, e cosa vede chi lo usa?» |
| `deploy/cluster/burst.sh` | «quanto aiuta l'autoscaler, con meno interferenze possibili?» |

Il secondo carica tutti i file prima e poi consegna i job in blocco: la coda
nasce istantanea e si misura la reazione dell'autoscaler quasi senza rumore. Il
primo è il test di carico vero e proprio — traffico realistico, dal punto di
vista del client.

La differenza fra i due rapporti (1,8× contro 1,36×) non è una contraddizione:
è la misura di quanto pesa il generatore di carico su un cluster di tre
macchine virtuali che girano sullo stesso portatile.

## Una trappola del metodo, da non ripetere

Le latenze che stampa k6 (`elaborazione_completa`) **non si possono confrontare
fra due esecuzioni diverse**. Dipendono da quanto era lunga la coda nel momento
in cui quelle immagini sono state spedite, e la scelta di quali campionare cade
in istanti diversi a ogni giro.

Al primo tentativo il confronto diceva che **un worker era più veloce di tre**
(31 s contro 45 s di mediana), che è ovviamente falso: i campioni del giro con
un worker erano stati presi in gran parte all'inizio, con la coda ancora corta.

Da qui la misura presa dal database: dal momento in cui la prima immagine è
stata consegnata a quando l'ultima è stata completata. Quella si confronta.

## Dopo un test di carico, buttare i dati

Seicento immagini sono circa 220 MB fra originali e varianti. Dopo alcune
esecuzioni un nodo è arrivato al **84%** del suo disco, e la soglia oltre cui
Kubernetes comincia a sfrattare i pod è l'85%.

Non esiste una pulizia automatica per i dati *completati* — il CronJob
`cleanup` toglie solo le registrazioni abbandonate. Il modo rapido è buttare
via tutto e reinstallare, che su questo cluster dura tre minuti:

```bash
cd deploy/terraform/local && terraform destroy -auto-approve && terraform apply -auto-approve
```
