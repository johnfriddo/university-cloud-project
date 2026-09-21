# media-common

Pacchetto Python condiviso dai servizi. Contiene i due adapter verso
l'infrastruttura esterna:

| Classe | Copre |
|---|---|
| `ObjectStorage` | MinIO in locale, Amazon S3 nel cloud |
| `JobQueue` | RabbitMQ in locale, Amazon MQ nel cloud |

Nessun servizio importa `boto3` o `pika` direttamente: passano tutti da qui.
È il motivo per cui la migrazione della Fase 7 sarà un cambio di endpoint e di
credenziali invece di una riscrittura.

## Perché è un pacchetto e non codice copiato

`api-service` e `worker-service` parlano con lo stesso object storage e con lo
stesso broker. Duplicare gli adapter significherebbe correggere ogni bug due
volte, e soprattutto rischiare che le due copie divergano proprio nel punto in
cui devono restare identiche: i nomi di code ed exchange, e il modo di firmare
gli URL.

## Confini

Gli adapter ricevono **valori semplici**, non l'oggetto di configurazione di un
servizio: il pacchetto è condiviso e non deve sapere come ciascun servizio
organizza le proprie impostazioni.

Qui dentro non finisce l'accesso a PostgreSQL: le query sono specifiche di ogni
servizio e non c'è niente di comune da condividere se non la libreria, che ogni
servizio dichiara per conto suo.

## Installazione

Non si installa a mano: i `Dockerfile` dei servizi lo copiano e lo installano
prima del codice applicativo.

```dockerfile
COPY common/ /app/common/
RUN pip install --no-cache-dir /app/common
```

Per questo il contesto di build dei servizi è `services/` e non la cartella del
singolo servizio.
