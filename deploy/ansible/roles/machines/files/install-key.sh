#!/usr/bin/env bash
#
# Aggiunge la chiave pubblica del progetto a una macchina Multipass.
#
#   install-key.sh <macchina> <chiave.pub>
#
# Non con `multipass exec`, che su questo Mac si bloccava a caso: il client
# restava a girare al 99% di processore dentro la propria libreria SSH, in
# never closed (see docs/registro-cluster.md). `multipass transfer` moves files
# senza aprire un canale interattivo, quindi l'elenco delle chiavi autorizzate
# carried out, extended and carried back.
#
# La chiave di Multipass resta nell'elenco, così `multipass shell` continua a funzionare.
#
# Stampa «aggiunta» quando ha cambiato qualcosa, così Ansible se ne accorge.
#
set -euo pipefail

MACHINE="${1:?serve il nome della macchina}"
PUBKEY="${2:?serve il percorso della chiave pubblica}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

multipass transfer "$MACHINE:/home/ubuntu/.ssh/authorized_keys" "$WORK/authorized_keys"

if grep -qxF "$(cat "$PUBKEY")" "$WORK/authorized_keys"; then
  echo "già presente su $MACHINE"
  exit 0
fi

cat "$PUBKEY" >> "$WORK/authorized_keys"
multipass transfer "$WORK/authorized_keys" "$MACHINE:/home/ubuntu/.ssh/authorized_keys"
echo "aggiunta su $MACHINE"
