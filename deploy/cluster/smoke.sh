#!/usr/bin/env bash
#
# Un giro completo dalla porta d'ingresso del cluster, esattamente come lo fa il browser.
#
#   deploy/cluster/smoke.sh
#
# Undici passi, dalla registrazione di un utente alla lettura dell'avviso scritto
# dal notification-service. Ogni richiesta passa dall'Ingress e dal nome di
# dominio vero: qui niente parla a un Service direttamente, perché le parti che
# si rompono in un cluster sono proprio quelle che una chiamata diretta salta —
# le regole di instradamento, il tetto sulla dimensione del corpo, e la firma
# dei link dell'archivio, che copre il nome dell'host e non sopravviverebbe a uno sbagliato.
#
# A ogni passo dice cosa è successo, così un fallimento nomina il proprio passo.
#
set -euo pipefail

BASE="${BASE:-http://media-platform.test}"
EMAIL="smoke-$(date +%s)@example.com"
PASSWORD="password-lunga-di-prova"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
IMAGE="$WORK/prova.jpg"

say() { printf '\n== %s\n' "$*"; }
fail() { printf '   FALLITO: %s\n' "$*" >&2; exit 1; }

say "0. preparo un'immagine di prova"
# Due sistemi, due comandi. Su macOS `sips` c'è già e non va installato
# niente; su Linux — dove questo script gira dentro GitHub Actions — quel
# comando non esiste e il mestiere lo fa ImageMagick. Anche `stat` ha due
# sintassi diverse per la stessa domanda, «quanto pesa questo file».
SORGENTE="$(cd "$(dirname "$0")/../.." && pwd)/docs/architettura_locale.png"
if command -v sips >/dev/null; then
  sips -s format jpeg "$SORGENTE" --out "$IMAGE" >/dev/null
  SIZE=$(stat -f%z "$IMAGE")
else
  magick "$SORGENTE" "$IMAGE" 2>/dev/null || convert "$SORGENTE" "$IMAGE"
  SIZE=$(stat -c%s "$IMAGE")
fi
echo "   $SIZE byte"

say "1. registrazione"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
[ "$code" = "201" ] || fail "atteso 201, ricevuto $code"
echo "   201"

say "2. login"
TOKEN=$(curl -sS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
echo "   token di ${#TOKEN} caratteri"

say "3. registrazione dell'immagine e link firmato"
# Un nome accentato di proposito: deve sopravvivere fino all'header di scaricamento.
RESP=$(curl -sS -X POST "$BASE/api/assets" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"filename\":\"città al tramonto.jpg\",\"mime\":\"image/jpeg\",\"size_bytes\":$SIZE}")
ASSET_ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["asset_id"])')
UPLOAD_URL=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["upload"]["url"])')
echo "   asset $ASSET_ID"
echo "   firmato su $(echo "$UPLOAD_URL" | cut -d? -f1)"

say "4. caricamento diretto sull'object storage, attraverso l'ingresso"
# Il passo che dimostra che il link firmato sopravvive all'Ingress: la firma
# copre il nome dell'host, e NGINX deve consegnare a MinIO esattamente quello.
code=$(curl -sS -o /dev/null -w '%{http_code}' -X PUT --upload-file "$IMAGE" \
  -H 'Content-Type: image/jpeg' "$UPLOAD_URL")
[ "$code" = "200" ] || fail "PUT ha risposto $code"
echo "   200"

say "5. conferma del caricamento"
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/assets/$ASSET_ID/complete" \
  -H "Authorization: Bearer $TOKEN")
[ "$code" = "202" ] || fail "atteso 202, ricevuto $code"
echo "   202"

say "6. attesa dell'elaborazione (polling, come il frontend)"
STATUS=""
for i in $(seq 1 60); do
  STATUS=$(curl -sS "$BASE/api/assets/status?ids=$ASSET_ID" -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["items"][0]["status"])')
  [ "$STATUS" = "DONE" ] && { echo "   $STATUS dopo ${i} tentativi"; break; }
  [ "$STATUS" = "FAILED" ] && fail "elaborazione fallita"
  sleep 2
done
[ "$STATUS" = "DONE" ] || fail "mai arrivato a DONE"

say "7. dettaglio: varianti e metadati tecnici"
DETAIL=$(curl -sS "$BASE/api/assets/$ASSET_ID" -H "Authorization: Bearer $TOKEN")
echo "$DETAIL" | python3 -c '
import sys, json
d = json.load(sys.stdin)
if len(d["variants"]) != 3:
    raise SystemExit("attese 3 varianti, trovate %d" % len(d["variants"]))
for v in d["variants"]:
    print("   %-7s %sx%s" % (v["kind"], v["width"], v["height"]))
m = d.get("metadata") or {}
if not m:
    raise SystemExit("metadati tecnici assenti: il worker non ha scritto sul documentale")
print("   metadati:", m.get("dimensions"))
'

say "8. scaricamento di una variante (in locale la rotta /derived, su AWS S3)"
VARIANT_URL=$(echo "$DETAIL" | python3 -c 'import sys,json; print(json.load(sys.stdin)["variants"][0]["url"])')
read -r code bytes < <(curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' "$VARIANT_URL")
[ "$code" = "200" ] || fail "la variante ha risposto $code"
echo "   200, $bytes byte"

say "9. link che salva, con il nome accentato intatto"
DL_URL=$(echo "$DETAIL" | python3 -c 'import sys,json; print(json.load(sys.stdin)["variants"][0]["download_url"])')
disposition=$(curl -sS -o /dev/null -D - "$DL_URL" | tr -d '\r' | grep -i '^content-disposition:')
echo "$disposition" | grep -q "filename\*=UTF-8''" || fail "manca il nome codificato nell'header"
echo "  ${disposition#*:}"

say "10. scaricamento dell'originale (in locale la rotta /originals, su AWS S3)"
ORIG_URL=$(echo "$DETAIL" | python3 -c 'import sys,json; print(json.load(sys.stdin)["original_download_url"])')
read -r code bytes < <(curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' "$ORIG_URL")
[ "$code" = "200" ] || fail "l'originale ha risposto $code"
[ "$bytes" = "$SIZE" ] || fail "l'originale scaricato pesa $bytes invece di $SIZE"
echo "   200, $bytes byte: identico a quello caricato"

say "11. avviso scritto dal notification-service"
curl -sS "$BASE/api/notifications" -H "Authorization: Bearer $TOKEN" \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
if not d["items"]:
    raise SystemExit("nessun avviso: il notification-service non ha ricevuto l evento")
for n in d["items"][:3]:
    print("   [%s] %s" % (n["event"], n["subject"]))
'

printf '\n== giro completo riuscito\n'
