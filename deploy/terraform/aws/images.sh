#!/usr/bin/env bash
#
# Costruisce le quattro immagini e le carica su ECR.
#
#   deploy/terraform/aws/images.sh            costruisce e carica, con etichetta automatica
#   deploy/terraform/aws/images.sh 20260922a  ...con l'etichetta che dici tu
#
# Stampa l'etichetta sull'ultima riga: è quella da passare a Terraform.
#
#   TAG=$(deploy/terraform/aws/images.sh)
#   deploy/terraform/aws/tf.sh app apply -var image_tag="$TAG"
#
# Perché mai «latest»: due installazioni a un mese di distanza eseguirebbero
# codice diverso sotto lo stesso nome, e `kubectl describe` non saprebbe
# distinguerle. L'etichetta è il momento in cui le immagini sono state costruite.
#
# Perché linux/amd64 su un portatile ARM, che è la via lenta: i nodi sono
# Intel. Non per scelta — il piano gratuito di AWS lascia accendere a questo
# account solo tipi di macchina del free tier, ed EKS Auto Mode accetta solo
# taglie da «large» in su. L'unica famiglia che soddisfa entrambe è la «flex»
# (vedi il NodePool in app/cluster.tf).
#
# Docker costruisce quindi in emulazione, il che richiede minuti invece di
# secondi. L'alternativa — un'immagine costruita per ARM — semplicemente non
# partirebbe su quei nodi: «exec format error», che non suona come ciò che è.
#
set -euo pipefail

SERVICES=(api-service worker-service notification-service frontend)
PLATFORM="linux/amd64"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

TAG="${1:-$(date +%Y%m%d-%H%M%S)}"
REGION="${AWS_REGION:-$(aws configure get region)}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

step() { printf '\n==> %s\n' "$*" >&2; }

step "Accesso a ECR ($REGISTRY)"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

for service in "${SERVICES[@]}"; do
  image="${REGISTRY}/media-platform/${service}:${TAG}"
  step "Costruisco $service"

  if [ "$service" = "frontend" ]; then
    # Il frontend si costruisce dalla propria cartella: non ha bisogno del
    # pacchetto Python condiviso, e il suo Dockerfile compila Angular dentro di sé.
    docker build --platform "$PLATFORM" -t "$image" "$REPO/services/frontend" >&2
  else
    # Gli altri tre si costruiscono da services/, perché l'immagine ha bisogno
    # anche del pacchetto condiviso media-common che sta lì accanto.
    docker build --platform "$PLATFORM" \
      -f "$REPO/services/${service}/Dockerfile" \
      -t "$image" "$REPO/services" >&2
  fi

  step "Carico $service"
  docker push "$image" >&2
done

step "Fatto. Etichetta:"
echo "$TAG"
