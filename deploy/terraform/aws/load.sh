#!/usr/bin/env bash
#
# Prova di carico su AWS, con i tre numeri che k6 non può vedere.
#
#   deploy/terraform/aws/load.sh                  200 immagini in un minuto (la specifica)
#   deploy/terraform/aws/load.sh oltre            600 in un minuto: abbastanza da far scattare l'autoscaler
#   FIXED_REPLICAS=1 deploy/terraform/aws/load.sh oltre   lo stesso carico, autoscaler fermo
#
# La stessa misura di quella locale (deploy/cluster/load.sh), con tre
# differenze pratiche:
#
# - la coda è Amazon MQ, in una subnet privata: la si interroga da dentro il
# cluster, attraverso l'API di gestione, con le credenziali che hanno i pod;
# - il database è RDS, anch'esso privato, e l'immagine porta psycopg ma non
# psql, quindi la durata del lotto si legge con una riga di Python in un pod;
# - c'è una colonna **nodi**. Sul portatile il numero di nodi era fisso a due
# e potevano crescere solo i pod, che è il tetto misurato dal capitolo 9 della
# relazione. Qui Karpenter può aggiungere macchine, quindi vale la pena
# guardare se lo fa.
#
set -euo pipefail

PROFILE="${1:-specifica}"
NAMESPACE="media-platform"
JOB_QUEUE="media.process"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SCRIPT="$REPO/tests/load/carico.js"

BASE="${BASE:-$(terraform -chdir="$HERE/app" output -raw app_url 2>/dev/null)}"
[ -n "$BASE" ] || { echo "non so a quale indirizzo rivolgermi: passa BASE=..." >&2; exit 2; }

WORK="$(mktemp -d)"
IMAGE="$WORK/carico.jpg"

kube() { kubectl -n "$NAMESPACE" "$@"; }
step() { printf '\n==> %s\n' "$*"; }

restore() {
  kube annotate scaledobject worker-service autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap restore EXIT

# Quanti messaggi aspettano, chiesto da dentro il cluster. Il broker non ha
# nessun indirizzo pubblico, e il pod ha già le credenziali nel proprio
# ambiente: da qui non c'è niente da passargli.
queue_length() {
  kube exec deploy/api-service -- python -c "
import base64, json, os, urllib.request, urllib.parse
from app.dlq_watch import management_base
url = os.environ['RABBITMQ_URL']
parts = urllib.parse.urlparse(url)
request = urllib.request.Request(management_base(url) + '/queues/%2F/${JOB_QUEUE}')
token = base64.b64encode(f'{parts.username}:{parts.password}'.encode()).decode()
request.add_header('Authorization', 'Basic ' + token)
try:
    with urllib.request.urlopen(request, timeout=10) as answer:
        print(json.loads(answer.read()).get('messages', 0))
except Exception:
    print(0)
" 2>/dev/null || echo 0
}

case "$PROFILE" in
  specifica) RATE=200; DURATION=1m ;;
  oltre)     RATE=600; DURATION=1m ;;
  *) sed -n '3,9p' "$0" >&2; exit 2 ;;
esac

command -v k6 >/dev/null || { echo "serve k6: brew install k6" >&2; exit 1; }

step "Preparo l'immagine di prova"
sips -s format jpeg "$REPO/docs/architettura_locale.png" --out "$IMAGE" >/dev/null
echo "   $(stat -f%z "$IMAGE") byte"

if [ -n "${FIXED_REPLICAS:-}" ]; then
  step "Fermo l'autoscaler a ${FIXED_REPLICAS} replica/e"
  kube annotate scaledobject worker-service \
    "autoscaling.keda.sh/paused-replicas=${FIXED_REPLICAS}" --overwrite >/dev/null
  until [ "$(kube get deployment worker-service -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "$FIXED_REPLICAS" ]; do
    sleep 3
  done
fi

step "Carico su AWS: ${RATE} immagini al minuto per ${DURATION}"
echo "   $BASE"

EMAIL="k6-$(date +%s)-$RANDOM@example.com"

k6 run --quiet --summary-mode full \
  -e "BASE=$BASE" -e "IMAGE=$IMAGE" -e "EMAIL=$EMAIL" \
  -e "RATE=$RATE" -e "DURATION=$DURATION" \
  "$SCRIPT" > "$WORK/k6.txt" 2>&1 &
K6=$!

printf '\n   %-8s %-10s %-10s %-8s %s\n' tempo 'in coda' 'repliche' pronte nodi
START=$(date +%s)
SEEN_MAX=1
SEEN_NODES=1
while kill -0 "$K6" 2>/dev/null; do
  now=$(( $(date +%s) - START ))

  queue=$(queue_length)
  : "${queue:=0}"

  pair=$(kube get deployment worker-service \
    -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null || echo "1 0")
  wanted="${pair%% *}"; ready="${pair##* }"
  : "${wanted:=1}" "${ready:=0}"
  [ "$wanted" -gt "$SEEN_MAX" ] && SEEN_MAX="$wanted"

  nodes=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
  [ "${nodes:-1}" -gt "$SEEN_NODES" ] && SEEN_NODES="$nodes"

  printf '   %-8s %-10s %-10s %-8s %s\n' "${now}s" "$queue" "$wanted" "$ready" "$nodes"
  sleep 5
done

wait "$K6" && ESITO="superato" || ESITO="soglie non rispettate"

step "Quello che ha visto il client (k6)"
grep -E "presa_in_carico|elaborazione_completa|immagini_|http_req_failed|checks_total|iterations" \
  "$WORK/k6.txt" | sed 's/^/   /' || cat "$WORK/k6.txt"

step "Quello che ha visto il cluster"
echo "   repliche massime: $SEEN_MAX su $(kube get scaledobject worker-service -o jsonpath='{.spec.maxReplicaCount}')"
echo "   nodi massimi: $SEEN_NODES"
echo "   esito delle soglie: $ESITO"

step "Aspetto che la coda si svuoti"
until [ "$(queue_length)" = "0" ]; do sleep 5; done
echo "   coda vuota"

# La misura confrontabile: dalla prima immagine consegnata all'ultima
# completata, letta dal database. Le latenze che stampa k6 non si possono
# confrontare fra due esecuzioni — vedi il capitolo 9 della relazione.
step "Il lotto, visto dal database"
kube exec deploy/api-service -- python -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    row = conn.execute('''
        select count(*),
               round(extract(epoch from max(a.updated_at) - min(a.submitted_at))::numeric, 1)
          from assets a join users u on u.id = a.user_id
         where u.email = %s and a.status = 'DONE'
    ''', ('$EMAIL',)).fetchone()
    print(row[0], row[1])
" | awk '{ if ($2 > 0) printf "   %s immagini elaborate in %s secondi — %.1f al secondo\n", $1, $2, $1/$2 }'
