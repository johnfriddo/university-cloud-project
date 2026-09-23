# Il deployment su AWS

Stessa applicazione del cluster locale, stessa chart Helm. Cambia tutto ciò che
sta sotto: l'infrastruttura non è più fatta da noi, è affittata.

| Locale | AWS |
|---|---|
| 3 VM Multipass con kubeadm | EKS in Auto Mode, nodi nelle subnet private |
| Ingress NGINX sulla porta del nodo | Application Load Balancer |
| PostgreSQL in uno StatefulSet | Amazon RDS, con backup automatici |
| MongoDB in uno StatefulSet | DynamoDB |
| RabbitMQ in uno StatefulSet | Amazon MQ, AMQP su TLS |
| MinIO in uno StatefulSet | Amazon S3 |
| Immagini importate a mano nei nodi | ECR |
| Secret di Kubernetes | Secrets Manager, e nessuna chiave per S3: IRSA |
| Avvisi scritti nel registro | SNS |

La differenza che conta più di tutte non è in tabella: **in locale i dati
stavano dentro il cluster, qui stanno accanto**. Si può distruggere l'intero
cluster e ritrovare utenti e immagini al loro posto.

## Le due parti, e perché sono due

```
deploy/terraform/aws/
├── tf.sh          il comando: crea il bucket dello stato e lancia Terraform
├── images.sh      costruisce le immagini per ARM e le carica su ECR
├── infra/         rete, cluster, database, coda, archivio, ruoli
└── app/           quello che gira dentro il cluster: KEDA e il chart
```

`infra` e `app` hanno vite diverse. L'applicazione si reinstalla dieci volte al
giorno; la rete e il database no. E una sola configurazione che crea il cluster
e insieme gli parla dovrebbe configurare il provider Kubernetes verso un
cluster che, al momento del piano, non esiste ancora: è una trappola nota.

Lo stato **non sta su questo disco**: sta in un bucket S3 cifrato e con le
versioni attive. Contiene le password in chiaro — verificato in Fase 6 — e
perderlo significherebbe perdere il filo di cosa esiste.

## Accendere tutto

```bash
# una sola volta: il bucket dello stato
deploy/terraform/aws/tf.sh bootstrap

# l'infrastruttura (~15 minuti: EKS e Amazon MQ sono i lenti)
deploy/terraform/aws/tf.sh infra apply

# le immagini su ECR; stampa l'etichetta da usare
TAG=$(deploy/terraform/aws/images.sh)

# l'applicazione
deploy/terraform/aws/tf.sh app apply -var image_tag="$TAG"

# l'indirizzo a cui rispondere
deploy/terraform/aws/tf.sh app output -raw app_url
```

Per usare `kubectl`:

```bash
aws eks update-kubeconfig --name media-platform --region eu-central-1
```

## Spegnere tutto

**Da fare ogni volta che non serve**: acceso costa circa 0,47 USD l'ora.

```bash
deploy/terraform/aws/tf.sh app destroy
deploy/terraform/aws/tf.sh infra destroy
```

Nell'ordine: `app` prima, perché l'ALB è creato dall'Ingress e va tolto prima
della rete che lo ospita.

Il bucket dello stato resta: è minuscolo, e serve al prossimo giro.

## Quanto costa

Prezzi di Francoforte, letti dall'API dei prezzi di AWS a settembre 2026.

| Voce | USD/ora |
|---|---|
| Amazon MQ, `mq.m7g.medium` singolo | 0,164 |
| Piano di controllo EKS | 0,100 |
| 1 nodo `c7i-flex.large` | 0,097 |
| NAT gateway | 0,052 |
| ALB | 0,027 |
| RDS `db.t4g.micro` | 0,019 |
| Auto Mode (gestione dei nodi) | ~0,005 |
| Indirizzi IPv4 pubblici | 0,015 |
| **Totale** | **~0,48** |

DynamoDB, S3, SNS, ECR e Secrets Manager, a questi volumi, sono centesimi.

La voce più cara è il broker, e non per caso: è l'unico componente che si paga
a ora anche quando non fa niente. Con SQS sparirebbe dalla tabella — al prezzo
di riscrivere l'adattatore della coda e le sue garanzie, che sul deployment
locale sono già misurate. La scelta è argomentata nel capitolo 10 della
relazione.

## Distribuire da GitHub Actions

Il workflow `.github/workflows/distribuzione.yml` fa gli stessi due passi a
mano: costruisce e carica le immagini, poi lancia `tf.sh app apply`. Si avvia a
mano dall'interfaccia di GitHub, non a ogni push: l'ambiente AWS resta acceso
solo quando serve, e un deploy automatico su un cluster spento fallirebbe ogni
volta.

**Nessuna chiave nel repository.** GitHub firma un token per ogni esecuzione,
AWS lo verifica e restituisce credenziali temporanee — lo stesso principio di
IRSA, un livello più su. Il ruolo accetta solo questo repository e solo il ramo
`main`.

Per attivarlo servono due **variabili** (non segreti) nel repository, sotto
*Settings → Secrets and variables → Actions → Variables*:

| Nome | Valore |
|---|---|
| `AWS_ROLE_ARN` | l'uscita `github_role_arn` di `tf.sh infra output` |
| `AWS_REGION` | `eu-central-1` |

Il ruolo si crea solo se `github_repository` è valorizzato in
`infra/locale.auto.tfvars`, che git ignora perché contiene anche l'indirizzo
email delle notifiche.

Un dettaglio che si nota leggendo il workflow: lì le immagini si costruiscono
**senza emulazione**, perché gli esecutori di GitHub sono già x86 come i nodi.
Sul Mac la stessa operazione passa per l'emulazione ed è parecchio più lenta.

## Le cose che qui sono diverse, e il perché

**Nodi Intel, e le immagini costruite per x86 su un Mac ARM.** Non è la scelta
che si farebbe a mente fredda: era l'unica possibile, e l'abbiamo scoperta con
un'installazione fallita. Due regole si incastrano male:

- l'account è sul **piano gratuito**, che rifiuta qualunque tipo di macchina
  non compreso nel free tier — Graviton `t4g.medium` incluso;
- **EKS Auto Mode** rifiuta qualunque taglia sotto `large`, quindi le taglie
  piccole del free tier (`t3.micro`, `t4g.small`) sono fuori anche loro.

Quello che sopravvive a entrambe è una famiglia sola: le `*-flex.large`, che
sono Intel. Da qui la costruzione delle immagini per x86, che sul Mac passa per
l'emulazione ed è lenta.

Il `NodePool` ha anche un tetto di 8 CPU, che è il freno alla bolletta: oltre
quello AWS non accende macchine, qualunque cosa chieda l'autoscaler.

**Nessuna chiave per S3, DynamoDB e SNS.** I pod assumono un ruolo IAM (IRSA) e
ricevono credenziali temporanee. Ogni servizio ha il suo: il worker può leggere
gli originali e scrivere le varianti, non cancellare; il notification-service
può solo pubblicare su SNS.

**Il permesso CORS sul bucket.** In locale tutto rispondeva su un nome solo,
quindi per il browser la pagina e l'archivio erano la stessa origine. Qui la
pagina sta sull'ALB e il file va su S3: due indirizzi diversi, e il browser non
invia se S3 non dichiara che quell'indirizzo può. Lo scrive `app`, perché
l'indirizzo dell'ALB esiste solo dopo che l'Ingress l'ha creato.

**Niente HTTPS e niente limite di frequenza.** Il primo richiede un dominio, il
secondo su AWS sarebbe mestiere di WAF: nessuno dei due è nello stack
concordato. Sono dichiarati fra i limiti, in appendice B della relazione.
