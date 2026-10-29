"""A small ``.xlsx`` writer for the dashboard's exports (T-168), with no extra dependency.

An ``.xlsx`` is a zip of a few XML files (Office Open XML). This writes only what the exports need:
sheets with a bold green header row that stays visible, filters, column widths, wrapped text for
long messages, and cells that are always text or numbers.

Text goes in as an *inline string*, never as a formula: a message that starts with ``=`` or ``@``
is shown as typed and never evaluated. Characters XML cannot carry are replaced, and a cell is
cut before Excel's own limit (32,767 characters).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final
from xml.sax.saxutils import escape

MIME: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_CELL_CHARS: Final = 32_000
MAX_SHEET_NAME: Final = 31

_NS: Final = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# What XML 1.0 cannot carry (control characters other than tab, newline and carriage return).
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_SHEET_NAME = re.compile(r"[\[\]:*?/\\]")

_SPREADSHEET: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_PACKAGE: Final = "application/vnd.openxmlformats-package"

# Cell styles (index into ``cellXfs`` of ``_STYLES``).
_HEADER, _BODY_WRAP, _BODY = 1, 2, 3

Cell = str | int | float | None

_STYLES: Final = f"""{_XML}<styleSheet xmlns="{_NS}">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>\
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill>\
<fill><patternFill patternType="gray125"/></fill>\
<fill><patternFill patternType="solid"><fgColor rgb="FF1D6B2B"/><bgColor indexed="64"/>\
</patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" \
applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">\
<alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">\
<alignment vertical="top"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


@dataclass
class Sheet:
    name: str
    header: Sequence[str]
    rows: Sequence[Sequence[Cell]]
    widths: Sequence[int] = ()  # characters, per column; missing ones get a default
    wrap: frozenset[int] = field(default_factory=frozenset)  # columns whose text wraps


def _override(part: str, content_type: str) -> str:
    return f'<Override PartName="{part}" ContentType="{content_type}"/>'


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def clean(text: str) -> str:
    """Text a cell can hold: no characters XML rejects, no more than ``MAX_CELL_CHARS``."""
    text = _INVALID_XML.sub("�", text).encode("utf-8", "replace").decode("utf-8")
    return text if len(text) <= MAX_CELL_CHARS else f"{text[:MAX_CELL_CHARS]}… (cortado)"


def sheet_name(name: str, taken: set[str]) -> str:
    """A name Excel accepts (no ``[]:*?/\\``, at most 31 characters) and not used yet."""
    base = _BAD_SHEET_NAME.sub(" ", name).strip().strip("'")[:MAX_SHEET_NAME] or "Planilha"
    candidate, n = base, 1
    while candidate.casefold() in taken:
        n += 1
        suffix = f" ({n})"
        candidate = base[: MAX_SHEET_NAME - len(suffix)] + suffix
    taken.add(candidate.casefold())
    return candidate


def _cell(ref: str, value: Cell, style: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):  # a bool is an int in Python; show it as text
        value = "sim" if value else "não"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(clean(value))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    columns = len(sheet.header)
    last = column_letter(max(columns, 1) - 1)
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" '
        f'width="{sheet.widths[i] if i < len(sheet.widths) else 18}" customWidth="1"/>'
        for i in range(columns)
    )
    lines = [
        '<row r="1" ht="22" customHeight="1">'
        + "".join(
            _cell(f"{column_letter(i)}1", title, _HEADER) for i, title in enumerate(sheet.header)
        )
        + "</row>"
    ]
    for number, row in enumerate(sheet.rows, start=2):
        cells = "".join(
            _cell(f"{column_letter(i)}{number}", value, _BODY_WRAP if i in sheet.wrap else _BODY)
            for i, value in enumerate(row[:columns])
        )
        lines.append(f'<row r="{number}">{cells}</row>')
    last_row = len(sheet.rows) + 1
    return (
        f'{_XML}<worksheet xmlns="{_NS}">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{cols}</cols>"
        f"<sheetData>{''.join(lines)}</sheetData>"
        f'<autoFilter ref="A1:{last}{last_row}"/>'
        "</worksheet>"
    )


def build_xlsx(sheets: Sequence[Sheet]) -> bytes:
    """The workbook as bytes, ready to be sent or saved as ``.xlsx``."""
    if not sheets:
        raise ValueError("uma planilha precisa de ao menos uma aba")
    taken: set[str] = set()
    names = [sheet_name(s.name, taken) for s in sheets]
    count = len(sheets)
    sheet_types = "".join(
        _override(f"/xl/worksheets/sheet{i}.xml", f"{_SPREADSHEET}.worksheet+xml")
        for i in range(1, count + 1)
    )
    content_types = (
        f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{_PACKAGE}.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + _override("/xl/workbook.xml", f"{_SPREADSHEET}.sheet.main+xml")
        + _override("/xl/styles.xml", f"{_SPREADSHEET}.styles+xml")
        + sheet_types
        + "</Types>"
    )
    root_rels = (
        f'{_XML}<Relationships xmlns="{_PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        f'{_XML}<workbook xmlns="{_NS}" xmlns:r="{_REL_NS}"><sheets>'
        + "".join(
            f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(names, start=1)
        )
        + "</sheets></workbook>"
    )
    workbook_rels = (
        f'{_XML}<Relationships xmlns="{_PKG_REL_NS}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{_REL_NS}/worksheet"'
            f' Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, count + 1)
        )
        + f'<Relationship Id="rId{count + 1}" Type="{_REL_NS}/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    return buffer.getvalue()
