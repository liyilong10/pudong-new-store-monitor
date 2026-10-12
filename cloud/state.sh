#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/gaode-poi-monitor/data/poi_monitor.db"
ENC="$ROOT/state/poi_monitor.db.enc"
: "${STATE_ENCRYPTION_KEY:?STATE_ENCRYPTION_KEY is required}"
mkdir -p "$(dirname "$DB")" "$(dirname "$ENC")"
case "${1:-}" in
  decrypt)
    if [[ -f "$ENC" ]]; then
      openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$ENC" -out "$DB" -pass env:STATE_ENCRYPTION_KEY
    elif [[ ! -f "$DB" ]]; then
      echo "No encrypted database seed found" >&2
      exit 2
    fi
    ;;
  encrypt)
    test -s "$DB"
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -in "$DB" -out "$ENC" -pass env:STATE_ENCRYPTION_KEY
    ;;
  *) echo "usage: state.sh decrypt|encrypt" >&2; exit 2;;
esac
