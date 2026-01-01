#!/bin/bash
set -euo pipefail
[ "${DEBUG:-0}" = "1" ] && set -x

# -----------------------------
# Paths
# -----------------------------
CONFIG_DIR="/config"
SERVICES_DIR="/services"
AVAHI_SERVICES_DIR="/etc/avahi/services"
CUPS_DIR="/etc/cups"
APP_DIR="/app"
CACHE_DIR="/var/cache/cups"

DBUS_RUN_DIR="/run/dbus"
DBUS_SOCKET="${DBUS_RUN_DIR}/system_bus_socket"

AVAHI_RUN_DIR="/run/avahi-daemon"

# -----------------------------
# Environment defaults
# -----------------------------
CUPSADMIN="${CUPSADMIN:-admin}"
CUPSPASSWORD="${CUPSPASSWORD:-$CUPSADMIN}"
CUPSMODE="${CUPSMODE:-cups}"    # cups | dnssd
DEBUG="${DEBUG:-0}"

# -----------------------------
# Helpers
# -----------------------------
log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" >&2
}

cleanup() {
  log "Stopping services"
  pkill -TERM cupsd 2>/dev/null || true
  pkill -TERM avahi-daemon 2>/dev/null || true
  pkill -TERM dbus-daemon 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# -----------------------------
# Validation
# -----------------------------
validate_env() {
  case "$CUPSMODE" in
    cups|dnssd) ;;
    *)
      log "Invalid CUPSMODE='$CUPSMODE' (must be 'cups' or 'dnssd')"
      exit 2
      ;;
  esac
}

# -----------------------------
# CUPS setup
# -----------------------------
setup_cups_admin() {
  if ! id -u "$CUPSADMIN" >/dev/null 2>&1; then
    log "Creating CUPS admin user: $CUPSADMIN"
    useradd -r -G lpadmin -M "$CUPSADMIN"
    echo "$CUPSADMIN:$CUPSPASSWORD" | chpasswd
  fi
}

setup_directories() {
  log "Setting up directories"

  mkdir -p \
    "$CONFIG_DIR/ppd" \
    "$SERVICES_DIR" \
    "$AVAHI_SERVICES_DIR" \
    "$CACHE_DIR" \
    "$DBUS_RUN_DIR" \
    "$AVAHI_RUN_DIR"

  # Persist printers and PPDs
  touch "$CONFIG_DIR/printers.conf"
  ln -sfn "$CONFIG_DIR/printers.conf" "$CUPS_DIR/printers.conf"
  ln -sfn "$CONFIG_DIR/ppd" "$CUPS_DIR/ppd"

  # Optional override
  if [ -f "$CONFIG_DIR/cupsd.conf" ]; then
    ln -sfn "$CONFIG_DIR/cupsd.conf" "$CUPS_DIR/cupsd.conf"
  fi
}

# -----------------------------
# DBus
# -----------------------------
start_dbus_if_needed() {
  mkdir -p "$DBUS_RUN_DIR"

  # If socket exists, verify bus is responsive
  if [ -S "$DBUS_SOCKET" ]; then
    if dbus-send --system --dest=org.freedesktop.DBus \
        --type=method_call / org.freedesktop.DBus.ListNames \
        >/dev/null 2>&1; then
      log "dbus already running and responsive"
      return 0
    fi

    log "Stale dbus socket found, removing"
    rm -f "$DBUS_SOCKET"
  fi

  log "Starting dbus-daemon"
  dbus-daemon --system --fork

  local timeout=10
  local i=0
  while [ ! -S "$DBUS_SOCKET" ]; do
    if [ "$i" -ge "$timeout" ]; then
      log "ERROR: dbus socket did not appear"
      ls -la "$DBUS_RUN_DIR" || true
      return 1
    fi
    sleep 1
    i=$((i + 1))
  done

  # Final sanity check
  if ! dbus-send --system --dest=org.freedesktop.DBus \
      --type=method_call / org.freedesktop.DBus.ListNames \
      >/dev/null 2>&1; then
    log "ERROR: dbus started but not responding"
    return 1
  fi

  log "dbus ready"
}

# -----------------------------
# Avahi
# -----------------------------
ensure_avahi_user() {
  getent group avahi >/dev/null || groupadd -r avahi
  id -u avahi >/dev/null 2>&1 || useradd -r -g avahi -M -s /usr/sbin/nologin avahi
}

ensure_avahi_conf() {
  if [ ! -f /etc/avahi/avahi-daemon.conf ]; then
    log "Creating minimal avahi-daemon.conf"
    mkdir -p /etc/avahi
    cat > /etc/avahi/avahi-daemon.conf <<'EOF'
[server]
use-ipv4=yes
use-ipv6=no

[publish]
publish-addresses=yes
publish-workstation=no

[reflector]
enable-reflector=no
EOF
  fi
}

start_avahi() {
  log "Starting Avahi daemon"

  [ -S "$DBUS_SOCKET" ] || {
    log "ERROR: dbus socket missing, Avahi cannot start"
    return 1
  }

  ensure_avahi_user
  ensure_avahi_conf

  mkdir -p "$AVAHI_RUN_DIR" /etc/avahi/services
  chown -R avahi:avahi "$AVAHI_RUN_DIR" /etc/avahi 2>/dev/null || true

  pkill -TERM avahi-daemon 2>/dev/null || true
  sleep 1

  # DEBUG: foreground probe to show real error
  if [ "$DEBUG" = "1" ]; then
    log "DEBUG=1: running avahi-daemon in foreground"
    timeout 5 /usr/sbin/avahi-daemon \
      --debug --no-drop-root --no-chroot || true
    pkill -TERM avahi-daemon 2>/dev/null || true
    sleep 1
  fi

  /usr/sbin/avahi-daemon --daemonize --no-drop-root --no-chroot

  local timeout=10
  local i=0
  while ! pgrep -x avahi-daemon >/dev/null 2>&1; do
    if [ "$i" -ge "$timeout" ]; then
      log "ERROR: avahi-daemon exited immediately"
      ps aux | grep -E 'avahi|dbus' || true
      return 1
    fi
    sleep 1
    i=$((i + 1))
  done

  log "Avahi started"
}

# -----------------------------
# AirPrint services
# -----------------------------
sync_services_to_avahi() {
  rm -f "$AVAHI_SERVICES_DIR"/*.service 2>/dev/null || true
  if compgen -G "$SERVICES_DIR"/*.service >/dev/null; then
    cp -f "$SERVICES_DIR"/*.service "$AVAHI_SERVICES_DIR"/
  fi
  avahi-daemon --reload 2>/dev/null || true
}

regenerate_airprint_services() {
  rm -f "$SERVICES_DIR"/AirPrint-*.service 2>/dev/null || true

  if [ "$CUPSMODE" = "cups" ]; then
    "$APP_DIR/airprint-generate.py" --cups -d "$SERVICES_DIR" \
      $([ "$DEBUG" = "1" ] && echo "--debug")
  else
    "$APP_DIR/airprint-generate.py" --dnssd -d "$SERVICES_DIR" \
      $([ "$DEBUG" = "1" ] && echo "--debug")
  fi

  sync_services_to_avahi
  rm -rf "$CACHE_DIR"/* 2>/dev/null || true
}

monitor_cups_changes() {
  log "Monitoring CUPS configuration"
  inotifywait -m -e close_write,moved_to,create "$CUPS_DIR" |
  while read -r _dir _events file; do
    case "$file" in
      printers.conf)
        log "printers.conf changed"
        regenerate_airprint_services || true
        ;;
      cupsd.conf)
        log "cupsd.conf changed"
        ;;
    esac
  done
}

# -----------------------------
# Main
# -----------------------------
main() {
  validate_env
  setup_cups_admin
  setup_directories
  start_dbus_if_needed

  local tries=0
  until start_avahi; do
    tries=$((tries + 1))
    [ "$tries" -ge 3 ] && {
      log "Avahi failed after 3 attempts"
      exit 1
    }
    log "Retrying Avahi startup ($tries/3)"
    sleep 2
  done

  sync_services_to_avahi
  monitor_cups_changes &

  log "Starting CUPS daemon"
  exec /usr/sbin/cupsd -f
}

main

