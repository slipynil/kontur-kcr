from datetime import date
from pathlib import Path

import pytest
from conftest import WorkbookFactory
from openpyxl import Workbook

from import_worker.clients import fingerprint
from import_worker.models import ImportParameters, ImportType
from import_worker.parser import InvalidWorkbook, parse_workbook
from import_worker.validation import InvalidRow, counter


def params(import_type: ImportType = ImportType.SNAPSHOT) -> ImportParameters:
    return ImportParameters(
        selected_quarter="2026_Q2",
        snapshot_date=date(2026, 4, 10),
        import_type=import_type,
    )


def test_historical_registry(tmp_path: Path, xlsx: WorkbookFactory) -> None:
    path = tmp_path / "registry.xlsx"
    path.write_bytes(
        xlsx(
            {
                "Реестр": [
                    ["Отчёт"],
                    [None, "Код КЦР", "Название"],
                    [
                        " 0012 ",
                        None,
                        " Клиент ",
                        None,
                        None,
                        None,
                        "VIP; В работе",
                        " 001234 ",
                        " 007 ",
                        "Дейсвующий",
                        False,
                        None,
                    ],
                ],
                "Продолжение": [
                    ["0013", None, "Другой клиент", None, None, None, None, "001234"],
                ],
            }
        )
    )
    result = parse_workbook(path, params(ImportType.REGISTRY))
    assert result.total_rows == 2 and not result.errors
    first, second = [row.item for row in result.rows]
    assert first.snapshot is None and second.snapshot is None
    assert first.client.model_dump(exclude_unset=True) == {
        "kcr_code": "0012",
        "organization_name": "Клиент",
        "inn": "001234",
        "kpp": "007",
        "status": "ACTIVE",
        "is_blocked": False,
        "manager_name": None,
        "tags": ["VIP"],
        "raw_work_status": "VIP; В работе",
    }
    assert second.client.inn == first.client.inn


def test_snapshot_mixed_rows_and_periods(tmp_path: Path, xlsx: WorkbookFactory) -> None:
    path = tmp_path / "snapshot.xlsx"
    path.write_bytes(
        xlsx(
            {
                "2 кв 2026": [
                    ["Код КЦР", None, None, "Всего", "Непередано", "Исходный квартал"],
                    ["001", None, None, None, "6.0", "2026_Q1"],
                    ["001", None, None, 111, 0, "2026_Q2"],
                    ["001", None, None, 111, 3, "2026_Q2"],
                    ["002", None, None, "111.5", 0, "2026_Q2"],
                    ["003", None, None, "111.0", -1, "2026_Q2"],
                    [None, None, "Клиент без кода", 100, 2],
                    ["004", None, None, "#N/A", 2],
                    ["005", None, None, 10, 3],
                    [None, None, None, "=SUM(D2:D9)", "=SUM(E2:E9)"],
                ],
                "2026 год": [["annual", None, None, 20, 10]],
                "1–2 кв 2026": [["combined", None, None, 20, 10]],
                "2026_Q1": [["wrong-quarter", None, None, 10, 5]],
            }
        )
    )
    result = parse_workbook(path, params())
    assert result.total_rows == 8
    assert [error.row_number for error in result.errors] == [4, 5, 6, 7, 8, 9, 0, 0, 0]
    assert result.errors[5].reason == "Некорректный исходный квартал документов"
    assert [row.item.snapshot.model_dump() for row in result.rows] == [
        {
            "quarter": "2026_Q1",
            "snapshot_date": date(2026, 4, 10),
            "total_documents": None,
            "unsubmitted_documents": 6,
        },
        {
            "quarter": "2026_Q2",
            "snapshot_date": date(2026, 4, 10),
            "total_documents": 111,
            "unsubmitted_documents": 0,
        },
    ]
    assert result.rows[0].item.client.model_dump(exclude_unset=True) == {"kcr_code": "001"}


def test_snapshot_uses_named_columns_and_reports_future_origins(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "reordered.xlsx"
    path.write_bytes(
        xlsx(
            {
                "II квартал 2026": [
                    ["Непереданные оригиналы документов", "Исходный квартал", "Код КЦР", "Заметка"],
                    [0, "2026_Q1", "001", "Не счётчик"],
                    [1, "2026_Q3", "002"],
                    [2, "2026_Q2", "003"],
                    ["=SUM(A2:A4)"],
                ],
                "Q1 2026": [[0, None, "old-quarter", 1]],
                "I–II кв 2026": [[0, None, "aggregate", 1]],
            }
        )
    )
    result = parse_workbook(path, params())
    assert result.total_rows == 3
    assert [(row.row_number, row.item.client.kcr_code) for row in result.rows] == [
        (2, "001"),
        (4, "003"),
    ]
    assert result.rows[0].item.snapshot.total_documents is None
    assert result.rows[0].item.snapshot.unsubmitted_documents == 0
    assert result.errors[0].kcr_code == "002"
    assert result.errors[0].reason == "Исходный квартал документов позже квартала наблюдения"
    assert [error.sheet for error in result.errors[1:]] == ["Q1 2026", "I–II кв 2026"]


def test_registry_uses_named_columns_without_reinterpreting_other_fields(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "named-registry.xlsx"
    path.write_bytes(
        xlsx(
            {
                "Реестр": [
                    ["Название организации", "Код КЦР", "Телефон", "ИНН", "Признаки"],
                    ["Клиент", "001", "+7 900 000-00-00", "001234", "Общение через МПП"],
                ]
            }
        )
    )
    result = parse_workbook(path, params(ImportType.REGISTRY))
    assert not result.errors
    client = result.rows[0].item.client
    assert client.kcr_code == "001" and client.inn == "001234"
    assert client.organization_name == "Клиент" and client.status is None
    assert client.tags == ["MPP"] and client.contacts == {"phone": "+7 900 000-00-00"}


def test_id_format_and_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "format.xlsx"
    book = Workbook()
    book.active.append([12, None, None, 111, 5])
    book.active["A1"].number_format = "0000"
    book.save(path)
    book.close()
    inputs = params()
    row = parse_workbook(path, inputs).rows[0]
    assert row.item.client.kcr_code == "0012"
    new_location = row.model_copy(update={"row_number": 10, "sheet": "другой лист"})
    assert fingerprint(inputs, row) == fingerprint(inputs, new_location)
    earlier = inputs.model_copy(update={"snapshot_date": date(2026, 4, 5)})
    earlier_row = parse_workbook(path, earlier).rows[0]
    assert fingerprint(inputs, row) != fingerprint(earlier, earlier_row)


@pytest.mark.parametrize(("raw", "value"), [("111", 111), ("111.0", 111), ("0", 0), (None, None)])
def test_counters(raw: str | None, value: int | None) -> None:
    assert counter(raw, "Всего документов") == value


@pytest.mark.parametrize("raw", ["111.5", "-1", "NaN", "2147483648"])
def test_invalid_counters(raw: str) -> None:
    with pytest.raises(InvalidRow):
        counter(raw, "Всего документов")


def test_corrupt_workbook(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"PK\x03\x04invalid")
    with pytest.raises(InvalidWorkbook):
        parse_workbook(path, params())
