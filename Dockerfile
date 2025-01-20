# ARG ARCH=amd64
FROM amd64/debian:bookworm-slim

# Install necessary packages, avoid installing unnecessary recommendations, and clean up after
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    locales \
    cups \
    cups-bsd \
    cups-filters \
    cups-pdf \
    cups-client \
    inotify-tools \
    avahi-daemon \
    avahi-utils \
    avahi-discover \
    python3 \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-cups \
    python3-avahi \
    python3-lxml \
    python3-dbus \
    python3-gi \
    python3-urllib3 \
    wget \
    rsync \
    foomatic-db-compressed-ppds \
    printer-driver-all \
    openprinting-ppds \
    hpijs-ppds \
    hp-ppd \
    hplip \
    printer-driver-splix \
    printer-driver-gutenprint \
    gutenprint-doc \
    gutenprint-locales \
    libgutenprint9 \
    libgutenprint-doc \
    ghostscript \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Expose CUPS and service directories
VOLUME ["/config", "/services"]

EXPOSE 631

# Add script to image and ensure it's executable
ADD app /app
RUN chmod +x /app/*

# Modify CUPS and Avahi configurations for remote access
RUN sed -i 's/Listen localhost:631/Listen *:631/' /etc/cups/cupsd.conf \
  && sed -i 's/Browsing No/Browsing On/' /etc/cups/cupsd.conf \
  && sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf \
  && sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All\n  Require user @SYSTEM/' /etc/cups/cupsd.conf \
  && sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf \
  && sed -i 's/.*enable\-dbus=.*/enable\-dbus=no/' /etc/avahi/avahi-daemon.conf \
  && echo "ServerAlias *" >> /etc/cups/cupsd.conf \
  && echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf \
  && echo "BrowseWebIF Yes" >> /etc/cups/cupsd.conf

# Set the default command
CMD ["/app/run.sh"]
