"""Tolerant XLSX loading for real-world Ozon exports.

Some export/anonymization pipelines used to produce the .xlsx files sellers
actually upload (observed directly on two real Ozon exports — advertising
statistics and product-card analytics) emit a handful of OOXML enum
attributes in the wrong case, e.g.:

  <alignment horizontal="Left"/>            instead of  horizontal="left"
  <pane activePane="bottom-right" .../>     instead of  activePane="bottomRight"

Strict OOXML readers (openpyxl, and therefore pandas' openpyxl engine) raise
ValueError on these and refuse to open the file at all — even though the
actual cell data is perfectly valid. This module patches only those known
attribute-casing issues in the workbook's view/style XML before handing the
bytes to pandas/openpyxl; it never touches cell values or worksheet data.
"""
from __future__ import annotations

import io
import re
import zipfile


def _fix_style_casing(text: str) -> str:
    text = re.sub(r'horizontal="([A-Z])(\w*)"', lambda m: f'horizontal="{m.group(1).lower()}{m.group(2)}"', text)
    text = re.sub(r'vertical="([A-Z])(\w*)"', lambda m: f'vertical="{m.group(1).lower()}{m.group(2)}"', text)
    return text


def _fix_active_pane(text: str) -> str:
    def fix(m: re.Match) -> str:
        parts = m.group(1).split("-")
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
        return f'activePane="{camel}"'

    return re.sub(r'activePane="([a-zA-Z-]+)"', fix, text)


def tolerant_xlsx_bytes(content: bytes) -> bytes:
    """Returns a copy of the .xlsx file with known bad-casing enum attributes
    fixed. Falls back to the original bytes unchanged if the file isn't a
    readable zip (caller's normal error handling then applies)."""
    try:
        src = zipfile.ZipFile(io.BytesIO(content), "r")
    except zipfile.BadZipFile:
        return content

    out = io.BytesIO()
    with src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/styles.xml":
                data = _fix_style_casing(data.decode("utf-8")).encode("utf-8")
            elif item.filename.startswith("xl/worksheets/sheet") and item.filename.endswith(".xml"):
                data = _fix_active_pane(data.decode("utf-8")).encode("utf-8")
            zout.writestr(item, data)
    return out.getvalue()
