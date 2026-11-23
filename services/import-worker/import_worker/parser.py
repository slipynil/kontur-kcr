import hashlib
import re
from itertools import zip_longest
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter
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
    REGISTRY_STATUS_TAGS,
    InvalidRow,
    counter,
    normalized_text,
)

REGISTRY_HEADERS = (
    "Код КЦР",
    "Название",
    "СЦ",
    "КПП",
    "ИНН",
    "ФИО менеджера",
    "ФИО менеджера КП",
    "статус клиента",
)
REGISTRY_WIDTH = len(REGISTRY_HEADERS)
HEADERS = {
    "kcr_code": ("код кцр", "kcr_code"),
    "total_documents": ("всего", "всего документов", "документов всего", "total_documents"),
    "unsubmitted_documents": (
        "непередано",
        "непереданные документы",
        "непереданные оригиналы документов",
        "неотправленные документы",
        "не отправлено документов",
        "всего не отправлено документов",
        "unsubmitted_documents",
    ),
    "quarter": ("quarter", "исходный квартал"),
}
HEADER_FIELDS = {alias: field for field, aliases in HEADERS.items() for alias in aliases}
IGNORED_HEADERS = {"блокировка", "блокировка в сервисе", "is_blocked"}
COUNTER_HEADERS = {
    "total_documents": "Документов всего",
    "unsubmitted_documents": "Всего не отправлено документов",
}
REQUIRED_SNAPSHOT_HEADERS = {"kcr_code": "Код КЦР", **COUNTER_HEADERS}
ANONYMIZED_CODE_HEADERS = {
    "уникальный обезличенный идентификатор",
    "уникальны обезличинный идентификатор",
}
ANNUAL_TITLE = re.compile(r"(?:(?:весь|годовой|годовая|годовые)\s+)?[0-9]{4}(?:\s*(?:г\.?|год))?")
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


def sheet_skip_reason(
    title: str, selected_quarter: str | None, *, allow_annual: bool = False
) -> str | None:
    title = normalized_text(title)
    if ANNUAL_TITLE.fullmatch(title):
        return None if allow_annual else "Годовой, составной или служебный лист пропущен"
    quarters = QUARTER_IN_TITLE.findall(title)
    if len(quarters) > 1 or AGGREGATE_TITLE.search(title):
        return "Годовой, составной или служебный лист пропущен"
    if quarters and selected_quarter is not None:
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


def has_excel_error(cell: Any, value: str | None, original: Any) -> bool:
    return (
        cell.data_type == "e"
        or (value or "").upper() in EXCEL_ERRORS
        or original.data_type == "f"
        and value is None
    )


def registry_header_error(sheet: Any) -> ImportErrorRow | None:
    sheet.reset_dimensions()
    cells = next(sheet.iter_rows(max_row=1), ())
    values = map(cell_text, cells)
    for index, (expected, value) in enumerate(zip_longest(REGISTRY_HEADERS, values), 1):
        if expected is None and value is not None:
            reason = "лишний столбец в реестре"
        elif expected is not None and (value or "").casefold() != expected.casefold():
            reason = f"ожидается заголовок «{expected}»"
        else:
            continue
        return ImportErrorRow(
            sheet=sheet.title, row_number=1, reason=f"{get_column_letter(index)}1 — {reason}"
        )
    return None


def registry_row_error(
    cached: tuple[Any, ...], values: tuple[str | None, ...], uncached: tuple[Any, ...]
) -> tuple[int, str] | None:
    for index, (header, cell, value, original) in enumerate(
        zip_longest(REGISTRY_HEADERS, cached, values, uncached), 1
    ):
        if header is None:
            if value is not None or original is not None and original.value is not None:
                return index, "лишний столбец в реестре"
            continue
        if cell is not None and has_excel_error(cell, value, original):
            return index, f"«{header}» содержит ошибку Excel или формулу без результата"
        if index == 1 and value is None:
            return index, "отсутствует код КЦР"
        if index == REGISTRY_WIDTH and value not in REGISTRY_STATUS_TAGS:
            return index, "недопустимый статус клиента"
    return None


def parse_registry_sheet(sheet: Any, raw_sheet: Any, result: ParsedImport) -> None:
    raw_sheet.reset_dimensions()
    seen: set[str] = set()
    for number, (cached, uncached) in enumerate(
        zip(sheet.iter_rows(min_row=2), raw_sheet.iter_rows(min_row=2), strict=True), 2
    ):
        values = tuple(map(cell_text, cached))
        if not any(values) and not any(cell.data_type == "f" for cell in uncached):
            continue
        normalized = tuple(value.casefold() if value is not None else None for value in values)
        code_key = normalized[0]
        if TOTAL_LABEL.match(code_key or "") or (
            code_key is None and any(TOTAL_LABEL.match(value or "") for value in normalized[1:3])
        ):
            continue
        result.total_rows += 1
        code = values[0]
        error = registry_row_error(cached, normalized, uncached)
        if error is None and code_key in seen:
            error = (1, "дубликат кода КЦР в листе")
        if error is not None:
            index, reason = error
            valid_code = code and not has_excel_error(cached[0], code, uncached[0])
            result.errors.append(
                ImportErrorRow(
                    sheet=sheet.title,
                    row_number=number,
                    kcr_code=code if valid_code else None,
                    reason=f"{get_column_letter(index)}{number} — {reason}",
                )
            )
            continue
        assert code is not None and code_key is not None
        _, name, _, kpp, inn, manager, _, status = values[:REGISTRY_WIDTH]
        status_key = normalized[REGISTRY_WIDTH - 1]
        assert status_key is not None
        client = ClientInput(
            kcr_code=code,
            organization_name=name,
            kpp=kpp,
            inn=inn,
            manager_name=manager,
            tags=[REGISTRY_STATUS_TAGS[status_key]],
            raw_work_status=status,
        )
        seen.add(code_key)
        result.rows.append(
            SourceRow(sheet=sheet.title, row_number=number, item=ImportItem(client=client))
        )


def snapshot_header_columns(headers: tuple[str, ...]) -> dict[str, list[int]]:
    columns: dict[str, list[int]] = {}
    identifiers = []
    for index, header in enumerate(headers):
        if (field := HEADER_FIELDS.get(header)) is not None:
            columns.setdefault(field, []).append(index)
        elif header in ANONYMIZED_CODE_HEADERS:
            identifiers.append(index)
    if identifiers:
        columns.setdefault("kcr_code", identifiers)
    return columns


def find_snapshot_header(
    sheet: Any, filename: str, result: ParsedImport
) -> tuple[int, tuple[str, ...], dict[str, int]] | None:
    first_populated_row = 0
    for number, cells in enumerate(sheet.iter_rows(), 1):
        headers = tuple(normalized_text(cell_text(cell) or "") for cell in cells)
        if any(headers) and not first_populated_row:
            first_populated_row = number
        columns = snapshot_header_columns(headers)
        if not columns:
            continue

        missing = [
            label for field, label in REQUIRED_SNAPSHOT_HEADERS.items() if field not in columns
        ]
        reasons = [f"отсутствует заголовок «{label}»" for label in missing]
        reasons.extend(
            f"{get_column_letter(index + 1)}{number} — повторяется заголовок «{headers[index]}»"
            for indices in columns.values()
            for index in indices[1:]
        )
        if not reasons:
            return number, headers, {field: indices[0] for field, indices in columns.items()}
        break
    else:
        number = first_populated_row or 1
        expected = ", ".join(REQUIRED_SNAPSHOT_HEADERS.values())
        reasons = [f"не найдена строка заголовков. Ожидаются «{expected}»"]

    result.errors.extend(
        ImportErrorRow(sheet=sheet.title, row_number=number, reason=f"Файл «{filename}» — {reason}")
        for reason in reasons
    )
    return None


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
            and isinstance(cell.value, str)
            and re.match(r"=\s*(?:sum|subtotal|aggregate)\(", cell.value, re.I)
            for cell in (uncached[columns[field]] for field in COUNTER_HEADERS)
        )
    )


def snapshot_item(
    values: tuple[str | None, ...],
    columns: dict[str, int],
    params: ImportParameters,
    counters: dict[str, int | None],
) -> ImportItem:
    code = values[columns["kcr_code"]]
    if code is None:
        raise InvalidRow("В строке отсутствует Код КЦР")
    assert params.selected_quarter is not None and params.snapshot_date is not None
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
            **counters,
        ),
    )


def parse_snapshot_sheet(
    sheet: Any, raw_sheet: Any, params: ImportParameters, result: ParsedImport, filename: str
) -> None:
    width = sheet.max_column or 0
    sheet.reset_dimensions()
    header = find_snapshot_header(sheet, filename, result)
    if header is None:
        return
    header_row, headers, columns = header
    width = max(width, len(headers))
    ignored = {index for index, name in enumerate(headers) if name in IGNORED_HEADERS}
    seen: set[tuple[str, str]] = set()
    raw_sheet.reset_dimensions()
    start_row = header_row + 1
    for number, (cached, uncached) in enumerate(
        zip(
            sheet.iter_rows(min_row=start_row, max_col=width),
            raw_sheet.iter_rows(min_row=start_row, max_col=width),
            strict=True,
        ),
        start_row,
    ):
        values = tuple(map(cell_text, cached))
        values = tuple(None if index in ignored else value for index, value in enumerate(values))
        if is_total(values, uncached, columns):
            continue
        code_index = columns["kcr_code"]
        if normalized_text(values[code_index] or "") == headers[code_index] and all(
            normalized_text(values[i] or "") == headers[i] for i in columns.values()
        ):
            continue
        invalid_cells = {
            index
            for index, (cell, value, original) in enumerate(
                zip(cached, values, uncached, strict=True)
            )
            if index not in ignored and has_excel_error(cell, value, original)
        }
        if not any(values) and not invalid_cells:
            continue
        result.total_rows += 1
        code = values[code_index] if code_index not in invalid_cells else None
        counters: dict[str, int | None] = {}
        for field, label in COUNTER_HEADERS.items():
            index = columns[field]
            try:
                if index in invalid_cells:
                    raise InvalidRow(f"{label} содержит ошибку Excel или формулу без результата")
                counters[field] = counter(cached[index].value, label)
            except InvalidRow as exc:
                address = f"{get_column_letter(index + 1)}{number}"
                result.errors.append(
                    ImportErrorRow(
                        sheet=sheet.title,
                        row_number=number,
                        kcr_code=code,
                        reason=f"Файл «{filename}», {address} — {exc}",
                    )
                )
        if len(counters) != len(COUNTER_HEADERS):
            continue
        try:
            if invalid_cells:
                raise InvalidRow("Ошибка Excel или отсутствует сохранённое значение формулы")
            item = snapshot_item(values, columns, params, counters)
            assert item.snapshot is not None
            key = (item.client.kcr_code, item.snapshot.quarter)
            if key in seen:
                raise InvalidRow("Дубликат строки в листе")
            seen.add(key)
            result.rows.append(SourceRow(sheet=sheet.title, row_number=number, item=item))
        except InvalidRow as exc:
            result.errors.append(
                ImportErrorRow(
                    sheet=sheet.title,
                    row_number=number,
                    kcr_code=code,
                    reason=str(exc),
                )
            )


def parse_workbook(
    path: Path,
    params: ImportParameters,
    expected_digest: str | None = None,
    *,
    filename: str | None = None,
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
                sheets = cached.worksheets
                if params.import_type is ImportType.REGISTRY:
                    for sheet in sheets:
                        if (error := registry_header_error(sheet)) is not None:
                            result.errors.append(error)
                    if not result.errors:
                        for sheet in sheets:
                            parse_registry_sheet(sheet, raw[sheet.title], result)
                    return result

                for sheet in sheets:
                    reason = sheet_skip_reason(
                        sheet.title, params.selected_quarter, allow_annual=len(sheets) == 1
                    )
                    if reason:
                        result.errors.append(
                            ImportErrorRow(sheet=sheet.title, row_number=0, reason=reason)
                        )
                    else:
                        parse_snapshot_sheet(
                            sheet, raw[sheet.title], params, result, filename or path.name
                        )
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
