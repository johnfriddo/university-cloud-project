#!/usr/bin/env bash
#
# Installa l'applicazione sul cluster locale, con Helm.
#
#   deploy/cluster/app.sh secrets     genera le credenziali, una volta sola
#   deploy/cluster/app.sh install     installa o aggiorna l'applicazione
#   deploy/cluster/app.sh template    stampa il risultato senza installare niente
#   deploy/cluster/app.sh status      pod, dischi, lavori e memoria usata
#   deploy/cluster/app.sh logs NOME   il registro di un servizio
#   deploy/cluster/app.sh test        la suite di integrazione, dentro il cluster
#   deploy/cluster/app.sh uninstall   disinstalla, conservando i dischi
#   deploy/cluster/app.sh purge       disinstalla e cancella anche i dischi
#
# Helm gira dentro la macchina del piano di controllo, non sul Mac: è lì che
# stanno già kubectl e le credenziali del cluster, e qui non c'è niente da
# installare. Il chart ci arriva come archivio via SSH prima di ogni esecuzione,
# così quello che si installa è sempre quello che c'è su disco adesso.
#
set -euo pipefail

RELEASE="media-platform"
NAMESPACE="media-platform"
CONTROL_PLANE="media-cp"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CHART_DIR="$REPO/deploy/helm/media-platform"
# Credentials live outside git: .gitignore lists this name.
VALUES_LOCAL="$REPO/deploy/helm/values.local.yaml"

# Dove atterra il chart dentro la macchina del piano di controllo.
REMOTE_CHART="/home/ubuntu/media-platform-chart"
REMOTE_VALUES="/home/ubuntu/values.local.yaml"

SSH_KEY="$HOME/.ssh/media-platform"
KNOWN_HOSTS="$HOME/.ssh/media-platform_known_hosts"

step() { printf '\n==> %s\n' "$*"; }

vm_ip() {
  multipass info "$1" --format csv | awk -F, 'NR==2 {print $3}'
}

ssh_cp() {
  ssh -i "$SSH_KEY" \
      -o UserKnownHostsFile="$KNOWN_HOSTS" \
      -o StrictHostKeyChecking=accept-new \
      -o BatchMode=yes \
      -o ConnectTimeout=10 \
      "ubuntu@$(vm_ip "$CONTROL_PLANE")" "$@"
}

on_cp() { ssh_cp "$(printf '%q ' "$@")"; }

# Una password di sole lettere e cifre, di proposito: questi valori finiscono
# dentro stringhe di connessione come postgresql://utente:password@host/db, dove
# una barra, una chiocciola o due punti verrebbero letti come parte
# dell'indirizzo invece che della password. La casualità la dà il sistema.
random_secret() {
  local length="$1"
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$length"
}

secrets() {
  if [ -f "$VALUES_LOCAL" ]; then
    echo "le credenziali esistono già: $VALUES_LOCAL"
    echo "per rigenerarle, cancella il file — ma i dati esistenti non saranno più leggibili"
    return
  fi

  step "Genero le credenziali in $VALUES_LOCAL"
  umask 077
  cat > "$VALUES_LOCAL" <<EOF
# Credenziali del cluster locale, generate da deploy/cluster/app.sh secrets.
#
# NON va in git: .gitignore lo esclude. Se lo perdi, i dati già scritti su
# PostgreSQL, MongoDB e MinIO restano dove sono ma nessuno sa più aprirli.
#
# Generato il $(date '+%Y-%m-%d %H:%M')

secrets:
  postgresUser: media
  postgresPassword: $(random_secret 24)
  mongoUser: media
  mongoPassword: $(random_secret 24)
  rabbitmqUser: media
  rabbitmqPassword: $(random_secret 24)
  minioRootUser: media
  # MinIO vuole almeno 8 caratteri.
  minioRootPassword: $(random_secret 32)
  # Almeno 32 caratteri: sotto quella lunghezza una firma HMAC-SHA256 è
  # considerata debole (RFC 7518).
  jwtSecret: $(random_secret 48)
EOF
  chmod 600 "$VALUES_LOCAL"
  echo "fatto. Il file resta sul Mac e non entra mai nella repo."
}

# Copia il chart e le credenziali dentro la macchina del piano di controllo.
send_chart() {
  [ -f "$VALUES_LOCAL" ] || { echo "mancano le credenziali: lancia prima 'app.sh secrets'" >&2; exit 1; }

  ssh_cp "rm -rf '$REMOTE_CHART' && mkdir -p '$REMOTE_CHART'"
  tar -czf - -C "$CHART_DIR" . | ssh_cp "tar -xzf - -C '$REMOTE_CHART'"
  # Le credenziali arrivano dallo stesso canale cifrato, e atterrano in un file
  # che può leggere solo il proprietario.
  ssh_cp "umask 077 && cat > '$REMOTE_VALUES'" < "$VALUES_LOCAL"
}

install() {
  send_chart

  step "Installo il chart nel namespace ${NAMESPACE}"
  # --wait: il comando torna quando i pod sono pronti davvero, non quando
  # Kubernetes ha accettato la richiesta. Senza, "installato" vorrebbe dire
  # soltanto "gli oggetti esistono".
  #
  # --timeout 10m e non il predefinito di 5: la prima installazione crea
  # quattro dischi e avvia quattro archivi dati su due macchine virtuali che
  # condividono lo stesso disco fisico.
  on_cp helm upgrade --install "$RELEASE" "$REMOTE_CHART" \
    --namespace "$NAMESPACE" --create-namespace \
    --values "$REMOTE_VALUES" \
    --wait --timeout 10m

  status
}

template() {
  send_chart
  on_cp helm template "$RELEASE" "$REMOTE_CHART" \
    --namespace "$NAMESPACE" \
    --values "$REMOTE_VALUES"
}

status() {
  step "Pod"
  on_cp kubectl -n "$NAMESPACE" get pods -o wide

  step "Lavori una tantum"
  on_cp kubectl -n "$NAMESPACE" get jobs

  step "Dischi"
  on_cp kubectl -n "$NAMESPACE" get pvc

  step "Ingresso"
  on_cp kubectl -n "$NAMESPACE" get ingress

  # Le code dei rifiutati, nell'unico posto dove qualcuno guarda davvero. Un
  # CronJob scrive un avviso nel registro ogni quindici minuti, ma è un numero
  # sullo schermo a renderlo impossibile da non vedere.
  step "Code"
  on_cp kubectl -n "$NAMESPACE" exec rabbitmq-0 -- \
    rabbitmqctl list_queues --quiet name messages consumers \
    || echo "   (il broker non risponde)"

  step "Memoria per pod"
  on_cp kubectl -n "$NAMESPACE" top pods --no-headers || echo "   (il misuratore non ha ancora dati: riprova fra un minuto)"

  step "Memoria per nodo"
  on_cp kubectl top nodes || true
}

logs() {
  local component="${1:?serve il nome di un servizio, per esempio worker-service}"
  shift || true
  on_cp kubectl -n "$NAMESPACE" logs -l "app.kubernetes.io/component=$component" --tail=200 "$@"
}

# Runs the integration suite inside the cluster, against the real ingress.
#
# Gli stessi test che girano contro docker compose, senza modifiche. Quello che
# cambia è da dove girano: un pod nel cluster, che raggiunge l'applicazione
# attraverso `media-platform.test` esattamente come fa il browser — le regole di
# instradamento, i link firmati dell'archivio e il DNS fanno tutti parte di ciò
# che si sta provando, e un test che chiamasse i Service direttamente salterebbe proprio quelli.
#
# I test viaggiano come ConfigMap invece di essere cotti dentro un'immagine:
# sono 42 KB di testo, cambiano molto più spesso di qualunque immagine, e
# un'immagine andrebbe ricostruita e portata nel cluster a mano per ogni
# asserzione modificata. In Fase 7 la pipeline costruisce un'immagine di prova e questo diventa un normale scaricamento.
test_suite() {
  step "Consegno i test al cluster"
  local remote="/home/ubuntu/media-platform-tests"
  ssh_cp "rm -rf '$remote' && mkdir -p '$remote/suite' '$remote/root'"
  tar -czf - -C "$REPO/tests/integration" . | ssh_cp "tar -xzf - -C '$remote/suite'"
  tar -czf - -C "$REPO" pyproject.toml conftest.py | ssh_cp "tar -xzf - -C '$remote/root'"

  # Due ConfigMap e non una: sia la radice del repository sia la suite hanno un
  # file chiamato conftest.py, e una ConfigMap non può contenere due volte la stessa chiave.
  on_cp bash -c "kubectl -n '$NAMESPACE' create configmap tests-suite --from-file='$remote/suite' \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl -n '$NAMESPACE' create configmap tests-root --from-file='$remote/root' \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null"

  # Dove il pod deve cercare il nome dell'applicazione. Ogni link firmato
  # dell'archivio che gli viene consegnato indica quel nome, quindi deve risolvere
  # porta d'ingresso, altrimenti non funziona niente.
  #
  # L'indirizzo del Service del controller d'ingresso, non quello del nodo. Un pod
  # che raggiunge la porta 80 del nodo esce dal cluster e rientra, e per strada il
  # suo indirizzo viene tradotto in quello del nodo: l'ingresso vede allora tre
  # macchine invece di un pod, il che rompe l'esenzione che tiene questa suite
  # fuori dal limite di frequenza sulle rotte di accesso. Misurato, non
  # supposto — il registro degli accessi diceva `client: 192.168.252.7`.
  #
  # Attraverso il Service il traffico resta dentro il cluster e l'indirizzo del pod
  # arriva intatto.
  local ingress_ip
  ingress_ip=$(on_cp kubectl -n nginx-ingress get service \
    -l app.kubernetes.io/instance=nginx-ingress -o jsonpath='{.items[0].spec.clusterIP}')
  echo "   media-platform.test → ${ingress_ip} (il Service dell'ingresso)"

  step "Eseguo la suite"
  on_cp kubectl -n "$NAMESPACE" delete job integration-tests --ignore-not-found >/dev/null

  # L'immagine del worker: porta già Pillow, che ai test serve per fabbricare le
  # immagini, e ogni altra libreria che toccano è quella standard.
  # Manca solo pytest, che viene scaricato all'avvio — l'unica cosa qui dentro
  # che ha bisogno della rete, e la ragione per cui un'immagine di prova vera
  # appartiene alla pipeline e non a questo script.
  ssh_cp "kubectl -n '$NAMESPACE' apply -f -" <<YAML >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: integration-tests
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      hostAliases:
        - ip: "${ingress_ip}"
          hostnames: ["${RELEASE}.test"]
      containers:
        - name: tests
          image: media-platform/worker-service:0.1.0
          imagePullPolicy: Never
          workingDir: /repo
          command: ["sh", "-c"]
          args:
            - |
              set -e
              pip install --quiet --disable-pip-version-check --target /tmp/libs "pytest~=8.3"
              export PYTHONPATH=/tmp/libs
              exec python -m pytest tests/integration \
                -m "integration and not slow" \
                -p no:cacheprovider --color=yes
          env:
            - name: API_BASE
              value: "http://${RELEASE}.test"
            - name: RABBITMQ_URL
              valueFrom: { secretKeyRef: { name: app-secrets, key: RABBITMQ_URL } }
          resources:
            requests: { memory: 128Mi, cpu: 100m }
            limits: { memory: 384Mi }
          volumeMounts:
            - { name: suite, mountPath: /repo/tests/integration }
            - { name: root, mountPath: /repo/conftest.py, subPath: conftest.py }
            - { name: root, mountPath: /repo/pyproject.toml, subPath: pyproject.toml }
      volumes:
        - { name: suite, configMap: { name: tests-suite } }
        - { name: root, configMap: { name: tests-root } }
YAML

  # Segue l'uscita mentre succede: una suite che si pianta è più istruttiva
  # mentre si pianta che dopo.
  on_cp kubectl -n "$NAMESPACE" wait --for=condition=Ready pod \
    -l job-name=integration-tests --timeout=120s >/dev/null 2>&1 || true
  ssh_cp "kubectl -n '$NAMESPACE' logs -f job/integration-tests"

  # `logs -f` torna appena il processo finisce, mentre lo stato del Job viene
  # scritto un istante dopo: chiederlo subito darebbe «fallito» a una suite
  # appena passata.
  if on_cp kubectl -n "$NAMESPACE" wait --for=condition=Complete job/integration-tests \
       --timeout=120s >/dev/null 2>&1; then
    step "Suite superata"
  else
    echo "la suite ha fallito" >&2
    exit 1
  fi
}

uninstall() {
  step "Disinstallo ${RELEASE}"
  # I dischi restano: Helm non cancella i PersistentVolumeClaim creati da uno
  # StatefulSet, e reinstallando si ritrovano i dati di prima. Per buttarli
  # via davvero c'è 'purge'.
  on_cp helm uninstall "$RELEASE" --namespace "$NAMESPACE" --wait
  echo "i dischi sono rimasti. Per cancellarli: deploy/cluster/app.sh purge"
}

purge() {
  on_cp helm uninstall "$RELEASE" --namespace "$NAMESPACE" --wait || true
  step "Cancello i dischi e il namespace"
  on_cp kubectl delete namespace "$NAMESPACE" --wait=true
}

case "${1:-}" in
  secrets)   secrets ;;
  install)   install ;;
  template)  template ;;
  status)    status ;;
  logs)      shift; logs "$@" ;;
  test)      test_suite ;;
  uninstall) uninstall ;;
  purge)     purge ;;
  *)         sed -n '3,11p' "$0" >&2; exit 2 ;;
esac
