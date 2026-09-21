#!/usr/bin/env bash
#
# Porta le immagini dell'applicazione dentro il cluster, senza un registro.
#
#   deploy/cluster/images.sh export   costruisce le immagini e le esporta in un file
#   deploy/cluster/images.sh load     consegna il file ai nodi e ce le importa
#   deploy/cluster/images.sh check    elenca le immagini presenti su ogni nodo
#   deploy/cluster/images.sh prune    recupera lo spazio dei livelli superati
#
# Un registro sarebbe un servizio in più da tenere acceso, e memoria che questo
# portatile non ha. Le macchine non vedono il Docker del Mac, quindi le immagini
# viaggiano come file: esportate una volta, copiate su ogni operaio, importate
# in containerd — il runtime a cui il kubelet chiede davvero le immagini.
#
# Le due fasi sono separate di proposito: `export` ha bisogno di Docker acceso,
# `load` del cluster acceso, e su 8 GB i due non stanno comodi insieme.
# In Fase 7 entrambe vengono sostituite da un registro (ECR) e da uno scaricamento.
#
set -euo pipefail

VERSION="0.1.0"
SERVICES=(api-service worker-service notification-service frontend)

# Solo gli operai: il piano di controllo porta il marchio che gli dà kubeadm,
# quindi là non viene mai schedulato nessun pod dell'applicazione.
TARGETS=(media-w1 media-w2)

# containerd tiene le immagini in namespace; il kubelet guarda in questo.
# Un'immagine importata nel namespace predefinito sarebbe invisibile a Kubernetes.
CONTAINERD_NAMESPACE="k8s.io"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ARCHIVE="${TMPDIR:-/tmp}/media-platform-images-${VERSION}.tar"

SSH_KEY="$HOME/.ssh/media-platform"
KNOWN_HOSTS="$HOME/.ssh/media-platform_known_hosts"

step() { printf '\n==> %s\n' "$*"; }

in_vm() {
  local node="$1"; shift
  ssh -i "$SSH_KEY" -o UserKnownHostsFile="$KNOWN_HOSTS" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 \
      "ubuntu@$(multipass info "$node" --format csv | awk -F, 'NR==2 {print $3}')" \
      "$(printf '%q ' "$@")"
}

export_images() {
  step "Costruisco le immagini"
  ( cd "$REPO/deploy/compose" && docker compose build "${SERVICES[@]}" )

  step "Le etichetto con la versione ${VERSION}"
  # Una versione, mai «latest»: Kubernetes riscarica un'immagine «latest» a ogni
  # avvio, e qui non c'è nessun posto da cui scaricarla.
  for service in "${SERVICES[@]}"; do
    docker tag "media-platform-${service}:latest" "media-platform/${service}:${VERSION}"
  done

  step "Le esporto in un solo file"
  # Un archivio solo per tutte e quattro: i livelli che condividono si salvano una volta.
  local tagged=()
  for service in "${SERVICES[@]}"; do tagged+=("media-platform/${service}:${VERSION}"); done
  docker save -o "$ARCHIVE" "${tagged[@]}"
  echo "$ARCHIVE — $(du -h "$ARCHIVE" | cut -f1)"
}

load_images() {
  [ -f "$ARCHIVE" ] || { echo "manca $ARCHIVE: lancia prima 'images.sh export'" >&2; exit 1; }

  for node in "${TARGETS[@]}"; do
    step "Consegno le immagini a ${node}"
    multipass transfer "$ARCHIVE" "$node:/tmp/images.tar"
    in_vm "$node" sudo ctr --namespace "$CONTAINERD_NAMESPACE" images import /tmp/images.tar
    in_vm "$node" rm -f /tmp/images.tar
  done

  check
}

check() {
  for node in "${TARGETS[@]}"; do
    step "Immagini su ${node}"
    in_vm "$node" sudo ctr --namespace "$CONTAINERD_NAMESPACE" images ls -q \
      | grep "media-platform/" || echo "   nessuna immagine dell'applicazione"
  done
}

# Recupera lo spazio lasciato indietro dalle importazioni precedenti.
#
# Importare una build nuova con la stessa etichetta sposta l'etichetta ma non
# rimuove i livelli che usava la vecchia: containerd li tiene finché qualcuno
# non chiede di toglierli. Dopo qualche ciclo di ricostruzione e importazione la
# differenza è grande — misurata qui: 153 blocchi di contenuto per 48 immagini,
# e 1,2 GB recuperati su un nodo, che è passato dal 77% del disco al 63%.
#
# Conta perché il kubelet comincia a sfrattare i pod all'85%, e su un disco da
# 10 GB quella soglia dista poche ricostruzioni.
prune() {
  for node in "${TARGETS[@]}"; do
    step "Recupero lo spazio su ${node}"
    local before after
    before="$(in_vm "$node" df -h / | awk 'NR==2 {print $5}')"
    in_vm "$node" sudo ctr --namespace "$CONTAINERD_NAMESPACE" content prune references >/dev/null
    after="$(in_vm "$node" df -h / | awk 'NR==2 {print $5}')"
    echo "   disco: ${before} → ${after}"
  done
}

case "${1:-}" in
  export) export_images ;;
  load)   load_images ;;
  check)  check ;;
  prune)  prune ;;
  *)      sed -n '3,8p' "$0" >&2; exit 2 ;;
esac
