#!/usr/bin/env python3

"""
Copyright (c) 2010 Timothy J Fontaine <tjfontaine@atxconsulting.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

***
Discovery by DNS-SD: Copyright (c) 2013 Vidar Tysse <news@vidartysse.net>
***

***
Update for Secure IPPS/HTTPS Printing and CUPS version 2.1: Copyright (c) 2016 Julian Pawlowski <julian.pawlowski@gmail.com>
***

***
Restructered and modernized code: Copyright (c) 2025 Bram van Dartel <xirixiz@gmail.com>
***
"""

import sys
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from urllib.parse import urlparse
from argparse import ArgumentParser
from xml.dom import minidom
from getpass import getpass
from io import StringIO

try:
    from lxml.etree import Element, ElementTree, tostring
    USING_LXML = True
except ImportError:
    try:
        from xml.etree.ElementTree import Element, ElementTree, tostring
        USING_LXML = False
    except ImportError:
        raise ImportError('Failed to find python lxml or elementtree. Please install one or use Python >= 3.6')

try:
    import cups
    HAS_CUPS = True
except ImportError:
    HAS_CUPS = False

try:
    import avahisearch
    HAS_AVAHI = True
except ImportError:
    HAS_AVAHI = False

# Constants
XML_TEMPLATE = """<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
<name replace-wildcards="yes"></name>
<service>
    <type>_ipps._tcp</type>
    <subtype>_universal._sub._ipps._tcp</subtype>
    <port>631</port>
    <txt-record>txtvers=1</txt-record>
    <txt-record>qtotal=1</txt-record>
    <txt-record>Transparent=T</txt-record>
    <txt-record>URF=DM3</txt-record>
    <txt-record>TLS=1.2</txt-record>
</service>
</service-group>"""

ALLOWED_DOCUMENT_TYPES = {
    'application/pdf': True,
    'application/postscript': True,
    'application/vnd.cups-raster': True,
    'application/octet-stream': True,
    'image/urf': True,
    'image/png': True,
    'image/tiff': True,
    'image/jpeg': True,
    'image/gif': True,
    'text/plain': True,
    'text/html': True,
}

BLOCKED_DOCUMENT_TYPES = {
    'image/x-xwindowdump': False,
    'image/x-xpixmap': False,
    'image/x-xbitmap': False,
    'image/x-sun-raster': False,
    'image/x-sgi-rgb': False,
    'image/x-portable-pixmap': False,
    'image/x-portable-graymap': False,
    'image/x-portable-bitmap': False,
    'image/x-portable-anymap': False,
    'application/x-shell': False,
    'application/x-perl': False,
    'application/x-csource': False,
    'application/x-cshell': False,
}

DOCUMENT_TYPES = {**ALLOWED_DOCUMENT_TYPES, **BLOCKED_DOCUMENT_TYPES}

@dataclass
class PrinterInfo:
    """Dataclass to store printer information"""
    name: str
    host: Optional[str]
    address: Optional[str]
    port: int
    domain: str
    txt: Dict[str, str]
    source: str = ''

class AirPrintGenerator:
    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        port: Optional[int] = None,
        verbose: bool = False,
        directory: Optional[str] = None,
        prefix: str = 'AirPrint-',
        adminurl: bool = False,
        use_cups: bool = True,
        use_avahi: bool = False,
        dns_domain: Optional[str] = None
    ):
        self.host = host
        self.user = user
        self.port = port
        self.verbose = verbose
        self.directory = Path(directory) if directory else None
        self.prefix = prefix
        self.adminurl = adminurl
        self.use_cups = use_cups and HAS_CUPS
        self.use_avahi = use_avahi and HAS_AVAHI
        self.dns_domain = dns_domain

        if self.user and HAS_CUPS:
            cups.setUser(self.user)

    def _log(self, message: str) -> None:
        """Helper method for verbose logging"""
        if self.verbose:
            print(message, file=sys.stderr)

    def _validate_txt_record(self, key: str, value: str) -> Optional[str]:
        """Validate and potentially truncate TXT record to stay within DNS limits"""
        txt_record = f"{key}={value}"
        if len(txt_record.encode('utf-8')) >= 255:
            self._log(f"Warning: TXT record {key} exceeds 255 bytes, truncating")
            # Try to truncate the value while keeping a valid UTF-8 string
            max_bytes = 254 - len(key.encode('utf-8')) - 1  # -1 for '='
            value_bytes = value.encode('utf-8')
            truncated_bytes = value_bytes[:max_bytes]
            while not self._is_valid_utf8(truncated_bytes):
                truncated_bytes = truncated_bytes[:-1]
            return truncated_bytes.decode('utf-8')
        return value

    def _is_valid_utf8(self, bytes_str: bytes) -> bool:
        """Check if a byte string is valid UTF-8"""
        try:
            bytes_str.decode('utf-8')
            return True
        except UnicodeDecodeError:
            return False

    def _process_printer_formats(self, attrs: Dict[str, Any]) -> str:
        """Process and validate printer format support"""
        formats = []
        deferred = []

        for fmt in attrs['document-format-supported']:
            if fmt in DOCUMENT_TYPES:
                if DOCUMENT_TYPES[fmt]:
                    formats.append(fmt)
            else:
                deferred.append(fmt)

        if 'image/urf' not in formats:
            print(f"Warning: image/urf not in mime types, printer may not be available on iOS 6+",
                  file=sys.stderr)

        all_formats = ','.join(formats + deferred)
        return self._validate_txt_record('pdl', all_formats).split('=', 1)[1]

    def _get_printer_capabilities(self, attrs: Dict[str, Any]) -> Dict[str, str]:
        """Extract printer capabilities from CUPS attributes"""
        capabilities = {}

        # Standard capabilities mapping
        caps_mapping = {
            'printer-bindings-supported': ('Bind', lambda x: 'T' if x else None),
            'sides-supported': ('Duplex', lambda x: 'T' if 'two-sided-long-edge' in x else None),
            'collate-supported': ('Collate', lambda x: 'T' if x else None),
            'copies-supported': ('Copies', lambda x: 'T' if x and x > 1 else None),
            'color-supported': ('Color', lambda x: 'T' if x else None),
            'printer-binary-ok-supported': ('Binary', lambda x: 'T' if x else None),
        }

        for cups_key, (airprint_key, transform) in caps_mapping.items():
            if cups_key in attrs:
                value = transform(attrs[cups_key])
                if value:
                    capabilities[airprint_key] = value

        return capabilities

    def _collect_cups_printers(self) -> List[PrinterInfo]:
        """Collect printer information from CUPS"""
        if not self.use_cups:
            return []

        self._log('Collecting shared printers from CUPS')

        conn = cups.Connection(self.host, self.port) if self.host else cups.Connection()
        printers = []

        for name, details in conn.getPrinters().items():
            if not details['printer-is-shared']:
                continue

            attrs = conn.getPrinterAttributes(name)
            uri = urlparse(details['printer-uri-supported'])

            port_no = uri.port or self.port or cups.getPort()
            resource_path = uri.path

            # Clean up resource path
            if match := re.match(r'^//(.*):(\d+)(/.*)', resource_path):
                resource_path = match.group(3)
            resource_path = re.sub(r'^/+', '', resource_path)

            formats = self._process_printer_formats(attrs)

            # Get printer capabilities
            capabilities = self._get_printer_capabilities(attrs)

            # Build basic TXT record dictionary
            txt_records = {
                'rp': resource_path,
                'note': details['printer-location'],
                'product': '(GPL Ghostscript)',
                'ty': details['printer-make-and-model'],
                'printer-state': str(details['printer-state']),
                'printer-type': hex(details['printer-type']),
                'pdl': formats,
                'media-default': attrs.get('media-default', ''),
            }

            # Add adminurl if required
            if self.adminurl:
                txt_records['adminurl'] = details['printer-uri-supported']

            # Add auth if using CUPS authentication
            if self.user:
                txt_records['air'] = f"{self.user},{getpass('Enter password for CUPS authentication: ')}"

            # Merge capabilities
            txt_records.update(capabilities)

            # Validate all TXT records
            validated_txt = {}
            for key, value in txt_records.items():
                if value:  # Only add non-empty values
                    validated_value = self._validate_txt_record(key, str(value))
                    if validated_value:
                        validated_txt[key] = validated_value.split('=', 1)[1]

            printer = PrinterInfo(
                name=name,
                host=None,
                address=None,
                port=port_no,
                domain='local',
                txt=validated_txt,
                source='CUPS'
            )
            printers.append(printer)

        return printers

    def _collect_avahi_printers(self) -> List[PrinterInfo]:
        """Collect printer information using DNS-SD/Avahi"""
        if not self.use_avahi:
            return []

        self._log('Collecting networked printers using DNS-SD')
        finder = avahisearch.AvahiPrinterFinder(verbose=self.verbose)
        return [PrinterInfo(**{**p, 'source': 'DNS-SD'}) for p in finder.Search()]

    def _create_service_file(self, printer: PrinterInfo) -> None:
        """Generate service file for a printer"""
        tree = ElementTree()
        tree.parse(StringIO(XML_TEMPLATE.replace('\n', '').replace('\r', '').replace('\t', '')))

        root = tree.getroot()
        name_elem = tree.find('name')
        if name_elem is not None:
            name_elem.text = f'Sec.AirPrint {printer.name} @ %h'

        service_elem = tree.find('service')
        if service_elem is None:
            raise ValueError("Invalid XML template: missing service element")

        port_elem = service_elem.find('port')
        if port_elem is not None:
            port_elem.text = str(printer.port)

        if printer.host:
            host = printer.host
            if self.dns_domain:
                parts = host.rsplit('.', 1)
                if len(parts) > 1:
                    host = f"{parts[0]}.{self.dns_domain}"
            host_elem = Element('host-name')
            host_elem.text = host
            service_elem.append(host_elem)

        # Add txt records
        for key, value in printer.txt.items():
            if self.adminurl or key != 'adminurl':
                txt_elem = Element('txt-record')
                txt_elem.text = f"{key}={value}"
                service_elem.append(txt_elem)

        # Add color support if available
        if printer.txt.get('color-supported'):
            color_elem = Element('txt-record')
            color_elem.text = 'Color=T'
            service_elem.append(color_elem)

        # Add paper size support
        if printer.txt.get('media-default') == 'iso_a4_210x297mm':
            paper_elem = Element('txt-record')
            paper_elem.text = 'PaperMax=legal-A4'
            service_elem.append(paper_elem)

        # Generate filename
        source_prefix = f"{printer.source}-" if printer.source else ""
        filename = f"{self.prefix}{source_prefix}{printer.name}.service"

        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
            filepath = self.directory / filename
        else:
            filepath = Path(filename)

        # Write the file - binary mode for lxml, text mode for standard library
        with filepath.open('wb' if USING_LXML else 'w', encoding=None if USING_LXML else 'utf-8') as f:
            if USING_LXML:
                tree.write(f, pretty_print=True, xml_declaration=True, encoding="UTF-8")
            else:
                xmlstr = tostring(root, encoding='unicode')
                doc = minidom.parseString(xmlstr)
                dt = minidom.getDOMImplementation('').createDocumentType(
                    'service-group', None, 'avahi-service.dtd')
                doc.insertBefore(dt, doc.documentElement)
                doc.writexml(f)

        self._log(f'Created from {printer.source or "unknown"}: {filepath}')

    def generate(self) -> None:
        """Main method to generate service files for all printers"""
        printers = self._collect_cups_printers() + self._collect_avahi_printers()

        if not printers:
            print("No printers found.", file=sys.stderr)
            return

        for printer in printers:
            self._create_service_file(printer)

def main():
    parser = ArgumentParser(description='Generate AirPrint service files for printers')
    parser.add_argument('-s', '--dnssd', action="store_true", dest="avahi",
                      help="Search for network printers using DNS-SD (requires avahi)")
    parser.add_argument('-D', '--dnsdomain', action="store",
                      help='DNS domain where printers are located')
    parser.add_argument('-c', '--cups', action="store_true",
                      help="Search CUPS for shared printers (requires CUPS)")
    parser.add_argument('-H', '--host', help='Hostname of CUPS server (optional)')
    parser.add_argument('-P', '--port', type=int, help='Port number of CUPS server')
    parser.add_argument('-u', '--user', help='Username for CUPS authentication')
    parser.add_argument('-d', '--directory', help='Directory to create service files')
    parser.add_argument('-v', '--verbose', action="store_true",
                      help="Print debugging information to STDERR")
    parser.add_argument('--debug', action="store_true",
                      help="Print detailed debug information about dependencies")
    parser.add_argument('-p', '--prefix', default='AirPrint-',
                      help='Prefix for generated files')
    parser.add_argument('-a', '--admin', action="store_true", dest="adminurl",
                      help="Include the printer specified URI as the adminurl")

    args = parser.parse_args()

    # Debug information
    if args.debug:
        print("Debug Information:", file=sys.stderr)
        print(f"Python version: {sys.version}", file=sys.stderr)
        print(f"CUPS available: {HAS_CUPS}", file=sys.stderr)
        print(f"Avahi available: {HAS_AVAHI}", file=sys.stderr)
        print(f"Arguments: {args}", file=sys.stderr)

    # Check if required modules are available based on requested mode
    missing_deps = []
    if args.cups and not HAS_CUPS:
        missing_deps.append("CUPS (python3-cups)")
    if args.avahi and not HAS_AVAHI:
        missing_deps.append("Avahi (python3-avahi)")

    if missing_deps:
        print("Warning: Some requested features are not available:", file=sys.stderr)
        for dep in missing_deps:
            print(f"  - Missing {dep}", file=sys.stderr)
        print("\nPlease install the missing dependencies to use all features.", file=sys.stderr)

        # Only exit if ALL requested features are unavailable
        if len(missing_deps) == sum([args.cups, args.avahi]):
            sys.exit(1)

    if args.cups and HAS_CUPS:
        cups.setPasswordCB(getpass)

    generator = AirPrintGenerator(
        host=args.host,
        user=args.user,
        port=args.port,
        verbose=args.verbose or args.debug,  # Enable verbose mode if debug is on
        directory=args.directory,
        prefix=args.prefix,
        adminurl=args.adminurl,
        use_cups=args.cups,
        use_avahi=args.avahi,
        dns_domain=args.dnsdomain,
    )

    generator.generate()

    if args.avahi and HAS_AVAHI and not args.dnsdomain:
        print("NOTE: If printers found by DNS-SD do not resolve outside the local subnet,",
              "specify the printers' DNS domain with --dnsdomain or edit the generated",
              "<host-name> element to fit your network.", file=sys.stderr)