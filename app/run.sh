#!/bin/sh

# Exit immediately on error and print each command before execution
set -e
set -x

# Set CUPSADMIN to 'admin' if not provided
CUPSADMIN="${CUPSADMIN:-admin}"

# Set CUPSPASSWORD to the value of CUPSADMIN if not provided
CUPSPASSWORD="${CUPSPASSWORD:-$CUPSADMIN}"

CUPSMODE="${CUPSMODE:-cups}"

# Check if the user exists in /etc/shadow
if ! grep -qi "^${CUPSADMIN}:" /etc/shadow; then
    # Add user to the 'lpadmin' group without creating a home directory
    useradd -r -G lpadmin -M "$CUPSADMIN"

    # Set the password for the CUPSADMIN user
    echo "$CUPSADMIN:$CUPSPASSWORD" | chpasswd
fi

# Create necessary directories if they don't exist
mkdir -p /config/ppd /services

# Clean existing Avahi services and CUPS PPDs
rm -rf /etc/avahi/services/*
rm -rf /etc/cups/ppd

# Symlink PPD directory from config to CUPS
ln -s /config/ppd /etc/cups

# Copy existing Avahi services if they exist
if [ -n "$(ls -A /services/*.service 2>/dev/null)" ]; then
    cp -f /services/*.service /etc/avahi/services/
fi

# Ensure printers.conf exists in /config and copy it to /etc/cups
[ -f /config/printers.conf ] || touch /config/printers.conf
cp /config/printers.conf /etc/cups/printers.conf

# Copy cupsd.conf from config if it exists
if [ -f /config/cupsd.conf ]; then
    cp /config/cupsd.conf /etc/cups/cupsd.conf
fi

# Start Avahi daemon in the background
/usr/sbin/avahi-daemon --daemonize

while [ ! -f /var/run/avahi-daemon/pid ]; do 
	"Waiting for the avahi-daemon to be started..."
	sleep 1;
done

# Background task to monitor CUPS configuration changes and regenerate AirPrint services
(
    /usr/bin/inotifywait -m -e close_write,moved_to,create /etc/cups | while read -r directory events filename; do
        if [ "$filename" = "printers.conf" ]; then
            rm -rf /services/AirPrint-*.service
            /app/airprint-generate.py --"$CUPSMODE" -d /services
            cp /etc/cups/printers.conf /config/printers.conf
            rsync -avh /services/ /etc/avahi/services/
            chmod 755 /var/cache/cups
            rm -rf /var/cache/cups/*
        fi
        if [ "$filename" = "cupsd.conf" ]; then
            cp /etc/cups/cupsd.conf /config/cupsd.conf
        fi
    done
) &

# Start CUPS in the foreground
exec /usr/sbin/cupsd -f
