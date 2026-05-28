"""Subsonic response envelope helpers — JSON and XML output.

All Subsonic endpoints return the same envelope regardless of success/error.
Clients signal their preferred format via ``f=json`` (default: XML).

XML note: Subsonic's XML format is attribute-heavy.  The rule used here:
  - scalar values  → XML attributes on the current element
  - dict values    → XML child element (key = tag name)
  - list of dicts  → repeated child elements (key = tag name, one per item)
  - None           → attribute/element omitted
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from fastapi.responses import JSONResponse, Response

SUBSONIC_VERSION = "1.16.1"
SERVER_TYPE = "Octave"
SERVER_VERSION = "1.0.0"
XMLNS = "http://subsonic.org/restapi"

# ── error codes ───────────────────────────────────────────────────────────────
ERR_GENERIC = 0
ERR_MISSING_PARAM = 10
ERR_WRONG_CREDENTIALS = 40
ERR_NOT_AUTHORIZED = 50
ERR_NOT_FOUND = 70


# ── internal helpers ──────────────────────────────────────────────────────────

def _xml_el(tag: str, data, parent=None) -> ET.Element:
    """Recursively build an XML element from a dict/scalar."""
    el = ET.SubElement(parent, tag) if parent is not None else ET.Element(tag)
    if isinstance(data, dict):
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, list):
                for item in v:
                    _xml_el(k, item, el)
            elif isinstance(v, dict):
                _xml_el(k, v, el)
            else:
                el.set(k, str(v).lower() if isinstance(v, bool) else str(v))
    elif data is not None:
        el.text = str(data)
    return el


def _base_xml(status: str) -> ET.Element:
    root = ET.Element("subsonic-response")
    root.set("xmlns", XMLNS)
    root.set("status", status)
    root.set("version", SUBSONIC_VERSION)
    root.set("type", SERVER_TYPE)
    root.set("serverVersion", SERVER_VERSION)
    return root


def _base_json(status: str) -> dict:
    return {
        "subsonic-response": {
            "status": status,
            "version": SUBSONIC_VERSION,
            "type": SERVER_TYPE,
            "serverVersion": SERVER_VERSION,
        }
    }


def _render_xml(root: ET.Element) -> Response:
    body = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
    return Response(content=body, media_type="application/xml; charset=UTF-8")


# ── public API ────────────────────────────────────────────────────────────────

def ok(fmt: str, data: dict | None = None) -> Response:
    """Return a successful Subsonic response, optionally embedding *data*."""
    if fmt in ("json", "jsonp"):
        body = _base_json("ok")
        if data:
            body["subsonic-response"].update(data)
        return JSONResponse(body)
    root = _base_xml("ok")
    if data:
        for key, value in data.items():
            _xml_el(key, value, root)
    return _render_xml(root)


def err(fmt: str, code: int, message: str) -> Response:
    """Return a Subsonic error response (HTTP 200 per spec)."""
    if fmt in ("json", "jsonp"):
        body = _base_json("failed")
        body["subsonic-response"]["error"] = {"code": code, "message": message}
        return JSONResponse(body)
    root = _base_xml("failed")
    e = ET.SubElement(root, "error")
    e.set("code", str(code))
    e.set("message", message)
    return _render_xml(root)
