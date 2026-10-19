#!/usr/bin/env bash
# A local test site: the built web app and the sample datasets, served by a real aibi-server on
# loopback, for trying the catalogue, the builder and the result page in a browser.
#
#   scripts/testsite.sh start    # build, configure, serve, import; prints the address
#   scripts/testsite.sh status
#   scripts/testsite.sh stop
#   scripts/testsite.sh reset    # stop, and delete the site's data (the token is kept)
#
# Everything the site holds lives in one directory, AIBI_TESTSITE_DIR (default ~/aibi-testsite, mode
# 0700). The curator token (the operator's key, SPEC §11.2) is written there once, to `curator-token`
# (mode 0600), and is never printed. The server binds 127.0.0.1 only, which needs no TLS (SPEC §14):
# to reach it from another machine, use an SSH tunnel (ssh -L 8000:127.0.0.1:8000 host); do not
# change `bind` without TLS and a proxy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="${AIBI_TESTSITE_DIR:-$HOME/aibi-testsite}"
PORT="${AIBI_TESTSITE_PORT:-8000}"
DATASETS=(orchard hostile_values)   # fixtures/<name>/*.csv, imported under that name
SERVER_DIR="$ROOT/server"
WEB_DIR="$ROOT/web"
ORIGIN="http://127.0.0.1:$PORT"
PIDFILE="$SITE/server.pid"
LOGFILE="$SITE/server.log"

die() { echo "testsite: $*" >&2; exit 1; }

running() {
  [[ -f "$PIDFILE" ]] || return 1
  local pid; pid="$(cat "$PIDFILE")"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_server() {
  if running; then
    local pid; pid="$(cat "$PIDFILE")"
    kill "$pid"
    for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    echo "stopped the server (pid $pid)"
  fi
  rm -f "$PIDFILE"
}

build() {
  command -v uv >/dev/null || die "uv is required (https://docs.astral.sh/uv/)"
  command -v node >/dev/null || die "node is required (version in web/.nvmrc)"
  (cd "$SERVER_DIR" && uv sync --quiet)
  # The bundle is checked at start: group and others must not be able to write it (umask 022).
  (umask 022 && cd "$WEB_DIR" && npm ci --ignore-scripts --silent && npm run build --silent)
}

make_token() {
  [[ -f "$SITE/curator-token" && -f "$SITE/token-hash" ]] && return 0
  local out hash token
  out="$("$SERVER_DIR/.venv/bin/aibi-server" new-token)"
  hash="$(sed -n 's/^token_hash = "\(sha256:[0-9a-f]\{64\}\)"$/\1/p' <<<"$out" | head -n1)"
  token="$(grep -Eo 'aibi_[A-Za-z0-9_-]{43}' <<<"$out" | head -n1)"
  [[ -n "$hash" && -n "$token" ]] || die "aibi-server new-token gave no token and hash (its output is not shown)"
  (umask 077 && printf '%s\n' "$token" > "$SITE/curator-token" && printf '%s\n' "$hash" > "$SITE/token-hash")
}

write_config() {
  local hash; hash="$(cat "$SITE/token-hash")"
  mkdir -p "$SITE/data" "$SITE/imports"
  (umask 077 && cat > "$SITE/aibi.toml" <<EOF
[server]
bind = "127.0.0.1"
port = $PORT
web_bundle = "$WEB_DIR/dist"

[curator]
token_hash = "$hash"

[storage]
data = "$SITE/data"
imports = ["$SITE/imports"]
EOF
  )
}

wait_ready() {
  for _ in $(seq 1 300); do
    running || die "the server exited before serving; see $LOGFILE"
    [[ "$(curl -s -o /dev/null -w '%{http_code}' "$ORIGIN/" || true)" == "200" ]] && return 0
    sleep 0.3
  done
  die "the server did not serve $ORIGIN in time; see $LOGFILE"
}

import_datasets() {
  local name
  for name in "${DATASETS[@]}"; do
    local target="$SITE/imports/$name"
    if [[ -d "$SITE/data" ]] && AIBI_TOKEN="$(cat "$SITE/curator-token")" AIBI_OPERATOR="testsite" AIBI_SERVER="$ORIGIN" \
        "$SERVER_DIR/.venv/bin/aibi" status 2>/dev/null | grep -q "^$name\b"; then
      echo "$name is already imported"
      continue
    fi
    rm -rf "$target" && mkdir -p "$target"
    cp "$ROOT/fixtures/$name/"*.csv "$target/"
    AIBI_TOKEN="$(cat "$SITE/curator-token")" AIBI_OPERATOR="testsite" AIBI_SERVER="$ORIGIN" \
      "$SERVER_DIR/.venv/bin/aibi" import "$name" "$target"
  done
}

cmd_start() {
  mkdir -p "$SITE" && chmod 700 "$SITE"
  if running; then echo "already running: $ORIGIN"; return 0; fi
  build
  make_token
  write_config
  "$SERVER_DIR/.venv/bin/aibi-server" check --config "$SITE/aibi.toml" >/dev/null
  (cd "$SITE" && nohup "$SERVER_DIR/.venv/bin/aibi-server" serve --config "$SITE/aibi.toml" >>"$LOGFILE" 2>&1 & echo $! > "$PIDFILE")
  wait_ready
  import_datasets
  cat <<EOF

The test site is up:  $ORIGIN/
  datasets:           ${DATASETS[*]}
  stop it:            scripts/testsite.sh stop
  log:                $LOGFILE
  curator token:      $SITE/curator-token (mode 0600; paste its contents where the operator screen asks)
EOF
}

cmd_status() {
  if running; then echo "running (pid $(cat "$PIDFILE")): $ORIGIN/"; else echo "not running"; fi
}

cmd_reset() {
  stop_server
  rm -rf "$SITE/data" "$SITE/imports" "$SITE/aibi.toml"
  echo "deleted the site's data; the token is kept at $SITE/curator-token"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) stop_server ;;
  status) cmd_status ;;
  reset) cmd_reset ;;
  *) echo "usage: scripts/testsite.sh start|stop|status|reset" >&2; exit 2 ;;
esac
