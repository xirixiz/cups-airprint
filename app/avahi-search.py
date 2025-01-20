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

import logging
from typing import List
from dataclasses import dataclass
from argparse import ArgumentParser
import logging

try:
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    import avahi
    from gi.repository import GLib
    HAVE_AVAHI = True
except ImportError:
    HAVE_AVAHI = False

@dataclass
class PrinterService:
    """Represents a discovered network printer"""
    name: str
    host: str
    address: str
    port: int
    domain: str
    txt: dict  # Dict[str, str] but simplified since we don't need the full annotation

class AvahiPrinterFinder:
    """Discovers network printers using Avahi/DNS-SD"""

    SERVICE_TYPE = '_ipp._tcp'  # Standard IPP printer service type
    BROWSE_TIMEOUT = 2  # Timeout in seconds for service browsing

    def __init__(
        self,
        ipv4_only: bool = True,
        search_domain: str = 'local',
        verbose: bool = False,
        timeout: float = BROWSE_TIMEOUT
    ):
        """
        Initialize the printer finder

        Args:
            ipv4_only: If True, only search for IPv4 printers
            search_domain: Domain to search in
            verbose: Enable verbose logging
            timeout: Search timeout in seconds
        """
        if not HAVE_AVAHI:
            raise ImportError("Required packages not found. Please install: dbus-python, python-avahi, and pygobject")

        self.search_protocol = avahi.PROTO_INET if ipv4_only else avahi.PROTO_UNSPEC
        self.search_domain = search_domain
        self.timeout = timeout

        # Configure logging
        self.logger = logging.getLogger(__name__)
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=level, format='%(message)s')

        # Internal state
        self.receiving_events = False
        self.printers: List[PrinterService] = []
        self._main_loop: Optional[GLib.MainLoop] = None
        self._server: Optional[dbus.Interface] = None

    def _parse_txt_records(self, txt_array: List[str]) -> Dict[str, str]:
        """Convert Avahi TXT records array to dictionary"""
        txt_dict: Dict[str, str] = {}

        for txt in txt_array:
            if not isinstance(txt, str):
                txt = str(txt)
            try:
                key, *value_parts = txt.split('=', 1)
                value = value_parts[0] if value_parts else ''
                txt_dict[key] = value
            except Exception as e:
                self.logger.warning(f"Failed to parse TXT record '{txt}': {e}")

        return txt_dict

    def _handle_service_found(
        self,
        interface: int,
        protocol: int,
        name: str,
        stype: str,
        domain: str,
        _flags: int  # Unused but required by D-Bus signal
    ):
        """Handle discovery of a new service"""
        self.receiving_events = True
        self.logger.debug(f"Found service '{name}' type '{stype}' domain '{domain}'")

        try:
            # Resolve the service to get full details
            resolved = self._server.ResolveService(
                interface,
                protocol,
                name,
                stype,
                domain,
                self.search_protocol,
                dbus.UInt32(0)
            )

            # Unpack only the values we need
            _, _, r_name, _, r_domain, r_host, _, r_address, r_port, r_txt, _ = resolved

            self.logger.debug(f"Resolved: {r_host} - {r_name} - {r_address} - {r_port}")

            # Convert TXT records from Avahi format
            txt_array = avahi.txt_array_to_string_array(r_txt)
            txt_dict = self._parse_txt_records(txt_array)

            # Create printer service object
            printer = PrinterService(
                name=str(r_name),
                host=str(r_host),
                address=str(r_address),
                port=int(r_port),
                domain=str(r_domain),
                txt=txt_dict
            )

            self.printers.append(printer)

        except dbus.DBusException as e:
            self.logger.error(f"Failed to resolve service: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error resolving service: {e}")

    def _handle_all_for_now(self) -> None:
        """Handle completion of initial service discovery"""
        self.logger.debug("Initial service discovery complete")
        self._main_loop.quit()

    def _check_timeout(self) -> bool:
        """Check if we should stop searching for services"""
        if not self.receiving_events:
            self.logger.debug("No new services found, stopping search")
            self._main_loop.quit()
            return False

        self.receiving_events = False
        return True

    def search(self) -> List[PrinterService]:
        """
        Search for network printers

        Returns:
            List of discovered PrinterService objects

        Raises:
            dbus.DBusException: If there are D-Bus communication errors
        """
        if not HAVE_AVAHI:
            self.logger.error("Avahi support not available")
            return []

        try:
            # Initialize D-Bus connection
            loop = DBusGMainLoop()
            bus = dbus.SystemBus(mainloop=loop)

            # Get Avahi server interface
            self._server = dbus.Interface(
                bus.get_object(avahi.DBUS_NAME, '/'),
                'org.freedesktop.Avahi.Server'
            )

            # Create service browser
            browser_path = self._server.ServiceBrowserNew(
                avahi.IF_UNSPEC,
                self.search_protocol,
                self.SERVICE_TYPE,
                self.search_domain,
                dbus.UInt32(0)
            )

            browser = dbus.Interface(
                bus.get_object(avahi.DBUS_NAME, browser_path),
                avahi.DBUS_INTERFACE_SERVICE_BROWSER
            )

            # Set up signal handlers
            browser.connect_to_signal("ItemNew", self._handle_service_found)
            browser.connect_to_signal("AllForNow", self._handle_all_for_now)

            # Set up timeout
            GLib.timeout_add(int(self.timeout * 1000), self._check_timeout)

            # Run event loop
            self._main_loop = GLib.MainLoop()
            self._main_loop.run()

            return self.printers

        except dbus.DBusException as e:
            self.logger.error(f"D-Bus error: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            raise
        finally:
            self._main_loop = None
            self._server = None

def main() -> None:
    """Command line interface for printer discovery"""
    parser = ArgumentParser(description="Discover network printers using Avahi/DNS-SD")
    parser.add_argument('-v', '--verbose', action="store_true",
                      help="Enable verbose output")
    parser.add_argument('-t', '--timeout', type=float, default=2.0,
                      help="Search timeout in seconds")
    args = parser.parse_args()

    try:
        finder = AvahiPrinterFinder(verbose=args.verbose, timeout=args.timeout)
        printers = finder.search()

        for printer in printers:
            print(f"\n{printer.name}")
            print(f"  host       = {printer.host}")
            print(f"  address    = {printer.address}")
            print(f"  port       = {printer.port}")
            print(f"  domain     = {printer.domain}")
            print("  txt record:")
            for key, value in printer.txt.items():
                print(f"    {key} = {value}")
        print()

    except KeyboardInterrupt:
        print("\nSearch cancelled by user")
    except Exception as e:
        print(f"\nError: {e}")
        raise

if __name__ == '__main__':
    main()