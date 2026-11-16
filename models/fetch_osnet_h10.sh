#!/usr/bin/env bash
# Bench artifact (EVAL-ONLY); see models/osnet_h10.md. Verifies size, prints the SHA-256 to pin.
set -euo pipefail
URL='https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v5.3.0/hailo10h/osnet_x1_0.hef'
SIZE=3948544
OUT="${1:?usage: fetch_osnet_h10.sh <output path>}"
SHA="${GARUDA_OSNET_H10_SHA256:-}"
mkdir -p "$(dirname "$OUT")"
PART="$(mktemp "${OUT}.part.XXXXXX")"; trap 'rm -f "$PART"' EXIT
curl -fsS --connect-timeout 20 --max-time 300 -o "$PART" "$URL"
[ "$(stat -c %s "$PART")" = "$SIZE" ] || { echo "unexpected size" >&2; exit 1; }
GOT="$(sha256sum "$PART" | cut -d ' ' -f1)"
if [ -n "$SHA" ] && [ "$GOT" != "$SHA" ]; then echo "SHA-256 mismatch: $GOT" >&2; exit 1; fi
mv "$PART" "$OUT"; echo "fetched $OUT sha256=$GOT"
