# ARG ARCH=amd64
FROM amd64/debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    cups \
    cups-bsd \
    cups-filters \
    cups-client \
    cups-filters \
    ghostscript \
    avahi-daemon \
    avahi-utils \
    dbus \
    inotify-tools \
    python3 \
    python3-cups \
    python3-dbus \
    python3-gi \
    python3-avahi \
    ipp-usb \
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
    foomatic-db-compressed-ppds
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

VOLUME ["/config", "/services"]
EXPOSE 631

COPY app /app
RUN chmod +x /app/*

CMD ["/app/run.sh"]

