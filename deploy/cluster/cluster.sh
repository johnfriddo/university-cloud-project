#!/usr/bin/env bash
#
# Il cluster Kubernetes locale: tre macchine Multipass, costruite con kubeadm.
#
#   deploy/cluster/cluster.sh create     crea le macchine e il cluster da zero
#   deploy/cluster/cluster.sh addons     dischi, consumi, Helm, ingresso e autoscaler
#   deploy/cluster/cluster.sh ingress    solo Helm e la porta d'ingresso
#   deploy/cluster/cluster.sh keda       solo l'autoscaler a eventi
#   deploy/cluster/cluster.sh status     nodi, pod di sistema e memoria usata
#   deploy/cluster/cluster.sh stop       spegne le macchine, conservandole
#   deploy/cluster/cluster.sh start      le riaccende
#   deploy/cluster/cluster.sh destroy    cancella tutto, senza ritorno
#   deploy/cluster/cluster.sh kubectl …  un comando kubectl sul cluster
#   deploy/cluster/cluster.sh ssh NODO   una shell su una delle macchine
#
# La Fase 5 costruisce il cluster a mano, passo per passo, così ogni passo lo si
# vede funzionare prima di automatizzarlo. Nella Fase 6 gli stessi passi diventano Ansible.
#
set -euo pipefail

K8S_MINOR="v1.36"
FLANNEL_VERSION="v0.28.9"
METRICS_SERVER_VERSION="v0.9.0"
LOCAL_PATH_VERSION="v0.0.37"
HELM_VERSION="v4.3.0"
# Il controller d'ingresso NGINX di F5, non quello della comunità «ingress-nginx»:
# quel progetto è stato ritirato e il suo repository archiviato a marzo 2026. Il chart 2.7.3 porta
# il controller 5.6.3. Vedi docs/registro-cluster.md.
NGINX_INGRESS_CHART_VERSION="2.7.3"
# KEDA: l'autoscaler che legge la coda invece del processore.
KEDA_CHART_VERSION="2.20.2"
# La porta fissa che ogni nodo apre per l'applicazione: http://<nodo>:30080.
# Tenuta come via d'ingresso per il debug, ma non è così che si raggiunge l'applicazione.
INGRESS_HTTP_NODEPORT=30080

# L'applicazione si raggiunge sulla porta 80 di questo solo nodo, e il nome nel
# /etc/hosts del Mac punta qui.
#
# La porta 80 non è una questione di gusto. I link che il browser usa per
# caricare e scaricare i file sono firmati, e la firma copre il nome dell'host;
# NGINX passa l'host a MinIO **senza** la porta. Un link firmato per
# «media-platform.test:30080» arriverebbe quindi a MinIO annunciando
# «media-platform.test», la firma non combacerebbe più, e ogni caricamento
# e ogni scaricamento verrebbero rifiutati. Sulla porta 80 non c'è nessuna porta da perdere.
INGRESS_NODE="media-w1"
UBUNTU="24.04"

# L'intervallo di indirizzi della rete fra i pod. Il predefinito di Flannel:
# cambiarlo significa cambiarlo in due posti, qui e nella configurazione di Flannel.
POD_CIDR="10.244.0.0/16"

CONTROL_PLANE="media-cp"
WORKERS=("media-w1" "media-w2")

# Dimensionate al minimo che kubeadm accetta: il portatile ha 8 GB in tutto, e
# macOS con un editor aperto ne vuole circa 3. Misurato e annotato in
# docs/registro-cluster.md.
CP_CPUS=2;     CP_MEMORY="2G"
WORKER_CPUS=2; WORKER_MEMORY="1536M"
DISK="10G"

# Una chiave del progetto, fuori dal repository. Ansible userà la stessa nella
# Fase 6: raggiunge le macchine via SSH, come fa questo script.
SSH_KEY="$HOME/.ssh/media-platform"
KNOWN_HOSTS="$HOME/.ssh/media-platform_known_hosts"

HERE="$(cd "$(dirname "$0")" && pwd)"

step() { printf '\n==> %s\n' "$*"; }

vm_ip() {
  multipass info "$1" --format csv | awk -F, 'NR==2 {print $3}'
}

# I comandi raggiungono le macchine via SSH normale, non con `multipass exec`.
#
# `multipass exec` è stato visto bloccarsi a caso: il client restava a girare al
# 99% di processore dentro la propria libreria SSH (libssh, in attesa di una
# fine-file su un canale che non arrivava mai), qualunque cosa avesse in entrata
# e in uscita. L'OpenSSH di sistema, usato qui sotto, ha eseguito 20 comandi di
# stall. Recorded in docs/registro-cluster.md.
in_vm() {
  local node="$1"; shift
  # printf %q protegge ogni argomento, così la shell remota vede esattamente le
  # parole date qui — patch JSON comprese — invece di spezzarle di nuovo.
  ssh -i "$SSH_KEY" \
      -o UserKnownHostsFile="$KNOWN_HOSTS" \
      -o StrictHostKeyChecking=accept-new \
      -o BatchMode=yes \
      -o ConnectTimeout=10 \
      "ubuntu@$(vm_ip "$node")" "$(printf '%q ' "$@")"
}

on_cp() { in_vm "$CONTROL_PLANE" "$@"; }

# Aggiunge la chiave del progetto a una macchina senza `multipass exec`: l'elenco
# delle chiavi autorizzate viene copiato fuori, esteso e ricopiato dentro. La
# chiave di Multipass resta nell'elenco, così `multipass shell` e `multipass transfer` continuano a funzionare.
install_key() {
  local node="$1" work
  work="$(mktemp -d)"
  multipass transfer "$node:/home/ubuntu/.ssh/authorized_keys" "$work/authorized_keys"
  grep -qxF "$(cat "$SSH_KEY.pub")" "$work/authorized_keys" \
    || cat "$SSH_KEY.pub" >> "$work/authorized_keys"
  multipass transfer "$work/authorized_keys" "$node:/home/ubuntu/.ssh/authorized_keys"
  rm -rf "$work"
}

ensure_key() {
  [ -f "$SSH_KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "media-platform cluster" -f "$SSH_KEY"
}

create() {
  ensure_key
  # Le macchine nuove riusano vecchi indirizzi con chiavi d'host nuove: una voce
  # rimasta indietro farebbe rifiutare da SSH le macchine come impostori.
  : > "$KNOWN_HOSTS"

  step "Creo le tre macchine virtuali"
  multipass launch "$UBUNTU" --name "$CONTROL_PLANE" --cpus "$CP_CPUS" --memory "$CP_MEMORY" --disk "$DISK"
  for worker in "${WORKERS[@]}"; do
    multipass launch "$UBUNTU" --name "$worker" --cpus "$WORKER_CPUS" --memory "$WORKER_MEMORY" --disk "$DISK"
  done

  step "Consegno la chiave SSH del progetto"
  for node in "$CONTROL_PLANE" "${WORKERS[@]}"; do
    install_key "$node"
  done

  step "Preparo i nodi (containerd, kubelet, kubeadm)"
  for node in "$CONTROL_PLANE" "${WORKERS[@]}"; do
    multipass transfer "$HERE/prepare-node.sh" "$node:/tmp/prepare-node.sh"
    in_vm "$node" sudo bash /tmp/prepare-node.sh "$K8S_MINOR"
  done

  local cp_ip
  cp_ip="$(vm_ip "$CONTROL_PLANE")"

  step "Accendo il piano di controllo su ${cp_ip}"
  on_cp sudo kubeadm init \
    --pod-network-cidr="$POD_CIDR" \
    --apiserver-advertise-address="$cp_ip" \
    --node-name="$CONTROL_PLANE"

  # kubectl gira dentro la macchina del piano di controllo: la sua versione
  # combacia con quella del cluster, non c'è niente da installare sul Mac, e le
  # credentials never leave the machine.
  on_cp bash -c 'mkdir -p ~/.kube && sudo cp /etc/kubernetes/admin.conf ~/.kube/config && sudo chown "$(id -u):$(id -g)" ~/.kube/config'

  step "Installo la rete fra i pod (Flannel ${FLANNEL_VERSION})"
  on_cp kubectl apply -f "https://github.com/flannel-io/flannel/releases/download/${FLANNEL_VERSION}/kube-flannel.yml"

  step "Aggancio gli operai al cluster"
  local join
  join="$(on_cp sudo kubeadm token create --print-join-command)"
  for worker in "${WORKERS[@]}"; do
    # shellcheck disable=SC2086  # il comando di join va spezzato in parole
    in_vm "$worker" sudo $join --node-name="$worker"
  done

  step "Aspetto che tutti i nodi siano pronti"
  on_cp kubectl wait --for=condition=Ready node --all --timeout=300s

  status
}

addons() {
  step "Gestore dei dischi (local-path-provisioner ${LOCAL_PATH_VERSION})"
  # kubeadm non porta con sé nessun sistema di dischi: un database che chiede un
  # disco persistente aspetterebbe per sempre. Questo gestore risponde a quelle
  # richieste con una cartella sul nodo dove gira il pod — elementare, ma sufficiente per un cluster di laboratorio.
  on_cp kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"
  # Classe predefinita: il chart può così chiedere «un disco» senza nominare
  # nessun gestore, e lo stesso chart funziona su AWS con EBS dietro.
  on_cp kubectl patch storageclass local-path \
    -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

  step "Misuratore dei consumi (metrics-server ${METRICS_SERVER_VERSION})"
  on_cp kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"
  # kubeadm dà a ogni kubelet un certificato autofirmato, che metrics-server non
  # sa verificare. Saltare quel controllo è la scelta abituale per un cluster di
  # laboratorio su una rete privata; l'alternativa (certificati firmati dal
  # cluster e approvati a mano, di nuovo a ogni rinnovo) qui non comprerebbe niente.
  on_cp kubectl -n kube-system patch deployment metrics-server --type=json \
    -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

  on_cp kubectl -n local-path-storage rollout status deployment/local-path-provisioner --timeout=180s
  on_cp kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s

  ingress
  keda
}

install_helm() {
  if on_cp helm version --short 2>/dev/null | grep -q "^${HELM_VERSION}+"; then
    return
  fi
  step "Installo Helm ${HELM_VERSION} nel piano di controllo"
  # L'impronta viene verificata prima di installare qualunque cosa: uno scaricamento
  # alterato per strada finirebbe altrimenti per girare come amministratore.
  on_cp bash -c "set -e; cd /tmp
    curl -fsSL -o helm.tgz https://get.helm.sh/helm-${HELM_VERSION}-linux-arm64.tar.gz
    curl -fsSL -o helm.tgz.sha256sum https://get.helm.sh/helm-${HELM_VERSION}-linux-arm64.tar.gz.sha256sum
    echo \"\$(cut -d' ' -f1 helm.tgz.sha256sum)  helm.tgz\" | sha256sum -c -
    tar -xzf helm.tgz linux-arm64/helm
    sudo install -m 0755 linux-arm64/helm /usr/local/bin/helm
    rm -rf helm.tgz* linux-arm64"
}

ingress() {
  install_helm

  step "Porta d'ingresso: NGINX Ingress Controller (chart ${NGINX_INGRESS_CHART_VERSION})"
  # Ogni valore diverso dai predefiniti del chart vive in un file solo, con la
  # ragione scritta accanto: nginx-ingress-values.yaml. Prima erano quindici
  # opzioni --set ripetute qui e nel ruolo di Ansible.
  multipass transfer "$HERE/nginx-ingress-values.yaml" "$CONTROL_PLANE:/tmp/nginx-ingress-values.yaml"
  on_cp helm upgrade --install nginx-ingress oci://ghcr.io/nginx/charts/nginx-ingress \
    --version "$NGINX_INGRESS_CHART_VERSION" \
    --namespace nginx-ingress --create-namespace \
    --values /tmp/nginx-ingress-values.yaml \
    --wait --timeout 5m

  step "Verifico che la porta 80 del nodo ${INGRESS_NODE} risponda"
  in_vm "$INGRESS_NODE" curl -fsS -o /dev/null -w 'porta 80: %{http_code}\n' http://localhost/nginx-health
}

keda() {
  install_helm

  step "Autoscaler a eventi: KEDA ${KEDA_CHART_VERSION}"
  # Kubernetes sa scalare su processore e memoria, e qui nessuno dei due dice
  # niente di utile: un worker in attesa sulla coda non consuma processore, e
  # quando il processore salirebbe la raffica sarebbe già finita. KEDA aggiunge
  # la metrica che conta — quanti messaggi aspettano — e la consegna al normale
  # HorizontalPodAutoscaler.
  #
  # Three deployments come with it: the operator, a metrics server that answers
  # alle domande dell'HPA, e un webhook che rifiuta i ScaledObject malformati.
  # Tutti e tre hanno un tetto di memoria, come ogni altra cosa qui dentro.
  # Il chart arriva dal repository Helm di KEDA, indirizzato come URL diretto
  # invece che con `helm repo add`: non c'è niente da registrare dentro la
  # macchina, e la versione si vede nel comando che la installa.
  on_cp helm upgrade --install keda \
    "https://kedacore.github.io/charts/keda-${KEDA_CHART_VERSION}.tgz" \
    --namespace keda --create-namespace \
    --set resources.operator.requests.memory=64Mi \
    --set resources.operator.limits.memory=192Mi \
    --set resources.metricServer.requests.memory=48Mi \
    --set resources.metricServer.limits.memory=128Mi \
    --set resources.webhooks.requests.memory=32Mi \
    --set resources.webhooks.limits.memory=96Mi \
    --wait --timeout 5m

  step "Verifico che KEDA sappia rispondere"
  # L'API delle metriche è la parte che deve funzionare: senza, l'HPA fa una
  # domanda a cui non risponde nessuno, e il numero di repliche non si muove mai.
  on_cp kubectl get apiservice v1beta1.external.metrics.k8s.io \
    -o custom-columns='API:.metadata.name,DISPONIBILE:.status.conditions[0].status'
}

status() {
  step "Nodi"
  on_cp kubectl get nodes -o wide

  step "Pod di sistema"
  on_cp kubectl get pods -A -o wide

  step "Memoria dentro ogni macchina"
  for node in "$CONTROL_PLANE" "${WORKERS[@]}"; do
    printf '%-9s ' "$node"
    in_vm "$node" free -m | awk '/^Mem:/ {printf "totale %5s MB   usata %5s MB   disponibile %5s MB\n", $2, $3, $7}'
  done
}

case "${1:-}" in
  create)  create ;;
  addons)  addons ;;
  ingress) ingress ;;
  keda)    keda ;;
  status)  status ;;
  stop)    multipass stop "$CONTROL_PLANE" "${WORKERS[@]}" ;;
  start)   multipass start "$CONTROL_PLANE" "${WORKERS[@]}" ;;
  destroy)
    multipass delete "$CONTROL_PLANE" "${WORKERS[@]}"
    multipass purge
    : > "$KNOWN_HOSTS"
    ;;
  kubectl) shift; on_cp kubectl "$@" ;;
  ssh)
    shift
    ssh -i "$SSH_KEY" -o UserKnownHostsFile="$KNOWN_HOSTS" -o StrictHostKeyChecking=accept-new \
        "ubuntu@$(vm_ip "${1:-$CONTROL_PLANE}")"
    ;;
  *)
    sed -n '3,14p' "$0" >&2
    exit 2
    ;;
esac
