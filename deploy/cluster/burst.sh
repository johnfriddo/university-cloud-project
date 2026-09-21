#!/usr/bin/env bash
#
# Manda una raffica di immagini al cluster e guarda il numero di worker.
#
#   deploy/cluster/burst.sh [quante]                 predefinito: 300 immagini
#   FIXED_REPLICAS=1 deploy/cluster/burst.sh [quante]  con l'autoscaler fermo
#
# La seconda forma è ciò che dà un senso alla prima: la stessa raffica gestita
# da un numero fisso di worker, così «l'autoscaler aiuta» diventa un numero
# invece di un'opinione. Mette in pausa il ScaledObject per la durata
# dell'esecuzione e lo libera alla fine.
#
# È il carico attorno a cui l'architettura è stata pensata: qualcuno carica
# duecento foto in una volta e poi sparisce per ore. È anche l'unico modo di
# vedere l'autoscaler fare qualcosa — una coda che non cresce mai non chiede
# nessun secondo worker.
#
# Due fasi di proposito. Prima ogni immagine viene registrata e spinta
# sull'object storage; solo dopo tutti i job vengono pubblicati, insieme.
# Caricare e confermare un'immagine alla volta spalmerebbe la pubblicazione
# sullo stesso tempo che il worker impiega a consumarla, e la coda non si
# formerebbe mai: misurato, al primo tentativo, quaranta immagini e la coda sempre a zero.
#
# Nemmeno il numero predefinito è arbitrario. Un worker è stato misurato a 5,6
# immagini al secondo, e all'autoscaler servono quindici o venti secondi per
# accorgersene e reagire: con un minimo di una replica la decisione è
# interamente dell'HorizontalPodAutoscaler, che chiede a KEDA la lunghezza della
# coda ogni quindici secondi e poi deve far partire i pod. Una raffica più corta
# di quella reazione finisce prima che qualcuno la veda — sessanta immagini
# smaltite in undici secondi senza che il numero di repliche si muovesse.
# Trecento tengono un worker occupato per circa un minuto: abbastanza per essere viste e per ricevere risposta.
#
set -euo pipefail

COUNT="${1:-300}"
PARALLEL=8
BASE="${BASE:-http://media-platform.test}"
NAMESPACE="media-platform"
# La coda su cui si pubblicano i job, con il nome che ha nei valori del chart.
JOB_QUEUE="media.process"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
IMAGE="$WORK/prova.jpg"
IDS="$WORK/ids.txt"

kube() { "$HERE/cluster.sh" kubectl -n "$NAMESPACE" "$@"; }
step() { printf '\n==> %s\n' "$*"; }

# KEDA capisce un'annotazione che significa «smetti di decidere, tieni questo numero».
# È il modo onesto di fare il confronto: il ScaledObject resta al suo posto,
# quindi fra le due esecuzioni non cambia nient'altro del sistema.
if [ -n "${FIXED_REPLICAS:-}" ]; then
  step "Fermo l'autoscaler a ${FIXED_REPLICAS} replica/e"
  kube annotate scaledobject worker-service \
    "autoscaling.keda.sh/paused-replicas=${FIXED_REPLICAS}" --overwrite >/dev/null
  trap 'kube annotate scaledobject worker-service autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
  # Aspetta che il deployment sia davvero a quel numero prima di caricarlo.
  until [ "$(kube get deployment worker-service -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "$FIXED_REPLICAS" ]; do
    sleep 3
  done
fi

step "Preparo l'immagine e l'utente"
sips -s format jpeg "$REPO/docs/architettura_locale.png" --out "$IMAGE" >/dev/null
SIZE=$(stat -f%z "$IMAGE")

EMAIL="burst-$(date +%s)@example.com"
PASSWORD="password-lunga-di-prova"
curl -sS -o /dev/null -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
TOKEN=$(curl -sS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
echo "   $COUNT immagini da $SIZE byte, $PARALLEL alla volta"

# Fase uno, un'immagine: la registra e ne spinge i byte. Qui non si pubblica
# nessun job, quindi la coda resta vuota per quanto tempo ci voglia.
cat > "$WORK/prepare.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
n="$1"
resp=$(curl -sS -X POST "$BASE/api/assets" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"filename\":\"burst-$n.jpg\",\"mime\":\"image/jpeg\",\"size_bytes\":$SIZE}")
id=$(echo "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["asset_id"])')
url=$(echo "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["upload"]["url"])')
curl -sS -o /dev/null -X PUT --upload-file "$IMAGE" -H 'Content-Type: image/jpeg' "$url"
echo "$id" >> "$IDS"
SCRIPT

# Fase due, un'immagine: dice che il file c'è. È la richiesta che pubblica
# il job, e si esaurisce in millisecondi.
cat > "$WORK/submit.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
curl -sS -o /dev/null -X POST "$BASE/api/assets/$1/complete" -H "Authorization: Bearer $TOKEN"
SCRIPT

chmod +x "$WORK/prepare.sh" "$WORK/submit.sh"
export BASE TOKEN SIZE IMAGE IDS

step "Fase 1: carico i file (la coda resta vuota)"
: > "$IDS"
start=$(date +%s)
seq 1 "$COUNT" | xargs -P "$PARALLEL" -n 1 "$WORK/prepare.sh"
echo "   $(wc -l < "$IDS" | tr -d ' ') file caricati in $(( $(date +%s) - start ))s"

step "Fase 2: consegno tutti i job insieme"
start=$(date +%s)
# Più parallelismo che nella fase uno: il punto è riempire la coda più in
# fretta di quanto un worker la svuoti.
xargs -P 16 -n 1 "$WORK/submit.sh" < "$IDS"
echo "   $COUNT job pubblicati in $(( $(date +%s) - start ))s"

step "Cosa succede"
# Due viste della stessa coda, di proposito. La prima è il conteggio del broker,
# che è la verità. La seconda è ciò che l'autoscaler crede in questo momento,
# cioè la verità di quindici o venti secondi fa — e quel divario è esattamente
# il motivo per cui una raffica breve resta senza risposta.
printf '   %-8s %-10s %-12s %-10s %s\n' tempo 'in coda' 'vista da KEDA' 'repliche' pronte
START=$(date +%s)
SEEN_MAX=1
QUIET=0
while :; do
  now=$(( $(date +%s) - START ))

  queue=$(kube exec rabbitmq-0 -- rabbitmqctl list_queues --quiet name messages 2>/dev/null \
    | awk -v q="$JOB_QUEUE" '$1 == q {print $2}')
  : "${queue:=0}"

  # L'HPA riporta la metrica divisa per il numero di repliche, quindi la si
  # rimoltiplica per confrontarla con il conteggio del broker.
  avg=$(kube get hpa keda-hpa-worker-service \
    -o jsonpath='{.status.currentMetrics[0].external.current.averageValue}' 2>/dev/null || echo 0)
  : "${avg:=0}"
  # Kubernetes scrive le quantità frazionarie in millesimi, con una «m»
  # suffix: 55334m means 55,334 messages per replica. Left as it is, the
  # l'aritmetica qui sotto la rifiuterebbe.
  case "$avg" in *m) avg=$(( ${avg%m} / 1000 )) ;; esac

  # Una chiamata sola per entrambi i numeri, divisa a mano: `read` farebbe
  # terminare lo script, perché jsonpath non stampa nessun a capo e un `read` che
  # incontra la fine dell'entrata restituisce un errore che `set -e` prende alla lettera.
  pair=$(kube get deployment worker-service \
    -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null || echo "1 0")
  wanted="${pair%% *}"; ready="${pair##* }"
  : "${wanted:=1}" "${ready:=0}"

  seen=$(( ${avg:-0} * wanted ))
  [ "$wanted" -gt "$SEEN_MAX" ] && SEEN_MAX="$wanted"
  printf '   %-8s %-10s %-12s %-10s %s\n' "${now}s" "$queue" "$seen" "$wanted" "$ready"

  # Si ferma quando la coda è vuota ed è tornata a un worker da un po'.
  if [ "$queue" = "0" ] && [ "$wanted" = "1" ] && [ "$now" -gt 60 ]; then
    QUIET=$(( QUIET + 1 ))
    [ "$QUIET" -ge 3 ] && break
  else
    QUIET=0
  fi
  [ "$now" -gt 900 ] && { echo "   troppo lungo, mi fermo"; break; }
  sleep 2
done

step "Riepilogo"
echo "   repliche massime raggiunte: $SEEN_MAX su $(kube get scaledobject worker-service -o jsonpath='{.spec.maxReplicaCount}')"
kube get pods -l app.kubernetes.io/component=worker-service --no-headers | sed 's/^/   /'

# Il numero che conta, preso dal database invece che dall'orologio di questo
# script: quanto è durato il lotto dal momento in cui è stato consegnato a
# quando l'ultima immagine è stata completata.
kube exec postgres-0 -- psql -U media -d media -t -A -F' ' -c "
  select count(*),
         round(extract(epoch from max(a.updated_at) - min(a.submitted_at))::numeric, 1)
  from assets a join users u on u.id = a.user_id
  where u.email = '$EMAIL' and a.status = 'DONE'" \
  | awk '{ printf "\n   %s immagini elaborate in %s secondi — %.1f al secondo\n", $1, $2, $1/$2 }'
