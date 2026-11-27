#!/usr/bin/env bash
# Build, test and deploy the Analysis Tracker with Docker Compose.
#
#   ./deploy.sh                 test + build + (re)start, then wait until healthy   (same as: deploy)
#   ./deploy.sh deploy --skip-tests
#   ./deploy.sh build           build the runtime image (tagged $IMAGE:$TAG and $IMAGE:<timestamp>)
#   ./deploy.sh test            run the test suite inside a throwaway build stage
#   ./deploy.sh up | down | restart | status | logs
#   ./deploy.sh backup          consistent DB snapshot + blobs -> ./backups/tracker-<time>.tar.gz
#   ./deploy.sh export          save the image to ./dist/<image>-<tag>.tar.gz (for a server without a registry)
#   ./deploy.sh load FILE       load an exported image on the target server
#
# Settings live in .env (created from .env.example on first run, with a random admin password).
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# --- docker access (falls back to sudo when the user is not in the docker group) ---------------
DOCKER=(docker)
init_docker() {
  if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null && sudo -n true 2>/dev/null || [ -t 0 ]; then
      warn "No access to the Docker daemon as $(id -un); using sudo. (Add yourself to the 'docker' group to avoid this.)"
      DOCKER=(sudo docker)
      "${DOCKER[@]}" info >/dev/null 2>&1 || die "Docker daemon is not reachable, even with sudo."
    else
      die "Docker daemon is not reachable. Is Docker running, and are you in the 'docker' group?"
    fi
  fi
  "${DOCKER[@]}" compose version >/dev/null 2>&1 || die "The Docker Compose plugin ('docker compose') is required."
}
compose() { "${DOCKER[@]}" compose --env-file .env "$@"; }

# --- .env -----------------------------------------------------------------------------------------
set_env() {  # set_env KEY VALUE  (replace the KEY= line in .env)
  local k=$1 v=$2
  if grep -q "^${k}=" .env; then sed -i "s|^${k}=.*|${k}=${v}|" .env; else echo "${k}=${v}" >> .env; fi
}

ensure_env() {
  if [ ! -f .env ]; then
    [ -f .env.example ] || die ".env.example is missing."
    cp .env.example .env
    chmod 600 .env
    local pw
    pw=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)
    set_env ADMIN_PASSWORD "$pw"
    log "Created .env with a random admin password (see ADMIN_PASSWORD in .env)."
    NEW_ENV=1
  fi
  set -a; . ./.env; set +a
  : "${IMAGE:=analysis-tracker}" "${TAG:=latest}" "${DATA_PATH:=./docker-data}" "${REPOS_PATH:=./repos}"
  : "${APP_PORT:=8000}" "${BIND_ADDR:=0.0.0.0}"
  [ -n "${ADMIN_PASSWORD:-}" ] || die "ADMIN_PASSWORD is empty in .env."
  [ "${#ADMIN_PASSWORD}" -ge 8 ] || die "ADMIN_PASSWORD in .env must be at least 8 characters."

  mkdir -p "$DATA_PATH" "$REPOS_PATH"
  # Run the container as the owner of the data directory, so files on the host stay accessible.
  if [ -z "${APP_UID:-}" ] || [ -z "${APP_GID:-}" ]; then
    APP_UID=$(stat -c %u "$DATA_PATH"); APP_GID=$(stat -c %g "$DATA_PATH")
    [ "$APP_UID" = 0 ] && { APP_UID=10001; APP_GID=10001; warn "Data dir is owned by root; running as 10001 (chown $DATA_PATH to 10001 if needed)."; }
    set_env APP_UID "$APP_UID"; set_env APP_GID "$APP_GID"
    export APP_UID APP_GID
  fi

  # Snap-packaged Docker: its AppArmor profile blocks every exec in a container started with
  # no-new-privileges, so the app would crash-loop. Turn the option off there, keep it elsewhere.
  if [ -z "${NO_NEW_PRIVILEGES:-}" ]; then
    if [[ "$(readlink -f "$(command -v docker)")" == */snap* ]]; then
      NO_NEW_PRIVILEGES=false
      warn "Snap-packaged Docker detected: running without no-new-privileges (AppArmor would block the container)."
    else
      NO_NEW_PRIVILEGES=true
    fi
    set_env NO_NEW_PRIVILEGES "$NO_NEW_PRIVILEGES"
    export NO_NEW_PRIVILEGES
  fi
}

# --- commands -------------------------------------------------------------------------------------
cmd_test() {
  log "Running the test suite in a build stage…"
  "${DOCKER[@]}" build --target test -t "${IMAGE}:test" .
  log "Tests passed."
}

cmd_build() {
  local stamp; stamp=$(date +%Y%m%d-%H%M%S)
  log "Building ${IMAGE}:${TAG} (also tagged ${IMAGE}:${stamp})…"
  "${DOCKER[@]}" build --target runtime -t "${IMAGE}:${TAG}" -t "${IMAGE}:${stamp}" .
}

wait_healthy() {
  local cid info health restarts state err i
  cid=$(compose ps -aq app)
  [ -n "$cid" ] || die "The app container was not created. Run: ./deploy.sh logs"
  log "Waiting for the app to become healthy…"
  for i in $(seq 1 60); do
    info=$("${DOCKER[@]}" inspect --format '{{.State.Health.Status}}|{{.RestartCount}}|{{.State.Status}}|{{.State.Error}}' "$cid" 2>/dev/null || echo "unknown|0|unknown|")
    IFS='|' read -r health restarts state err <<<"$info"
    if [ "$health" = healthy ]; then
      log "Up: http://localhost:${APP_PORT}"
      if [ "$BIND_ADDR" = 0.0.0.0 ]; then
        local ip
        for ip in $(hostname -I 2>/dev/null); do
          case $ip in 172.1[7-9].*|172.2[0-9].*|172.3[01].*|*:*) ;; *) log "On the local network: http://${ip}:${APP_PORT}" ;; esac
        done
      fi
      return 0
    fi
    # A crash loop never reaches "unhealthy", so stop as soon as the container has died or restarted.
    if [ "$health" = unhealthy ] || [ "${restarts:-0}" -gt 0 ] || [ "$state" = exited ] || [ "$state" = dead ]; then
      compose logs --tail 50 app || true
      [ -n "$err" ] && warn "Docker reports: $err"
      case $err in
        *"operation not permitted"*|*"permission denied"*)
          warn "If this is snap-packaged Docker, set NO_NEW_PRIVILEGES=false in .env and run ./deploy.sh up again." ;;
      esac
      compose stop app >/dev/null 2>&1 || true
      die "The app failed to start (state: $state, health: $health, restarts: $restarts). Stopped it to end the restart loop."
    fi
    sleep 2
  done
  compose logs --tail 50 app
  die "Timed out waiting for the app."
}

cmd_up() {
  log "Starting containers…"
  compose up -d --no-build --remove-orphans
  wait_healthy
  if [ "${NEW_ENV:-0}" = 1 ]; then
    log "First login: ${ADMIN_USERNAME:-admin} / ${ADMIN_PASSWORD}   (change it after logging in)"
  fi
}

cmd_deploy() {
  local skip=0
  for a in "$@"; do [ "$a" = --skip-tests ] && skip=1; done
  [ $skip = 1 ] || cmd_test
  cmd_build
  cmd_up
}

cmd_backup() {
  local stamp snap out
  stamp=$(date +%Y%m%d-%H%M%S)
  out="backups/tracker-${stamp}.tar.gz"
  mkdir -p backups
  snap="snapshot-${stamp}.sqlite3"
  if [ -n "$(compose ps -q --status running app 2>/dev/null)" ]; then
    # SQLite online-backup API: a consistent copy while the app keeps running.
    # Blobs are written before their DB rows and never change, so copying them afterwards is safe.
    log "Snapshotting the database from the running app…"
    compose exec -T app python -c \
      "import sqlite3,sys; s=sqlite3.connect('/data/tracker.sqlite3'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" \
      "/data/${snap}"
  else
    log "App is not running; copying the database file directly."
    cp "${DATA_PATH}/tracker.sqlite3" "${DATA_PATH}/${snap}"
  fi
  log "Writing ${out}…"
  tar -czf "$out" -C "$DATA_PATH" "$snap" blobs
  rm -f "${DATA_PATH}/${snap}"
  log "Backup done: ${out} ($(du -h "$out" | cut -f1)). Restore: stop the app, extract into DATA_PATH, rename the snapshot to tracker.sqlite3."
}

cmd_export() {
  mkdir -p dist
  local out="dist/${IMAGE//\//_}-${TAG}.tar.gz"
  log "Saving ${IMAGE}:${TAG} to ${out}…"
  "${DOCKER[@]}" save "${IMAGE}:${TAG}" | gzip > "$out"
  log "Copy ${out}, compose.yaml, .env.example and deploy.sh to the server, then run: ./deploy.sh load ${out} && ./deploy.sh up"
}

cmd_load() {
  [ -n "${1:-}" ] && [ -f "$1" ] || die "Usage: ./deploy.sh load dist/<image>.tar.gz"
  gunzip -c "$1" | "${DOCKER[@]}" load
}

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

main() {
  local cmd=${1:-deploy}; shift || true
  case $cmd in
    -h|--help|help) usage; exit 0 ;;
  esac
  init_docker
  ensure_env
  case $cmd in
    deploy)  cmd_deploy "$@" ;;
    build)   cmd_build ;;
    test)    cmd_test ;;
    up)      cmd_up ;;
    down)    compose down ;;
    restart) compose restart app; wait_healthy ;;
    status)  compose ps ;;
    logs)    compose logs -f --tail 200 app ;;
    backup)  cmd_backup ;;
    export)  cmd_export ;;
    load)    cmd_load "$@" ;;
    *) usage; die "Unknown command: $cmd" ;;
  esac
}

main "$@"
