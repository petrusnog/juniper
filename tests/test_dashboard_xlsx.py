"""The dashboard's own .xlsx writer (T-168): valid files, text that stays text."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import openpyxl
import pytest

from brain.dashboard.xlsx import (
    MAX_CELL_CHARS,
    Sheet,
    build_xlsx,
    clean,
    column_letter,
    sheet_name,
)


def load(sheets: list[Sheet]) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(build_xlsx(sheets)))


def test_a_workbook_round_trips_through_a_real_reader() -> None:
    book = load(
        [Sheet("Dados", ["Id", "Autor", "Mensagem", "N"], [["a1", "Usuário", "olá, çã é", 7]])]
    )
    sheet = book["Dados"]
    assert [c.value for c in sheet[1]] == ["Id", "Autor", "Mensagem", "N"]
    assert [c.value for c in sheet[2]] == ["a1", "Usuário", "olá, çã é", 7]
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:D2"
    assert sheet["A1"].font.b is True  # the header is bold


def test_the_package_is_made_of_well_formed_xml() -> None:
    data = build_xlsx([Sheet("A", ["x"], [["1"]]), Sheet("B", ["y"], [[2]])])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml"} <= set(names)
        assert {"xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"} <= set(names)
        for name in names:
            ElementTree.fromstring(archive.read(name))  # raises on malformed XML


@pytest.mark.parametrize("text", ["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\t=x"])
def test_text_that_looks_like_a_formula_is_never_a_formula(text: str) -> None:
    cell = load([Sheet("A", ["x"], [[text]])])["A"]["A2"]
    assert cell.data_type == "s" and cell.value == text


def test_characters_xml_cannot_carry_do_not_corrupt_the_file() -> None:
    nasty = "a\x00b\x08c\x1bd￾e & <tag> \"aspas\" 'simples'\r\nlinha"
    value = load([Sheet("A", ["x"], [[nasty]])])["A"]["A2"].value
    assert value.startswith("a") and "<tag>" in value and "&" in value and "\x00" not in value


def test_a_cell_is_cut_before_excels_limit() -> None:
    value = load([Sheet("A", ["x"], [["x" * 50_000]])])["A"]["A2"].value
    assert len(value) <= MAX_CELL_CHARS + 20 and value.endswith("(cortado)")
    assert len(clean("curto")) == 5


def test_sheet_names_follow_excels_rules() -> None:
    taken: set[str] = set()
    assert sheet_name("Mensagens: [a]/b?", taken) == "Mensagens   a  b"
    assert sheet_name("mensagens   a  b", taken) == "mensagens   a  b (2)"  # the same, case apart
    long = sheet_name("x" * 60, taken)
    assert len(long) == 31
    assert sheet_name("", taken) == "Planilha"


def test_column_letters() -> None:
    assert [column_letter(i) for i in (0, 25, 26, 27, 51, 52, 701, 702)] == [
        "A", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA",
    ]  # fmt: skip


def test_none_cells_are_empty_and_bools_are_words() -> None:
    row = load([Sheet("A", ["a", "b", "c"], [["x", None, True]])])["A"][2]
    assert [c.value for c in row] == ["x", None, "sim"]


def test_widths_and_wrapping_apply() -> None:
    sheet = load([Sheet("A", ["a", "b"], [["x", "y"]], widths=[10, 70], wrap=frozenset({1}))])["A"]
    assert sheet.column_dimensions["B"].width == 70
    assert sheet["B2"].alignment.wrap_text is True and not sheet["A2"].alignment.wrap_text


def test_an_empty_sheet_list_is_refused() -> None:
    with pytest.raises(ValueError):
        build_xlsx([])
