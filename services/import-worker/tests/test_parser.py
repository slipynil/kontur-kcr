from datetime import date
from pathlib import Path

import pytest
from conftest import REGISTRY_HEADER, SNAPSHOT_HEADER, WorkbookFactory
from openpyxl import Workbook

from import_worker.clients import fingerprint
from import_worker.models import ImportParameters, ImportType
from import_worker.parser import InvalidWorkbook, parse_workbook
from import_worker.validation import InvalidRow, counter


def params(import_type: ImportType = ImportType.SNAPSHOT) -> ImportParameters:
    if import_type is ImportType.REGISTRY:
        return ImportParameters(import_type=import_type)
    return ImportParameters(
        selected_quarter="2026_Q2",
        snapshot_date=date(2026, 4, 10),
        import_type=import_type,
    )


def test_registry_requires_header_in_first_row(tmp_path: Path, xlsx: WorkbookFactory) -> None:
    path = tmp_path / "registry.xlsx"
    path.write_bytes(
        xlsx(
            {
                "Реестр": [["Отчёт"], REGISTRY_HEADER],
            }
        )
    )
    result = parse_workbook(path, params(ImportType.REGISTRY))
    assert result.total_rows == 0 and not result.rows
    assert [(error.sheet, error.row_number, error.reason) for error in result.errors] == [
        ("Реестр", 1, "A1 — ожидается заголовок «Код КЦР»")
    ]


@pytest.mark.parametrize("annual_sheet", ["2026 год", "весь 2026 год", "Весь 2025 ГОД"])
def test_snapshot_mixed_rows_and_periods(
    tmp_path: Path, xlsx: WorkbookFactory, annual_sheet: str
) -> None:
    path = tmp_path / "snapshot.xlsx"
    path.write_bytes(
        xlsx(
            {
                "2 кв 2026": [
                    ["Код КЦР", None, None, "Всего", "Непередано", "Исходный квартал"],
                    ["001", None, None, None, 6.0, "2026_Q1"],
                    ["001", None, None, 111, 0, "2026_Q2"],
                    ["001", None, None, 111, 3, "2026_Q2"],
                    ["002", None, None, 111.5, 0, "2026_Q2"],
                    ["003", None, None, 111.0, -1, "2026_Q2"],
                    [None, None, "Клиент без кода", 100, 2],
                    ["004", None, None, "#N/A", 2],
                    ["005", None, None, 10, 3],
                    [None, None, None, "=SUM(D2:D9)", "=SUM(E2:E9)"],
                ],
                annual_sheet: [["annual", None, None, 20, 10]],
                "1–2 кв 2026": [["combined", None, None, 20, 10]],
                "2026_Q1": [["wrong-quarter", None, None, 10, 5]],
            }
        )
    )
    result = parse_workbook(path, params())
    assert result.total_rows == 8
    assert [error.row_number for error in result.errors] == [4, 5, 6, 7, 8, 9, 0, 0, 0]
    assert result.errors[5].reason == "Некорректный исходный квартал документов"
    assert result.errors[6].sheet == annual_sheet
    assert result.errors[6].reason == "Годовой, составной или служебный лист пропущен"
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
                    [
                        "Непереданные оригиналы документов",
                        "Исходный квартал",
                        "Код КЦР",
                        "Заметка",
                        "Документов всего",
                    ],
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


def test_standalone_annual_snapshot_uses_selected_quarter_and_explicit_kcr_code(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "annual.xlsx"
    path.write_bytes(
        xlsx(
            {
                "весь 2025 год": [
                    [
                        "Уникальны обезличинный идентификатор",
                        "Код КЦР",
                        "Наитменование организации",
                        "документов всего",
                        "Всего не отправлено документов",
                    ],
                    ["legacy-id", "001", "Тестовая организация", 111, 7],
                ]
            }
        )
    )
    result = parse_workbook(path, params())
    assert result.total_rows == 1 and not result.errors
    assert result.rows[0].item.client.kcr_code == "001"
    assert result.rows[0].item.snapshot.model_dump() == {
        "quarter": "2026_Q2",
        "snapshot_date": date(2026, 4, 10),
        "total_documents": 111,
        "unsubmitted_documents": 7,
    }


def test_anonymized_snapshot_recognizes_legacy_header_without_counting_it_as_row(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "anonymized.xlsx"
    path.write_bytes(
        xlsx(
            {
                "2 кв 2026": [
                    [None],
                    [
                        "Уникальны обезличинный идентификатор",
                        None,
                        None,
                        "документов всего",
                        "Всего не отправлено документов",
                    ],
                    ["legacy-id", None, None, 111, 0],
                ]
            }
        )
    )
    result = parse_workbook(path, params())
    assert result.total_rows == 1 and not result.errors
    assert result.rows[0].row_number == 3
    assert result.rows[0].item.client.kcr_code == "legacy-id"
    assert result.rows[0].item.snapshot.total_documents == 111
    assert result.rows[0].item.snapshot.unsubmitted_documents == 0


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        (
            [*REGISTRY_HEADER[:3], "ИНН", "КПП", *REGISTRY_HEADER[5:]],
            "D1 — ожидается заголовок «КПП»",
        ),
        (REGISTRY_HEADER[:-1], "H1 — ожидается заголовок «статус клиента»"),
        ([*REGISTRY_HEADER, "Примечание"], "I1 — лишний столбец в реестре"),
    ],
)
def test_registry_rejects_workbook_when_a_header_is_wrong(
    tmp_path: Path, xlsx: WorkbookFactory, headers: list[str], reason: str
) -> None:
    path = tmp_path / "wrong-headers.xlsx"
    row = ["001", "Клиент", None, "007", "001234", "Менеджер", None, "Новичок"]
    path.write_bytes(xlsx({"Первый": [REGISTRY_HEADER, row], "Второй": [headers, row]}))
    result = parse_workbook(path, params(ImportType.REGISTRY))
    assert result.total_rows == 0 and not result.rows
    assert [(error.sheet, error.row_number, error.reason) for error in result.errors] == [
        ("Второй", 1, reason)
    ]


def test_registry_matches_statuses_without_case_and_reports_rejected_rows(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "registry.xlsx"
    profile = ["Клиент", "СЦ", "007", "001234", "Менеджер", "Менеджер КП"]
    good_statuses = ["ViP", "НОВИЧОК", "интегратор", "ТЕХПАРТНЕР", "мПп", "Не Работать"]
    path.write_bytes(
        xlsx(
            {
                "Реестр": [
                    [header.upper() for header in REGISTRY_HEADER],
                    *[
                        [f"A{index}", *profile, status]
                        for index, status in enumerate(good_statuses)
                    ],
                    [],
                    ["B0", *profile, "ACTIVE"],
                    ["B1", *profile],
                    ["B2", *profile, "VIP; Новичок"],
                    ["a0", *profile, "VIP"],
                    ["B3", *profile, "=1/0"],
                ]
            }
        )
    )
    result = parse_workbook(path, params(ImportType.REGISTRY))
    assert result.total_rows == 11
    assert [row.item.client.tags for row in result.rows] == [
        ["VIP"],
        ["NOVICE"],
        ["INTEGRATOR"],
        ["TECH_PARTNER"],
        ["MPP"],
        ["DO_NOT_WORK"],
    ]
    assert result.rows[0].item.client.model_dump(exclude_unset=True) == {
        "kcr_code": "A0",
        "organization_name": "Клиент",
        "kpp": "007",
        "inn": "001234",
        "manager_name": "Менеджер",
        "tags": ["VIP"],
        "raw_work_status": "ViP",
    }
    assert [(error.row_number, error.kcr_code, error.reason) for error in result.errors] == [
        (9, "B0", "H9 — недопустимый статус клиента"),
        (10, "B1", "H10 — недопустимый статус клиента"),
        (11, "B2", "H11 — недопустимый статус клиента"),
        (12, "a0", "A12 — дубликат кода КЦР в листе"),
        (13, "B3", "H13 — «статус клиента» содержит ошибку Excel или формулу без результата"),
    ]


def test_id_format_and_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "format.xlsx"
    book = Workbook()
    book.active.append(SNAPSHOT_HEADER)
    book.active.append([12, 111, 5])
    book.active["A2"].number_format = "0000"
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


@pytest.mark.parametrize(("raw", "value"), [(111, 111), (111.0, 111), (0, 0), (None, None)])
def test_counters(raw: object, value: int | None) -> None:
    assert counter(raw, "Всего документов") == value


@pytest.mark.parametrize("raw", [111.5, -1, float("nan"), 2147483648, "111", True])
def test_invalid_counters(raw: object) -> None:
    with pytest.raises(InvalidRow):
        counter(raw, "Всего документов")


def test_snapshot_validates_headings_and_reports_both_counter_cells(
    tmp_path: Path, xlsx: WorkbookFactory
) -> None:
    path = tmp_path / "stored-batch.xlsx"
    path.write_bytes(
        xlsx(
            {
                "Срез": [
                    [],
                    ["Название", "дОКУМЕНТОВ ВСЕГО", "КОД кцр", "ВСЕГО не отправлено документов"],
                    ["Клиент", "11", "001", "=1/0"],
                    ["Клиент", 111.0, "002", 0],
                ],
                "Неверный заголовок": [
                    ["Код КЦР", "Опечатка", "Всего не отправлено документов"],
                    ["003", 12, 1],
                ],
                "Дублирующийся заголовок": [
                    [*SNAPSHOT_HEADER, "ВСЕГО документов"],
                    ["004", 12, 1, 10],
                ],
                "Без заголовков": [["005", 12, 1]],
            }
        )
    )
    result = parse_workbook(path, params(), filename="uploaded.xlsx")
    assert result.total_rows == 2
    assert [row.item.client.kcr_code for row in result.rows] == ["002"]
    assert result.rows[0].item.snapshot.total_documents == 111
    assert [(error.sheet, error.row_number, error.kcr_code) for error in result.errors] == [
        ("Срез", 3, "001"),
        ("Срез", 3, "001"),
        ("Неверный заголовок", 1, None),
        ("Дублирующийся заголовок", 1, None),
        ("Без заголовков", 1, None),
    ]
    assert "B3" in result.errors[0].reason
    assert "D3" in result.errors[1].reason
    assert "отсутствует заголовок «Документов всего»" in result.errors[2].reason
    assert "D1" in result.errors[3].reason
    assert "не найдена строка заголовков" in result.errors[4].reason
    assert all("uploaded.xlsx" in error.reason for error in result.errors)


def test_corrupt_workbook(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"PK\x03\x04invalid")
    with pytest.raises(InvalidWorkbook):
        parse_workbook(path, params())
