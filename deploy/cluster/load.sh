#!/usr/bin/env bash
#
# Prova di carico con k6, più i due numeri che k6 non può vedere.
#
#   deploy/cluster/load.sh                  200 immagini in un minuto (la specifica)
#   deploy/cluster/load.sh oltre            600 in un minuto: abbastanza da far scattare l'autoscaler
#   FIXED_REPLICAS=1 deploy/cluster/load.sh oltre   lo stesso carico con l'autoscaler fermo
#
# La terza forma è ciò che dà un senso alla seconda: lo stesso carico gestito da
# un numero fisso di worker, così «l'autoscaler aiuta» diventa un numero invece
# di un'opinione.
#
# k6 misura quello che vede un client: quanto dura una richiesta, quanto ci mette
# un'immagine a essere pronta. Non può vedere quanti messaggi aspettano in coda
# né quanti worker esistono — e sono i due numeri che chiede la specifica.
# Questo script li campiona dal cluster mentre k6 gira, e stampa le due viste
# una accanto all'altra.
#
set -euo pipefail

PROFILE="${1:-specifica}"
BASE="${BASE:-http://media-platform.test}"
NAMESPACE="media-platform"
JOB_QUEUE="media.process"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SCRIPT="$REPO/tests/load/carico.js"

WORK="$(mktemp -d)"
IMAGE="$WORK/carico.jpg"

kube() { "$HERE/cluster.sh" kubectl -n "$NAMESPACE" "$@"; }
step() { printf '\n==> %s\n' "$*"; }

# Qualunque cosa succeda, l'autoscaler si riprende il proprio mestiere.
restore() {
  kube annotate scaledobject worker-service autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap restore EXIT

case "$PROFILE" in
  specifica)
    # Il carico che chiede docs/Progetto.md: 200 immagini in un minuto.
    RATE=200; DURATION=1m ;;
  oltre)
    # Tre volte tanto. Serve perché un worker solo regge il carico della
    # specifica senza affanno: a 200 al minuto la coda non si forma e
    # l'autoscaler non ha niente a cui reagire. Vedi docs/registro-cluster.md.
    RATE=600; DURATION=1m ;;
  *)
    sed -n '3,11p' "$0" >&2; exit 2 ;;
esac

command -v k6 >/dev/null || { echo "serve k6: brew install k6" >&2; exit 1; }

step "Preparo l'immagine di prova"
sips -s format jpeg "$REPO/docs/architettura_locale.png" --out "$IMAGE" >/dev/null
echo "   $(stat -f%z "$IMAGE") byte"

if [ -n "${FIXED_REPLICAS:-}" ]; then
  step "Fermo l'autoscaler a ${FIXED_REPLICAS} replica/e"
  # KEDA capisce un'annotazione che significa «smetti di decidere, tieni
  # questo numero». È il modo onesto di fare il confronto: il ScaledObject
  # resta al suo posto, quindi fra le due prove non cambia nient'altro.
  kube annotate scaledobject worker-service \
    "autoscaling.keda.sh/paused-replicas=${FIXED_REPLICAS}" --overwrite >/dev/null
  until [ "$(kube get deployment worker-service -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "$FIXED_REPLICAS" ]; do
    sleep 3
  done
fi

step "Carico: ${RATE} immagini al minuto per ${DURATION}"

# L'utente lo decide questo script e non k6, perché serve anche dopo: è la
# chiave con cui si chiede al database quanto è durato il lotto.
EMAIL="k6-$(date +%s)-$RANDOM@example.com"

# k6 in sottofondo, il campionamento in primo piano: le due misure devono
# essere prese nello stesso momento per poterle confrontare.
k6 run --quiet --summary-mode full \
  -e "BASE=$BASE" -e "IMAGE=$IMAGE" -e "EMAIL=$EMAIL" \
  -e "RATE=$RATE" -e "DURATION=$DURATION" \
  "$SCRIPT" > "$WORK/k6.txt" 2>&1 &
K6=$!

printf '\n   %-8s %-10s %-10s %s\n' tempo 'in coda' 'repliche' pronte
START=$(date +%s)
SEEN_MAX=1
while kill -0 "$K6" 2>/dev/null; do
  now=$(( $(date +%s) - START ))

  queue=$(kube exec rabbitmq-0 -- rabbitmqctl list_queues --quiet name messages 2>/dev/null \
    | awk -v q="$JOB_QUEUE" '$1 == q {print $2}')
  : "${queue:=0}"

  pair=$(kube get deployment worker-service \
    -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null || echo "1 0")
  wanted="${pair%% *}"; ready="${pair##* }"
  : "${wanted:=1}" "${ready:=0}"
  [ "$wanted" -gt "$SEEN_MAX" ] && SEEN_MAX="$wanted"

  printf '   %-8s %-10s %-10s %s\n' "${now}s" "$queue" "$wanted" "$ready"
  sleep 3
done

wait "$K6" && ESITO="superato" || ESITO="soglie non rispettate"

step "Quello che ha visto il client (k6)"
grep -E "presa_in_carico|elaborazione_completa|immagini_|http_req_failed|checks_total|iterations" \
  "$WORK/k6.txt" | sed 's/^/   /' || cat "$WORK/k6.txt"

step "Quello che ha visto il cluster"
echo "   repliche massime raggiunte: $SEEN_MAX su $(kube get scaledobject worker-service -o jsonpath='{.spec.maxReplicaCount}')"
echo "   esito delle soglie: $ESITO"

# Le immagini restano in coda anche dopo che k6 ha finito: la misura del lotto
# si prende quando l'ultima è pronta, non quando l'ultima è stata spedita.
step "Aspetto che la coda si svuoti"
until [ "$(kube exec rabbitmq-0 -- rabbitmqctl list_queues --quiet name messages 2>/dev/null \
           | awk -v q="$JOB_QUEUE" '$1 == q {print $2}')" = "0" ]; do
  sleep 5
done
echo "   coda vuota"

# La misura che si può confrontare fra due esecuzioni: dal momento in cui la
# prima immagine è stata consegnata a quando l'ultima è stata completata.
#
# Le latenze che stampa k6 **non** si possono confrontare: dipendono da quanto
# era lunga la coda quando quelle immagini sono state spedite, e la scelta di
# quali campionare cade in momenti diversi a ogni giro.
step "Il lotto, visto dal database"
kube exec postgres-0 -- psql -U media -d media -t -A -F' ' -c "
  select count(*),
         round(extract(epoch from max(a.updated_at) - min(a.submitted_at))::numeric, 1)
  from assets a join users u on u.id = a.user_id
  where u.email = '$EMAIL' and a.status = 'DONE'" \
  | awk '{ printf "   %s immagini elaborate in %s secondi — %.1f al secondo\n", $1, $2, $1/$2 }'
