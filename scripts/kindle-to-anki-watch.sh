#!/bin/zsh
# Run one safe synchronization attempt. launchd invokes this periodically.

set -u

REPO_DIR="${KINDLE_TO_ANKI_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
KINDLE_DB="${KINDLE_TO_ANKI_DB:-/Volumes/Kindle/System/vocabulary/vocab.db}"
DECK="${KINDLE_TO_ANKI_DECK:-Kindle words}"
CHECKPOINT="${KINDLE_TO_ANKI_CHECKPOINT:-$HOME/.kindle-to-anki}"
BATCH_SIZE="${KINDLE_TO_ANKI_BATCH_SIZE:-5}"
TIMEOUT="${KINDLE_TO_ANKI_TIMEOUT:-60}"
ANKI_URL="${KINDLE_TO_ANKI_ANKI_URL:-http://localhost:8765}"
KEYCHAIN_SERVICE="${KINDLE_TO_ANKI_KEYCHAIN_SERVICE:-kindle-to-anki-mw-api-key}"
LOCK_DIR="${TMPDIR:-/tmp}/kindle-to-anki.lock"

log() {
  print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] $*"
}

if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap '/bin/rm -rf "$LOCK_DIR"' EXIT INT TERM

if [[ ! -f "$KINDLE_DB" ]]; then
  exit 0
fi

# Do not read a database while macOS is still mounting/copying the volume.
size_before=$(/usr/bin/stat -f '%z' "$KINDLE_DB" 2>/dev/null || print -r -- '')
/bin/sleep 1
size_after=$(/usr/bin/stat -f '%z' "$KINDLE_DB" 2>/dev/null || print -r -- '')
if [[ -z "$size_before" || "$size_before" != "$size_after" ]]; then
  log "Kindle database is still changing; will retry later."
  exit 0
fi

anki_available() {
  /usr/bin/curl -fsS --max-time 3 "$ANKI_URL" \
    -H 'Content-Type: application/json' \
    --data '{"action":"version","version":5}' 2>/dev/null | /usr/bin/grep -q '"error":null'
}

if ! anki_available; then
  if ! /usr/bin/pgrep -x Anki >/dev/null 2>&1; then
    /usr/bin/open -a Anki >/dev/null 2>&1 || true
  fi
  for _ in {1..20}; do
    if anki_available; then
      break
    fi
    /bin/sleep 1
  done
fi

if ! anki_available; then
  log "AnkiConnect is unavailable; will retry later."
  exit 0
fi

api_key=$(/usr/bin/security find-generic-password \
  -a "$USER" -s "$KEYCHAIN_SERVICE" -w 2>/dev/null || print -r -- '')
if [[ -z "$api_key" ]]; then
  log "No Merriam-Webster API key in Keychain service '$KEYCHAIN_SERVICE'."
  exit 1
fi

log "Synchronizing Kindle vocabulary into '$DECK'."
/usr/bin/python3 "$REPO_DIR/kindle_to_anki.py" \
  --kindle-db "$KINDLE_DB" \
  --deck "$DECK" \
  --mw-api-key "$api_key" \
  --checkpoint "$CHECKPOINT" \
  --anki-url "$ANKI_URL" \
  --batch-size "$BATCH_SIZE" \
  --timeout "$TIMEOUT"
