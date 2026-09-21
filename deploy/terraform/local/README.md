# Terraform — l'applicazione sul cluster locale

```bash
cd deploy/terraform/local
terraform init      # una volta sola: scarica i provider e scrive il lock
terraform apply
```

Prerequisito: il cluster deve esistere e le sue credenziali devono essere sul
Mac. Le mette lì Ansible alla fine di `site.yml`, in
`~/.kube/media-platform.conf`.

## Cosa fa, e cosa non fa

Terraform qui **non crea infrastruttura**: quando parte, il cluster esiste già
e l'ha costruito Ansible. Il suo compito è dichiarare cosa ci deve stare sopra.

> Ansible tocca le macchine, Terraform tocca il cluster.

Due risorse in tutto:

| Risorsa | Perché |
|---|---|
| `kubernetes_namespace_v1.app` | il recinto dell'applicazione |
| `helm_release.media_platform` | il chart, con i suoi valori |

Il namespace è creato qui e non da Helm (`create_namespace`) perché così
Terraform sa di possederlo: un `terraform destroy` lo porta via insieme a tutto
il resto, mentre un namespace creato da Helm resterebbe lì, vuoto, a ricordare
che qualcosa non è stato pulito.

In Fase 7 questa cartella avrà una sorella, `deploy/terraform/aws/`, dove
Terraform fa anche l'altro mestiere — creare VPC, EKS, RDS — che in locale non
avrebbe senso.

## La prova

```bash
terraform plan -detailed-exitcode
```

Deve dire **«No changes»** e uscire con codice `0`. È la stessa proprietà che
Ansible dimostra con `changed=0`: la configurazione descrive uno stato, e
quando la realtà già corrisponde non c'è niente da fare.

Misurato su questo cluster: installazione completa da zero in **1 minuto e 10
secondi**, giro completo superato subito dopo.

## Una cosa da sapere sui segreti

**Lo stato di Terraform contiene, in chiaro, tutto ciò che Terraform gestisce.**

È facile credere il contrario, quindi vale la pena verificarlo invece di
fidarsi. Il chart riceve `deploy/helm/values.local.yaml`, e il contenuto di
quel file — le cinque password comprese — finisce dentro `terraform.tfstate`:

```
postgresPassword     compare nello stato 2 volte
mongoPassword        compare nello stato 2 volte
rabbitmqPassword     compare nello stato 2 volte
minioRootPassword    compare nello stato 2 volte
jwtSecret            compare nello stato 2 volte
```

Marcare una variabile `sensitive` non cambierebbe niente: nasconde il valore
nell'uscita del comando, non nel file.

Le difese vere sono tre, e due ci sono già:

| | |
|---|---|
| Lo stato è escluso da git | `.gitignore` esclude `*.tfstate` |
| Il file va tenuto privato | Terraform lo scrive con i permessi predefiniti (`644`): su una macchina condivisa va stretto a mano, o si lavora con `umask 077` |
| Lo stato remoto e cifrato | Fase 7: S3 con il blocco su DynamoDB, come prevede il documento di progetto |

## I file

| File | Contenuto |
|---|---|
| `versions.tf` | le versioni dei provider, bloccate |
| `variables.tf` | i valori modificabili, tutti con un predefinito |
| `main.tf` | i provider e le due risorse |
| `outputs.tf` | l'indirizzo e i comandi per verificare |
| `.terraform.lock.hcl` | le impronte esatte dei provider — **va committato** |

Nessuna variabile è obbligatoria: i predefiniti descrivono questo cluster. Per
cambiarne una al volo:

```bash
terraform apply -var 'ingress_host=altro-nome.test'
```

## Due dettagli di sintassi che costano tempo

**`metadata` è un blocco, non un attributo.** Il provider `kubernetes` usa
ancora la forma a blocco, mentre quello di Helm è passato agli attributi
(`kubernetes = { ... }`). È il motivo per cui nel codice si legge
`metadata[0].name`: un blocco può comparire più volte, quindi si indicizza.

**Il suffisso `_v1`.** Il provider nomina le risorse con la versione dell'API
Kubernetes che usano, e la forma senza suffisso (`kubernetes_namespace`) è
deprecata.
