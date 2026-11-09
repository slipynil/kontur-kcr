import hashlib
import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from import_worker.models import (
    ClientInput,
    ImportErrorRow,
    ImportItem,
    ImportParameters,
    ImportType,
    ParsedImport,
    SnapshotInput,
    SourceRow,
)
from import_worker.validation import (
    QUARTER,
    InvalidRow,
    blocked,
    client_status,
    counter,
    normalized_text,
    tags,
)

HEADERS = {
    "kcr_code": ("код кцр", "kcr_code"),
    "organization_name": ("название", "название организации", "organization_name"),
    "inn": ("инн", "inn"),
    "kpp": ("кпп", "kpp"),
    "status": ("статус", "действительность", "status"),
    "is_blocked": ("блокировка", "is_blocked"),
    "manager_name": ("менеджер", "фио менеджера", "manager_name"),
    "tags": ("признаки", "специальные признаки", "tags"),
    "raw_work_status": ("рабочие отметки", "raw_work_status"),
    "total_documents": ("всего", "всего документов", "total_documents"),
    "unsubmitted_documents": (
        "непередано",
        "непереданные документы",
        "непереданные оригиналы документов",
        "неотправленные документы",
        "unsubmitted_documents",
    ),
    "quarter": ("quarter", "исходный квартал"),
    "phone": ("телефон", "phone"),
    "email": ("email", "e-mail", "электронная почта"),
}
HEADER_FIELDS = {alias: field for field, aliases in HEADERS.items() for alias in aliases}
LEGACY_COLUMNS = {
    "kcr_code": 0,
    "organization_name": 2,
    "total_documents": 3,
    "unsubmitted_documents": 4,
    "tags": 6,
    "raw_work_status": 6,
    "inn": 7,
    "kpp": 8,
    "status": 9,
    "is_blocked": 10,
    "manager_name": 11,
}
SNAPSHOT_FIELDS = {"kcr_code", "quarter", "total_documents", "unsubmitted_documents"}
COUNTER_FIELDS = ("total_documents", "unsubmitted_documents")
QUARTER_IN_TITLE = re.compile(
    r"(?<!\d)([0-9]{4})[\s_-]*q([1-4])(?=$|[\s_-])"
    r"|(?<!\w)(q[1-4]|[1-4]|iv|iii|ii|i)(?:\s*кв(?:артал)?\.?\s*|\s+)([0-9]{4})(?!\d)"
)
ROMAN_QUARTERS = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
AGGREGATE_TITLE = re.compile(
    r"(?<!\w)(?:[1-4]|iv|iii|ii|i)\s*(?:[-–—/+,и]\s*(?:[1-4]|iv|iii|ii|i)\s*)+\s*кв"
    r"|q[1-4]\s*[-–—/+,]\s*q[1-4]"
    r"|\b(?:годовой|годовая|годовые|полугодие)\b"
)
TOTAL_LABEL = re.compile(r"^(?:итог(?:о|и)?|всего|общий итог)(?:$|\W)")
EXCEL_ERRORS = {"#N/A", "#DIV/0!", "#REF!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!"}
MAX_UNCOMPRESSED_BYTES = 256 * 1024**2


class InvalidWorkbook(ValueError):
    pass


def sheet_skip_reason(title: str, selected_quarter: str) -> str | None:
    title = normalized_text(title)
    quarters = QUARTER_IN_TITLE.findall(title)
    if (
        len(quarters) > 1
        or AGGREGATE_TITLE.search(title)
        or not quarters
        and re.fullmatch(r"[0-9]{4}(?:\s*(?:г\.?|год))?", title)
    ):
        return "Годовой, составной или служебный лист пропущен"
    if quarters:
        year, q, num, yr = quarters[0]
        quarter = q or ROMAN_QUARTERS.get(num, num.removeprefix("q"))
        if f"{year or yr}_Q{quarter}" != selected_quarter:
            return "Лист относится к другому кварталу"
    return None


def cell_text(cell: Any) -> str | None:
    value = cell.value
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int) or isinstance(value, float) and value.is_integer():
        value = int(value)
        if re.fullmatch(r"0{2,}", cell.number_format or "") and value >= 0:
            return str(value).zfill(len(cell.number_format))
    return str(value).strip() or None


def header_columns(
    values: tuple[str | None, ...], import_type: ImportType
) -> dict[str, int] | None:
    fields = (
        SNAPSHOT_FIELDS
        if import_type is ImportType.SNAPSHOT
        else HEADERS.keys() - (SNAPSHOT_FIELDS - {"kcr_code"})
    )
    columns = {
        field: index
        for index, value in enumerate(values)
        if (field := HEADER_FIELDS.get(normalized_text(value or ""))) in fields
    }
    code = columns.get("kcr_code")
    if code is None or code > 1 and len(columns) == 1:
        return None
    if (
        import_type is ImportType.REGISTRY
        and code == 1
        and normalized_text(values[1] or "") == "код кцр"
        and all(
            field == "kcr_code" or LEGACY_COLUMNS.get(field, index) == index
            for field, index in columns.items()
        )
    ):
        # The legacy registry labels column B while the identifiers are in A.
        columns["kcr_code"] = 0
    return columns


def field_value(values: tuple[str | None, ...], columns: dict[str, int], field: str) -> str | None:
    index = columns.get(field)
    return values[index] if index is not None else None


def is_total(
    values: tuple[str | None, ...], uncached: tuple[Any, ...], columns: dict[str, int]
) -> bool:
    code = values[columns["kcr_code"]]
    if TOTAL_LABEL.match(normalized_text(code or "")):
        return True
    return not code and (
        any(TOTAL_LABEL.match(normalized_text(value or "")) for value in values[:3])
        or any(
            cell.data_type == "f"
            and re.match(r"=\s*(?:sum|subtotal|aggregate)\(", cell.value, re.I)
            for cell in (uncached[columns[field]] for field in COUNTER_FIELDS if field in columns)
        )
    )


def row_item(
    values: tuple[str | None, ...],
    columns: dict[str, int],
    params: ImportParameters,
) -> ImportItem:
    code = values[columns["kcr_code"]]
    if code is None:
        raise InvalidRow("В строке отсутствует Код КЦР")
    if params.import_type is ImportType.SNAPSHOT:
        original = values[columns["quarter"]] if "quarter" in columns else params.selected_quarter
        if original is None or not QUARTER.fullmatch(original):
            raise InvalidRow("Некорректный исходный квартал документов")
        if original > params.selected_quarter:
            raise InvalidRow("Исходный квартал документов позже квартала наблюдения")
        return ImportItem(
            client=ClientInput(kcr_code=code),
            snapshot=SnapshotInput(
                quarter=original,
                snapshot_date=params.snapshot_date,
                total_documents=counter(
                    field_value(values, columns, "total_documents"), "Всего документов"
                ),
                unsubmitted_documents=counter(
                    field_value(values, columns, "unsubmitted_documents"), "Непереданные документы"
                ),
            ),
        )
    contacts = {
        field: value
        for field in ("phone", "email")
        if field in columns and (value := values[columns[field]]) is not None
    }
    profile = ClientInput(
        kcr_code=code,
        organization_name=field_value(values, columns, "organization_name"),
        inn=field_value(values, columns, "inn"),
        kpp=field_value(values, columns, "kpp"),
        status=client_status(field_value(values, columns, "status")),
        is_blocked=blocked(field_value(values, columns, "is_blocked")),
        manager_name=field_value(values, columns, "manager_name"),
        tags=tags(field_value(values, columns, "tags")),
        raw_work_status=field_value(values, columns, "raw_work_status"),
    )
    if "phone" in columns or "email" in columns:
        profile.contacts = contacts or None
    return ImportItem(client=profile)


def parse_sheet(sheet: Any, raw_sheet: Any, params: ImportParameters, result: ParsedImport) -> None:
    columns = LEGACY_COLUMNS.copy()
    seen: set[tuple[str, str | None]] = set()
    rows: list[tuple[str | None, ...]] = []
    row_numbers: list[int] = []
    invalid_cells: set[int] = set()
    header_found = False
    width = max(12, sheet.max_column or 0)
    # Pandas drops empty records while preserving physical Excel row numbers and object values.
    sheet.reset_dimensions()
    raw_sheet.reset_dimensions()
    for number, (cached, uncached) in enumerate(
        zip(sheet.iter_rows(max_col=width), raw_sheet.iter_rows(max_col=width), strict=True), 1
    ):
        values = tuple(map(cell_text, cached))
        if (header := header_columns(values, params.import_type)) is not None:
            if not header_found:
                header_found = True
                rows.clear()
                row_numbers.clear()
                invalid_cells.clear()
                if any(
                    LEGACY_COLUMNS.get(field, index) != index for field, index in header.items()
                ):
                    columns = header
                else:
                    columns.update(header)
            continue
        if is_total(values, uncached, columns):
            continue
        if any(
            cell.data_type == "e"
            or (value or "").upper() in EXCEL_ERRORS
            or original.data_type == "f"
            and value is None
            for cell, value, original in zip(cached, values, uncached, strict=True)
        ):
            invalid_cells.add(number)
        rows.append(values)
        row_numbers.append(number)

    frame = pd.DataFrame(rows, index=row_numbers, dtype=object)
    populated = frame.notna().any(axis=1) | frame.index.isin(invalid_cells)
    for number, *cells in frame.loc[populated].itertuples(name=None):
        values = tuple(cells)
        result.total_rows += 1
        try:
            if number in invalid_cells:
                raise InvalidRow("Ошибка Excel или отсутствует сохранённое значение формулы")
            item = row_item(values, columns, params)
            key = (item.client.kcr_code, item.snapshot.quarter if item.snapshot else None)
            if key in seen:
                raise InvalidRow("Дубликат строки в листе")
            seen.add(key)
            result.rows.append(SourceRow(sheet=sheet.title, row_number=number, item=item))
        except InvalidRow as exc:
            code = values[columns["kcr_code"]]
            result.errors.append(
                ImportErrorRow(
                    sheet=sheet.title,
                    row_number=number,
                    kcr_code=code if code and code.upper() not in EXCEL_ERRORS else None,
                    reason=str(exc),
                )
            )


def parse_workbook(
    path: Path, params: ImportParameters, expected_digest: str | None = None
) -> ParsedImport:
    result = ParsedImport()
    try:
        if expected_digest:
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != expected_digest:
                    raise InvalidWorkbook("Контрольная сумма файла XLSX изменилась")
        with ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise InvalidWorkbook("Распакованная книга превышает допустимый размер")
            if any(entry.flag_bits & 1 for entry in archive.infolist()):
                raise InvalidWorkbook("Защищённые паролем книги XLSX не поддерживаются")
        cached = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            raw = load_workbook(path, read_only=True, keep_links=False)
            try:
                for sheet in cached.worksheets:
                    reason = sheet_skip_reason(sheet.title, params.selected_quarter)
                    if reason:
                        result.errors.append(
                            ImportErrorRow(sheet=sheet.title, row_number=0, reason=reason)
                        )
                    else:
                        parse_sheet(sheet, raw[sheet.title], params, result)
            finally:
                raw.close()
        finally:
            cached.close()
    except InvalidWorkbook:
        raise
    except (
        BadZipFile,
        EOFError,
        InvalidFileException,
        KeyError,
        NotImplementedError,
        OSError,
        ParseError,
        ValueError,
        ZlibError,
    ) as exc:
        raise InvalidWorkbook("Не удалось прочитать книгу XLSX") from exc
    return result
