#!/usr/bin/env bash
# A local test site: the built web app and the sample datasets, served by a real aibi-server on
# loopback, for trying the catalogue, the builder and the result page in a browser.
#
#   scripts/testsite.sh start    # build, configure, serve, import; prints the address
#   scripts/testsite.sh status
#   scripts/testsite.sh stop
#   scripts/testsite.sh reset    # stop, and delete the site's data (the token is kept)
#   scripts/testsite.sh import-study <name> <dir>   # a cBioPortal study, by the oncology pack
#
# The server installs the oncology pack (`[packs] modules`), so a cBioPortal study directory can be
# imported with it: `import-study` copies the directory whole into the site's `imports/<name>` and
# imports it as the dataset <name> (`aibi import <name> <dir> --pack onco`, or `reimport` when the
# dataset exists), restarting the server first if it is running, so that it serves this script's
# configuration. <name> is a dataset id (`[a-z][a-z0-9_]{0,63}`, no `__`); the target must be the
# site's own `imports/<name>`, with no symlink on the way, and the source must not be that target,
# hold it or lie inside it (compared by realpath). The name, the source and the target are checked
# before anything is stopped, built, created or removed, and again before the target is.
#
# Everything the site holds lives in one directory, AIBI_TESTSITE_DIR (default ~/aibi-testsite, mode
# 0700). The curator token (the operator's key, SPEC §11.2) is written there once, to `curator-token`
# (mode 0600), and is never printed. The server binds 127.0.0.1 only, which needs no TLS (SPEC §14):
# to reach it from another machine, use an SSH tunnel (ssh -L 8000:127.0.0.1:8000 host); do not
# change `bind` without TLS and a proxy.
set -euo pipefail

ROOT="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
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
    [[ "$(curl -s -o /dev/null -w '%{http_code}' "$ORIGIN/" || true)" == "200" ]] \
      && echo "warning: something still answers at $ORIGIN (not the server this script started)" >&2
  fi
  rm -f "$PIDFILE"
}

build() {
  command -v uv >/dev/null || die "uv is required (https://docs.astral.sh/uv/)"
  command -v node >/dev/null || die "node is required (version in web/.nvmrc)"
  (CDPATH= cd -P -- "$SERVER_DIR" && uv sync --quiet)
  # The bundle is checked at start: group and others must not be able to write it (umask 022).
  (umask 022 && CDPATH= cd -P -- "$WEB_DIR" && npm ci --ignore-scripts --silent && npm run build --silent)
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

[packs]
modules = ["aibi.packs.onco"]
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
    local target; target="$(study_target "$name")"
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

aibi_cli() {
  AIBI_TOKEN="$(cat "$SITE/curator-token")" AIBI_OPERATOR="testsite" AIBI_SERVER="$ORIGIN" \
    "$SERVER_DIR/.venv/bin/aibi" "$@"
}

valid_name() {
  local name="$1"
  LC_ALL=C
  [[ "$name" =~ ^[a-z][a-z0-9_]{0,63}$ ]] || return 1
  [[ "$name" != *__* ]] || return 1
}

# The site's own imports/<name>, refused unless the name is a dataset id and the target is exactly
# that path under the site's real path, with no symlink among its components below the site. With
# a second argument (`early`), a site that has no imports directory yet passes: nothing is there to
# follow, and the check is made again once the site is up. Nothing is removed, created or built.
study_target() {
  local name="$1" early="${2:-}" site_real imports target
  ( valid_name "$name" ) || die "a dataset name is [a-z][a-z0-9_]{0,63} with no '__'"
  site_real="$(realpath -m -- "$SITE")" || die "the site's directory cannot be resolved"
  imports="$SITE/imports"
  [[ -L "$imports" ]] && die "the site's imports directory is a symlink; refusing"
  if [[ -e "$imports" ]]; then
    [[ -d "$imports" ]] || die "the site's imports is not a directory"
    [[ "$(realpath -e -- "$imports")" == "$site_real/imports" ]] || die "the site's imports directory is not its own"
  elif [[ -z "$early" ]]; then
    die "the site has no imports directory"
  fi
  target="$imports/$name"
  [[ -L "$target" ]] && die "the import target is a symlink; refusing"
  [[ "$(realpath -m -- "$target")" == "$site_real/imports/$name" ]] || die "the import target is not the site's own imports/$name"
  printf '%s\n' "$target"
}

cmd_import_study() {
  local name="${1:-}" source="${2:-}" target
  [[ -n "$name" && -n "$source" ]] || die "usage: scripts/testsite.sh import-study <name> <dir>"
  [[ -d "$source" ]] || die "not a directory: $source"
  source="$(realpath -e -- "$source")" || die "the source cannot be resolved: $source"
  target="$(study_target "$name" early)"
  # The target is removed before the copy, so a source that is it, holds it or lies in it would be
  # removed with it (or copied into itself): refused here, before anything is stopped or removed.
  local target_real; target_real="$(realpath -m -- "$target")"
  [[ "$source/" != "$target_real/"* && "$target_real/" != "${source%/}/"* ]] \
    || die "the source $source is, holds or lies inside the import target $target_real; refusing"
  if running; then stop_server; fi
  cmd_start
  target="$(study_target "$name")"
  rm -rf -- "$target"
  mkdir -p -- "$target"
  cp -R -- "$source/." "$target/"
  if aibi_cli status 2>/dev/null | grep -q "^$name\b"; then
    aibi_cli reimport "$name" "$target" --pack onco
  else
    aibi_cli import "$name" "$target" --pack onco
  fi
  echo "imported $name from $source (the oncology pack): $ORIGIN/"
}

# The pidfile holds the server's own pid: `cd` runs before the background command, so `$!` is the
# `nohup` that becomes the server. (`cd … && nohup … &` backgrounds the whole list, a subshell, and
# `$!` is that subshell: `stop` killed it and the server kept the port.)
launch_server() {
  (
    CDPATH= cd -P -- "$SITE" || exit 1
    nohup "$SERVER_DIR/.venv/bin/aibi-server" serve --config "$SITE/aibi.toml" >>"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
  )
}

cmd_start() {
  mkdir -p "$SITE" && chmod 700 "$SITE"
  if running; then echo "already running: $ORIGIN"; return 0; fi
  build
  make_token
  write_config
  "$SERVER_DIR/.venv/bin/aibi-server" check --config "$SITE/aibi.toml" >/dev/null
  launch_server
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

# Sourced (by the tests), the script defines its functions and runs nothing.
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0
case "${1:-}" in
  start) cmd_start ;;
  stop) stop_server ;;
  status) cmd_status ;;
  reset) cmd_reset ;;
  import-study) shift; cmd_import_study "$@" ;;
  *) echo "usage: scripts/testsite.sh start|stop|status|reset|import-study <name> <dir>" >&2; exit 2 ;;
esac
