FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    cups \
    cups-bsd \
    cups-client \
    cups-filters \
    ghostscript \
    foomatic-filters \
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
  && rm -rf /var/lib/apt/lists/*
  
VOLUME ["/config", "/services"]
EXPOSE 631

COPY app /app
RUN chmod +x /app/*

CMD ["/app/run.sh"]
