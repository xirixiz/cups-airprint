#!/bin/bash

# Strict error handling and debugging
set -euo pipefail
[ "${DEBUG:-0}" = "1" ] && set -x

# Configuration
readonly CONFIG_DIR="/config"
readonly SERVICES_DIR="/services"
readonly AVAHI_SERVICES_DIR="/etc/avahi/services"
readonly CUPS_DIR="/etc/cups"
readonly APP_DIR="/app"
readonly CACHE_DIR="/var/cache/cups"
readonly AVAHI_PID="/run/avahi-daemon/pid"

# Default values for environment variables
CUPSADMIN="${CUPSADMIN:-admin}"
CUPSPASSWORD="${CUPSPASSWORD:-$CUPSADMIN}"
CUPSMODE="${CUPSMODE:-cups}"

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" >&2
}

# Function to handle errors
error_handler() {
    local line_no=$1
    local command=$2
    local ret_code=$3
    log "Error occurred in command '$command' (line $line_no, exit code $ret_code)"
    exit $ret_code
}

trap 'error_handler ${LINENO} "${BASH_COMMAND}" $?' ERR

# Function to setup CUPS admin user
setup_cups_admin() {
    if ! grep -qi "^${CUPSADMIN}:" /etc/shadow; then
        log "Creating CUPS admin user: $CUPSADMIN"
        useradd -r -G lpadmin -M "$CUPSADMIN"
        echo "$CUPSADMIN:$CUPSPASSWORD" | chpasswd
    fi
}

# Function to setup directories and symlinks
setup_directories() {
    log "Setting up directories and symlinks"
    mkdir -p "$CONFIG_DIR/ppd" "$SERVICES_DIR"
    ln -sfn "$CONFIG_DIR/ppd" "$CUPS_DIR/ppd"
    rm -f "$AVAHI_SERVICES_DIR"/*.service 2>/dev/null || true
}

# Function to setup configuration files
setup_config_files() {
    log "Setting up configuration files"

    # Initialize printers.conf if it doesn't exist
    touch "$CONFIG_DIR/printers.conf"
    cp "$CONFIG_DIR/printers.conf" "$CUPS_DIR/printers.conf"

    # Copy cupsd.conf if it exists
    if [ -f "$CONFIG_DIR/cupsd.conf" ]; then
        cp "$CONFIG_DIR/cupsd.conf" "$CUPS_DIR/cupsd.conf"
    fi

    # Copy existing service files
    if compgen -G "$SERVICES_DIR/*.service" > /dev/null; then
        cp -f "$SERVICES_DIR"/*.service "$AVAHI_SERVICES_DIR"/
    fi
}

# Function to setup Avahi
setup_avahi() {
    log "Setting up Avahi daemon"
    mkdir -p /run/avahi-daemon
    chown avahi:avahi /run/avahi-daemon
    chown -R avahi:avahi /etc/avahi
}

# Function to start Avahi daemon
start_avahi() {
    log "Starting Avahi daemon"
    [ "${DEBUG:-0}" = "1" ] && log "Running avahi-daemon --daemonize"

    # Kill any existing Avahi processes
    pkill avahi-daemon || true

    # Remove existing PID file if it exists
    rm -f "$AVAHI_PID"

    # Start Avahi daemon with error handling
    if ! /usr/sbin/avahi-daemon --daemonize; then
        log "Failed to start Avahi daemon"
        if [ "${DEBUG:-0}" = "1" ]; then
            log "Avahi daemon status:"
            systemctl status avahi-daemon || true
            log "Avahi daemon logs:"
            journalctl -u avahi-daemon -n 50 || true
        fi
        return 1
    fi

    local timeout=30
    local counter=0
    while [ ! -f "$AVAHI_PID" ]; do
        if [ $counter -ge $timeout ]; then
            log "Error: Avahi daemon failed to start within $timeout seconds"
            if [ "${DEBUG:-0}" = "1" ]; then
                log "Checking Avahi process status:"
                ps aux | grep avahi
                log "Checking /run/avahi-daemon directory:"
                ls -la /run/avahi-daemon/
            fi
            return 1
        fi
        log "Waiting for Avahi daemon to start... ($counter/$timeout)"
        sleep 1
        ((counter++))
    done

    # Verify the daemon is actually running
    if ! pgrep -x avahi-daemon > /dev/null; then
        log "Error: Avahi daemon process not found after startup"
        return 1
    fi

    # Give the daemon a moment to initialize
    sleep 2

    [ "${DEBUG:-0}" = "1" ] && log "Avahi daemon started successfully"
    return 0
}

# Function to handle CUPS configuration changes
handle_cups_changes() {
    local directory=$1
    local events=$2
    local filename=$3

    case "$filename" in
        "printers.conf")
            log "Printer configuration changed, regenerating AirPrint services"
            rm -rf "$SERVICES_DIR"/AirPrint-*.service
            if [ "$CUPSMODE" = "cups" ]; then
                [ "${DEBUG:-0}" = "1" ] && log "Running airprint-generate.py with --cups"
                "$APP_DIR/airprint-generate.py" --cups -d "$SERVICES_DIR" $([ "${DEBUG:-0}" = "1" ] && echo "--debug")
            elif [ "$CUPSMODE" = "dnssd" ]; then
                [ "${DEBUG:-0}" = "1" ] && log "Running airprint-generate.py with --dnssd"
                "$APP_DIR/airprint-generate.py" --dnssd -d "$SERVICES_DIR" $([ "${DEBUG:-0}" = "1" ] && echo "--debug")
            else
                echo "Error: Invalid CUPSMODE value: $CUPSMODE (should be 'cups' or 'dnssd')" >&2
                return 1
            fi
            cp "$CUPS_DIR/printers.conf" "$CONFIG_DIR/printers.conf"
            rsync -a --delete "$SERVICES_DIR/" "$AVAHI_SERVICES_DIR/"
            chmod 755 "$CACHE_DIR"
            rm -rf "$CACHE_DIR"/*
            ;;
        "cupsd.conf")
            log "CUPS daemon configuration changed"
            cp "$CUPS_DIR/cupsd.conf" "$CONFIG_DIR/cupsd.conf"
            ;;
    esac
}

# Function to monitor CUPS configuration changes
monitor_cups_changes() {
    log "Starting CUPS configuration monitor"
    /usr/bin/inotifywait -m -e close_write,moved_to,create "$CUPS_DIR" | while read -r directory events filename; do
        handle_cups_changes "$directory" "$events" "$filename"
    done
}

# Main execution
main() {
    [ "${DEBUG:-0}" = "1" ] && log "Starting main execution with DEBUG=1"
    [ "${DEBUG:-0}" = "1" ] && log "CUPSMODE=$CUPSMODE"
    [ "${DEBUG:-0}" = "1" ] && log "CUPSADMIN=$CUPSADMIN"

    log "Starting CUPS configuration"
    setup_cups_admin
    setup_directories
    setup_config_files
    setup_avahi

    # Try to start Avahi with retries
    local max_retries=3
    local retry_count=0
    while [ $retry_count -lt $max_retries ]; do
        if start_avahi; then
            break
        fi
        retry_count=$((retry_count + 1))
        if [ $retry_count -lt $max_retries ]; then
            log "Retrying Avahi startup ($retry_count/$max_retries)..."
            sleep 5
        else
            log "Failed to start Avahi daemon after $max_retries attempts"
            exit 1
        fi
    done

    monitor_cups_changes &

    log "Starting CUPS daemon"
    [ "${DEBUG:-0}" = "1" ] && log "Running cupsd -f"
    exec /usr/sbin/cupsd -f
}

# Start the script
main
