#!/usr/bin/env bash
#
# Guasti veri sul cluster: pod uccisi mentre il sistema sta lavorando.
#
#   deploy/cluster/faults.sh [quale]
#
#   worker     i lavori aspettano un worker spento, e nessuno si perde
#   broker     i consumatori aspettano il broker invece di crollare
#   ucciso     un worker ucciso a metà lavoro non perde né duplica niente
#   database   l'API esce di servizio senza essere riavviata, e rientra
#   archivio   un archivio dati riparte con i suoi dati
#   tutti      tutti e cinque, in fila (predefinito)
#
# La versione per compose di queste prove sta in tests/integration/test_guasti.py
# e comanda Docker. Qui non si può riusare: quello che si vuole verificare è
# precisamente ciò che Kubernetes fa **diversamente** — riavvia ciò che muore, e
# toglie dalla rotazione un pod che si dichiara non pronto invece di ucciderlo.
#
# Ogni volta si controllano due proprietà, perché sono quelle facili da credere
# e difficili da verificare: nessun lavoro si perde, e nessun lavoro viene fatto
# due volte. La seconda è il motivo per cui conta il numero di varianti — tre per immagine, mai sei.
# never six.
#
set -euo pipefail

WHICH="${1:-tutti}"
BASE="${BASE:-http://media-platform.test}"
NAMESPACE="media-platform"
PASSWORD="password-lunga-di-prova"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

WORK="$(mktemp -d)"
IMAGE="$WORK/prova.jpg"

kube() { "$HERE/cluster.sh" kubectl -n "$NAMESPACE" "$@"; }

FAILURES=0
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
ok()   { printf '   \033[32mOK\033[0m   %s\n' "$*"; }
ko()   { printf '   \033[31mKO\033[0m   %s\n' "$*"; FAILURES=$(( FAILURES + 1 )); }
check(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else ko "$1: atteso «$3», trovato «$2»"; fi; }

# Qualunque cosa succeda, all'autoscaler viene restituito il suo mestiere e il
# numero di worker viene ripristinato. Una prova che muore a metà non deve lasciare il cluster zoppo.
restore() {
  kube annotate scaledobject worker-service autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
  # Se la prova del database è morta a metà, il database è rimasto spento e
  # ogni cosa successiva fallirebbe per un motivo che non la riguarda.
  kube scale statefulset postgres --replicas=1 >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap restore EXIT

# --- l'applicazione, vista da fuori -----------------------------------------

prepare_user() {
  sips -s format jpeg "$REPO/docs/architettura_locale.png" --out "$IMAGE" >/dev/null
  SIZE=$(stat -f%z "$IMAGE")
  EMAIL="faults-$(date +%s)-$RANDOM@example.com"
  curl -sS -o /dev/null -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
  TOKEN=$(curl -sS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
}

# Carica un'immagine e ne pubblica il job. Stampa l'identificativo dell'immagine.
upload() {
  local name="$1" resp id url
  resp=$(curl -sS -X POST "$BASE/api/assets" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"filename\":\"$name\",\"mime\":\"image/jpeg\",\"size_bytes\":$SIZE}")
  id=$(echo "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["asset_id"])')
  url=$(echo "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["upload"]["url"])')
  curl -sS -o /dev/null -X PUT --upload-file "$IMAGE" -H 'Content-Type: image/jpeg' "$url"
  curl -sS -o /dev/null -X POST "$BASE/api/assets/$id/complete" -H "Authorization: Bearer $TOKEN"
  echo "$id"
}

status_of() {
  curl -sS "$BASE/api/assets/status?ids=$1" -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["items"][0]["status"])'
}

wait_for_done() {
  local id="$1" deadline=$(( $(date +%s) + ${2:-120} )) s
  while [ "$(date +%s)" -lt "$deadline" ]; do
    s=$(status_of "$id")
    [ "$s" = "DONE" ] && { echo DONE; return; }
    [ "$s" = "FAILED" ] && { echo FAILED; return; }
    sleep 2
  done
  echo TIMEOUT
}

# --- il cluster, visto da dentro --------------------------------------------

# La sonda di prontezza di un consumatore, letta come la legge Kubernetes. Passa
# dal pod dell'api-service perché è l'unico posto dentro il cluster con un
# interprete Python e nessun lavoro da interrompere.
readiness() {
  # Il percorso non è lo stesso per tutti: le rotte dell'api-service vivono
  # sotto /api, quelle dei due consumatori no.
  local host="$1" path="${2:-/health/ready}" pod
  pod=$(kube get pod -l app.kubernetes.io/component=api-service \
    -o jsonpath='{.items[0].metadata.name}')
  kube exec "$pod" -- python -c "
import json, sys, urllib.error, urllib.request
try:
    with urllib.request.urlopen('http://$host:8000$path', timeout=5) as r:
        print(r.status, json.dumps(json.loads(r.read())))
except urllib.error.HTTPError as e:
    print(e.code, json.dumps(json.loads(e.read())))
except Exception as e:
    print('000', json.dumps({'errore': str(e)}))
" 2>/dev/null
}

readiness_code() { readiness "$1" "${2:-/health/ready}" | awk "{print \$1}"; }

restarts_of() {
  kube get pod -l "app.kubernetes.io/component=$1" \
    -o jsonpath='{.items[*].status.containerStatuses[0].restartCount}' 2>/dev/null | tr ' ' '+' | bc
}

running_count() {
  kube get pod -l "app.kubernetes.io/component=$1" --no-headers 2>/dev/null \
    | grep -c Running || true
}

variant_count() {
  kube exec postgres-0 -- psql -U media -d media -t -A \
    -c "select count(*) from variants where asset_id = '$1'" 2>/dev/null | tr -d ' \r'
}

# Fissa il numero di worker mettendo in pausa l'autoscaler, e aspetta che accada.
pin_workers() {
  kube annotate scaledobject worker-service \
    "autoscaling.keda.sh/paused-replicas=$1" --overwrite >/dev/null
  local deadline=$(( $(date +%s) + 120 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    [ "$(kube get deployment worker-service -o jsonpath='{.spec.replicas}')" = "$1" ] && return
    sleep 2
  done
}

unpin_workers() {
  kube annotate scaledobject worker-service autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
}

# --- i guasti ---------------------------------------------------------------

fault_worker() {
  say "Con il worker spento, i lavori aspettano e nessuno si perde"
  prepare_user
  pin_workers 0
  note "worker a 0 repliche"

  local ids=()
  for n in 1 2 3; do ids+=("$(upload "in-attesa-$n.jpg")"); done
  sleep 5

  local pending=0
  for id in "${ids[@]}"; do [ "$(status_of "$id")" = "PENDING" ] && pending=$(( pending + 1 )); done
  check "le tre immagini restano in attesa" "$pending" 3
  note "sono nella coda del broker, che le conserva"

  unpin_workers
  note "worker riacceso"

  local done_count=0
  for id in "${ids[@]}"; do [ "$(wait_for_done "$id")" = "DONE" ] && done_count=$(( done_count + 1 )); done
  check "riprese e completate al ritorno del worker" "$done_count" 3
}

fault_broker() {
  say "Senza broker, i consumatori aspettano invece di crollare"
  local before_w before_n
  before_w=$(restarts_of worker-service)
  before_n=$(restarts_of notification-service)
  note "riavvii prima: worker $before_w, notifier $before_n"

  kube delete pod rabbitmq-0 --wait=false >/dev/null
  note "broker eliminato"
  sleep 20

  check "il worker è ancora in esecuzione" "$(running_count worker-service)" 1
  check "il notifier è ancora in esecuzione" "$(running_count notification-service)" 1
  check "il worker dichiara di non poter lavorare" "$(readiness_code worker-service)" 503
  check "il notifier dichiara di non poter lavorare" "$(readiness_code notification-service)" 503
  # Il punto dell'intera prova: non pronto non è la stessa cosa di morto. Una
  # sonda di vitalità che controllasse il broker li avrebbe già riavviati
  # entrambi, più volte, per un guasto che nessuno dei due può risolvere.
  check "nessuno è stato riavviato dal cluster" \
    "$(restarts_of worker-service)+$(restarts_of notification-service)" "$before_w+$before_n"

  note "aspetto che il broker torni"
  kube wait --for=condition=Ready pod/rabbitmq-0 --timeout=180s >/dev/null

  local deadline=$(( $(date +%s) + 120 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    [ "$(readiness_code worker-service)" = "200" ] && [ "$(readiness_code notification-service)" = "200" ] && break
    sleep 5
  done
  check "il worker è tornato pronto da solo" "$(readiness_code worker-service)" 200
  check "il notifier è tornato pronto da solo" "$(readiness_code notification-service)" 200

  prepare_user
  check "e il sistema elabora di nuovo" "$(wait_for_done "$(upload "dopo-il-broker.jpg")")" DONE
}

fault_ucciso() {
  say "Un worker ucciso a metà lavoro non perde né duplica niente"
  prepare_user
  pin_workers 1

  local ids=()
  for n in $(seq 1 12); do ids+=("$(upload "sotto-il-treno-$n.jpg")"); done
  note "12 immagini in coda, un solo worker"

  # Ucciso mentre di sicuro ne ha una fra le mani, e senza il periodo di
  # grazia: uno spegnimento pulito finirebbe il lavoro e non dimostrerebbe niente.
  sleep 2
  local pod
  pod=$(kube get pod -l app.kubernetes.io/component=worker-service -o jsonpath='{.items[0].metadata.name}')
  kube delete pod "$pod" --grace-period=0 --force --wait=false >/dev/null 2>&1
  note "worker ucciso di colpo: $pod"

  unpin_workers
  local done_count=0 failed=0
  for id in "${ids[@]}"; do
    case "$(wait_for_done "$id" 180)" in
      DONE) done_count=$(( done_count + 1 )) ;;
      *)    failed=$(( failed + 1 )) ;;
    esac
  done
  check "tutte e dodici sono arrivate in fondo" "$done_count" 12
  check "nessuna persa per strada" "$failed" 0

  # Nessun doppione: il messaggio che il worker morto non ha mai confermato è
  # tornato ed è stato elaborato di nuovo, e le varianti hanno chiavi
  # deterministiche, quindi il secondo passaggio ha sovrascritto il primo invece di aggiungersi.
  local wrong=0
  for id in "${ids[@]}"; do [ "$(variant_count "$id")" != "3" ] && wrong=$(( wrong + 1 )); done
  check "tre varianti ciascuna, non sei" "$wrong" 0
}

fault_database() {
  say "Senza database, l'API esce di servizio senza essere riavviata"
  local before
  before=$(restarts_of api-service)

  # Spegnere lo StatefulSet, non cancellare il pod: un pod cancellato torna in
  # piedi in pochi secondi, e il primo tentativo di questa prova non è riuscito
  # a osservare nessun disservizio perché il database era già tornato. Qui il
  # guasto dura finché non lo si toglie.
  kube scale statefulset postgres --replicas=0 >/dev/null
  local deadline=$(( $(date +%s) + 60 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    [ "$(kube get statefulset postgres -o jsonpath='{.status.readyReplicas}')" = "" ] && break
    sleep 2
  done
  note "database spento"

  # La sonda di prontezza gira ogni dieci secondi e si arrende dopo tre
  # fallimenti, quindi il pod impiega circa mezzo minuto a essere dichiarato non pronto.
  sleep 40

  check "l'api-service è ancora in esecuzione" "$(running_count api-service)" 1
  # Da dentro il pod stesso, non attraverso il suo Service: un pod non pronto
  # esce dagli indirizzi del Service, quindi chiederglielo per nome darebbe un
  # errore di connessione invece della risposta che si vuole leggere.
  check "l'api-service dichiara di non poter lavorare" \
    "$(readiness_code localhost /api/health/ready)" 503

  # Attraverso l'ingresso, che è la parte che conta per chi usa l'applicazione:
  # un pod non pronto viene tolto dal Service, quindi l'ingresso non ha nessuno a
  # cui inoltrare e rifiuta la richiesta subito, invece di lasciarla fallire
  # dentro, su un database morto.
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/health/ready" || echo 000)
  if [ "$code" = "200" ]; then
    ko "dall'ingresso arriva ancora 200: il pod non è uscito dal servizio"
  else
    ok "l'ingresso non ha più nessuno a cui girare la richiesta ($code)"
  fi

  # Tutto il senso della prova: non pronto non è morto. Una sonda di vitalità che
  # controllasse il database avrebbe già riavviato l'API, più volte, per un guasto che
  # che non può risolvere.
  check "nessun riavvio dell'api-service" "$(restarts_of api-service)" "$before"

  kube scale statefulset postgres --replicas=1 >/dev/null
  note "database riacceso"
  kube wait --for=condition=Ready pod/postgres-0 --timeout=180s >/dev/null

  # Tre risposte buone di fila, non una. Rientrare in servizio significa
  # rispondere, non aver risposto una volta: una singola lettura può cogliere
  # il momento in cui il pod è appena tornato fra i destinatari del Service ma
  # una delle dipendenze non ha ancora risposto, e l'esito del controllo
  # dipenderebbe da quando è partito il cronometro.
  local buone=0 code=000 t0
  t0=$(date +%s)
  deadline=$(( t0 + 180 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/health/ready" 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then
      buone=$(( buone + 1 ))
      [ "$buone" -ge 3 ] && break
    else
      buone=0
    fi
    sleep 3
  done
  if [ "$buone" -ge 3 ]; then
    ok "l'API rientra in servizio da sola ($(( $(date +%s) - t0 ))s dopo il database)"
  else
    ko "l'API non è rientrata in servizio: ultima risposta $code"
    # Il dettaglio per dipendenza, che dice *quale* delle tre non risponde.
    note "$(readiness localhost /api/health/ready)"
  fi

  prepare_user
  check "e il ciclo completo funziona di nuovo" "$(wait_for_done "$(upload "dopo-il-database.jpg")")" DONE
}

fault_archivio() {
  say "Un archivio dati riparte con i suoi dati"
  prepare_user
  local id
  id="$(upload "prima-dello-schianto.jpg")"
  check "immagine elaborata" "$(wait_for_done "$id")" DONE

  local before_variants
  before_variants=$(variant_count "$id")

  kube delete pod minio-0 --grace-period=0 --force --wait=false >/dev/null 2>&1
  note "object storage ucciso di colpo"
  kube wait --for=condition=Ready pod/minio-0 --timeout=180s >/dev/null

  check "le varianti sono ancora registrate" "$(variant_count "$id")" "$before_variants"
  # Il file in sé, scaricato attraverso l'ingresso con un link appena firmato:
  # dimostra che il disco è tornato sullo stesso nodo con il proprio contenuto.
  local detail url code
  detail=$(curl -sS "$BASE/api/assets/$id" -H "Authorization: Bearer $TOKEN")
  url=$(echo "$detail" | python3 -c 'import sys,json; print(json.load(sys.stdin)["variants"][0]["url"])')
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$url" || echo 000)
  check "e il file si scarica ancora" "$code" 200
}

# --- esecuzione -------------------------------------------------------------

case "$WHICH" in
  worker)   fault_worker ;;
  broker)   fault_broker ;;
  ucciso)   fault_ucciso ;;
  database) fault_database ;;
  archivio) fault_archivio ;;
  tutti)
    fault_worker
    fault_broker
    fault_ucciso
    fault_database
    fault_archivio
    ;;
  *) sed -n '3,13p' "$0" >&2; exit 2 ;;
esac

say "Esito"
if [ "$FAILURES" -eq 0 ]; then
  printf '   \033[32mtutte le verifiche superate\033[0m\n'
else
  printf '   \033[31m%s verifiche fallite\033[0m\n' "$FAILURES"
  exit 1
fi
