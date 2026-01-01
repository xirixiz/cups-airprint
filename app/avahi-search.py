#!/usr/bin/env python

"""
Search for printers that are announced over DNS-SD (aka Bonjour, Zeroconf, mDNS).

Use standalone to show DNS-SD printer properties.
Use as module to enumerate DNS-SD printers in your own code.

Used by a modified 'airprint-generate.py' to generate avahi .service files for
networked printers, allowing the printers to be announced on different subnets
by copying the .service files to /etc/avahi/services/ there.
This solves the problem of AirPrint printers not being availabe outside the
local subnet even though routing/NAT exists between subnets.

Copyright (c) 2013 Vidar Tysse <news@vidartysse.net>
Licence: Unlimited use is allowed. Including this copyright notice is requested.

***
Restructered and modernized code: Copyright (c) 2025 Bram van Dartel <xirixiz@gmail.com>
***
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Iterable

try:
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    import avahi
    from gi.repository import GLib
    HAVE_AVAHI = True
except Exception:
    HAVE_AVAHI = False


@dataclass
class PrinterService:
    name: str
    host: str
    address: str
    port: int
    domain: str
    txt: Dict[str, str]
    stype: str


class AvahiPrinterFinder:
    """
    Discovers printers using Avahi DNS SD via dbus.

    It browses both ipp and ipps because some devices only advertise secure IPP.
    """

    SERVICE_TYPES = ("_ipp._tcp", "_ipps._tcp")
    DEFAULT_TIMEOUT_SEC = 2.0

    def __init__(
        self,
        ipv4_only: bool = True,
        search_domain: str = "local",
        verbose: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ):
        if not HAVE_AVAHI:
            raise ImportError("Missing python3 dbus, python3 gi, python3 avahi, or avahi daemon")

        self.search_protocol = avahi.PROTO_INET if ipv4_only else avahi.PROTO_UNSPEC
        self.search_domain = search_domain
        self.timeout = float(timeout)

        self.logger = logging.getLogger("avahisearch")
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(message)s",
        )

        self._main_loop: Optional[GLib.MainLoop] = None
        self._server: Optional[dbus.Interface] = None
        self._receiving_events = False
        self._results: List[PrinterService] = []

    @staticmethod
    def _txt_to_dict(txt_records) -> Dict[str, str]:
        try:
            arr = avahi.txt_array_to_string_array(txt_records)
        except Exception:
            arr = []

        out: Dict[str, str] = {}
        for item in arr:
            if not isinstance(item, str):
                item = str(item)
            if "=" in item:
                k, v = item.split("=", 1)
            else:
                k, v = item, ""
            out[k] = v
        return out

    def _on_item_new(self, stype: str) -> Callable:
        def handler(interface, protocol, name, _stype, domain, _flags):
            self._receiving_events = True
            try:
                resolved = self._server.ResolveService(
                    interface,
                    protocol,
                    name,
                    stype,
                    domain,
                    self.search_protocol,
                    dbus.UInt32(0),
                )

                _, _, r_name, r_stype, r_domain, r_host, _, r_address, r_port, r_txt, _ = resolved

                self._results.append(
                    PrinterService(
                        name=str(r_name),
                        host=str(r_host),
                        address=str(r_address),
                        port=int(r_port),
                        domain=str(r_domain),
                        txt=self._txt_to_dict(r_txt),
                        stype=str(r_stype),
                    )
                )
            except dbus.DBusException as e:
                self.logger.debug(f"Resolve failed for {name}: {e}")
            except Exception as e:
                self.logger.debug(f"Unexpected resolve error for {name}: {e}")

        return handler

    def _on_all_for_now(self):
        if self._main_loop:
            self._main_loop.quit()

    def _timeout_tick(self) -> bool:
        if not self._receiving_events:
            if self._main_loop:
                self._main_loop.quit()
            return False
        self._receiving_events = False
        return True

    def search(self) -> List[PrinterService]:
        if not HAVE_AVAHI:
            return []

        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        self._server = dbus.Interface(
            bus.get_object(avahi.DBUS_NAME, "/"),
            "org.freedesktop.Avahi.Server",
        )

        browsers = []
        for stype in self.SERVICE_TYPES:
            path = self._server.ServiceBrowserNew(
                avahi.IF_UNSPEC,
                self.search_protocol,
                stype,
                self.search_domain,
                dbus.UInt32(0),
            )
            browser = dbus.Interface(
                bus.get_object(avahi.DBUS_NAME, path),
                avahi.DBUS_INTERFACE_SERVICE_BROWSER,
            )
            browser.connect_to_signal("ItemNew", self._on_item_new(stype))
            browser.connect_to_signal("AllForNow", lambda: self._on_all_for_now())
            browsers.append(browser)

        GLib.timeout_add(int(self.timeout * 1000), self._timeout_tick)

        self._main_loop = GLib.MainLoop()
        self._main_loop.run()

        dedup = {}
        for p in self._results:
            key = (p.name, p.host, p.port, p.stype)
            dedup[key] = p

        return list(dedup.values())
