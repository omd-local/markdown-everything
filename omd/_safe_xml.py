"""Conservative XML parsing for bounded, untrusted feeds."""
from __future__ import annotations

import xml.etree.ElementTree as ET


class UnsafeXML(ValueError):
    """Raised when untrusted XML uses declarations OMD does not need."""


def parse_untrusted_xml(
    data: str | bytes,
    *,
    max_bytes: int,
    label: str,
) -> ET.Element:
    if not isinstance(data, (str, bytes)):
        raise TypeError("XML data must be text or bytes")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > max_bytes:
        raise UnsafeXML(f"{label} exceeds the {max_bytes}-byte XML limit")

    # OMD only needs ordinary RSS/Atom. Remove NULs before scanning so
    # UTF-16/32 declarations cannot bypass this guard.
    declarations = raw.replace(b"\x00", b"").lower()
    if b"<!doctype" in declarations or b"<!entity" in declarations:
        raise UnsafeXML(f"{label} contains unsupported DTD or entity declarations")

    return ET.fromstring(raw)  # noqa: S314 - declarations and size are checked above.
