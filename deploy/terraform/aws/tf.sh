#!/usr/bin/env bash
#
# Terraform su AWS, con lo stato tenuto su S3.
#
#   deploy/terraform/aws/tf.sh bootstrap          crea il bucket dello stato (una volta)
#   deploy/terraform/aws/tf.sh infra <comando>    rete, cluster, database, coda...
#   deploy/terraform/aws/tf.sh app   <comando>    quello che gira dentro il cluster
#
#   es.  tf.sh infra plan
#        tf.sh infra apply
#        tf.sh app destroy
#
# Perché lo stato non sta su questo disco: contiene ogni password in chiaro
# (verificato in Fase 6, vedi docs/registro-cluster.md). Su S3 è cifrato, con
# le versioni attive — uno stato sovrascritto si recupera — e mai pubblico.
#
# Perché due parti: il cluster e ciò che ci gira sopra hanno vite diverse.
# L'applicazione si distrugge e si reinstalla spesso; la rete e il database no.
# E una sola configurazione di Terraform che crea il cluster e insieme gli parla
# dovrebbe configurare il provider Kubernetes verso un cluster che al momento
# del piano non esiste ancora — una fonte nota di primi apply rotti.
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REGION="${AWS_REGION:-$(aws configure get region)}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
# Il numero dell'account rende il nome unico nel mondo, come pretende S3, senza
# scrivere l'account dentro il repository.
BUCKET="media-platform-tfstate-${ACCOUNT}"

bootstrap() {
  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "Il bucket dello stato esiste già: $BUCKET"
    return
  fi
  echo "==> Creo il bucket dello stato: $BUCKET ($REGION)"
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  # Le versioni: uno stato sovrascritto per sbaglio si può riportare indietro.
  aws s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "Fatto."
}

run() {
  local part="$1"; shift
  local dir="$HERE/$part"
  [ -d "$dir" ] || { echo "Parte sconosciuta: $part (infra o app)" >&2; exit 2; }

  # Il blocco del backend non può contenere variabili, quindi bucket e regione si
  # danno qui: «configurazione parziale». Rilanciare init è innocuo.
  if [ ! -d "$dir/.terraform" ] || [ "${1:-}" = "init" ]; then
    terraform -chdir="$dir" init -input=false \
      -backend-config="bucket=$BUCKET" \
      -backend-config="region=$REGION" >/dev/null
    [ "${1:-}" = "init" ] && { echo "Inizializzato: $part"; return; }
  fi

  TF_VAR_region="$REGION" TF_VAR_state_bucket="$BUCKET" \
    terraform -chdir="$dir" "$@"
}

case "${1:-}" in
  bootstrap) bootstrap ;;
  infra|app) run "$@" ;;
  *) sed -n '3,11p' "$0"; exit 2 ;;
esac
