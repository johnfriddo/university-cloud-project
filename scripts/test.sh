#!/usr/bin/env bash
#
# Esegue i test del progetto. Serve solo Docker.
#
#   scripts/test.sh unit          test di unità: nessun servizio acceso, secondi
#   scripts/test.sh integration   ciclo completo su uno stack di prova, minuti
#   scripts/test.sh faults        guasti veri: spegne e riaccende container
#   scripts/test.sh lint          controllo dello stile con ruff
#   scripts/test.sh frontend      test del frontend Angular, con vitest
#   scripts/test.sh all           lint + unit + frontend + integration
#   scripts/test.sh down          spegne lo stack di prova e ne cancella i dati
#
# Gli argomenti in più vanno a pytest:
#   scripts/test.sh unit -k exif -v
#
set -euo pipefail

cd "$(dirname "$0")/../deploy/compose"

# Nome di progetto e configurazione separati da quelli di sviluppo: lo stack di
# prova ha database propri, che nascono vuoti, e porte proprie.
compose() {
  docker compose -p media-platform-test --env-file .env.test \
    -f docker-compose.yml -f docker-compose.test.yml "$@"
}

# Prima di ogni test di integrazione lo stack di prova va portato al codice
# attuale. `compose run` accende i servizi che mancano ma non ricostruisce
# quelli già accesi: senza questo passaggio i test proverebbero, in silenzio,
# la versione di prima delle ultime modifiche.
aggiorna_stack() {
  compose build --quiet
  compose up -d --wait api-service worker-service notification-service
}

comando="${1:-all}"
shift || true

case "$comando" in
  unit)
    # --no-deps: i test di unità non toccano niente, non serve accendere nulla.
    compose run --rm --no-deps tests pytest -m unit "$@"
    ;;
  lint)
    compose run --rm --no-deps tests ruff check . "$@"
    ;;
  frontend)
    compose run --rm --no-deps --build frontend-tests "$@"
    ;;
  integration)
    aggiorna_stack
    compose run --rm tests pytest -m "integration and not slow" "$@"
    ;;
  faults)
    aggiorna_stack
    # Questi spengono container dello stack di prova: girano da soli, e il
    # container di prova deve poter comandare Docker.
    compose run --rm -v /var/run/docker.sock:/var/run/docker.sock tests \
      pytest -m slow "$@"
    ;;
  all)
    compose run --rm --no-deps tests ruff check .
    compose run --rm --no-deps tests pytest -m unit
    compose run --rm --no-deps --build frontend-tests
    aggiorna_stack
    compose run --rm tests pytest -m "integration and not slow"
    ;;
  down)
    compose --profile test down -v
    ;;
  *)
    echo "Comando sconosciuto: $comando" >&2
    sed -n '3,13p' "$0" >&2
    exit 2
    ;;
esac
