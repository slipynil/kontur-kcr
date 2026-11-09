from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from import_worker.models import ImportErrorRow

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def errors_xlsx(errors: list[ImportErrorRow]) -> bytes:
    with BytesIO() as output:
        workbook = Workbook(write_only=True)
        try:
            sheet = workbook.create_sheet("Ошибки импорта")
            sheet.append(("Лист", "Строка Excel", "Код КЦР", "Причина"))
            for error in errors:
                row = []
                for value in (error.sheet, error.row_number, error.kcr_code, error.reason):
                    cell = WriteOnlyCell(sheet, value=value)
                    if isinstance(value, str):
                        cell.data_type = "s"
                    row.append(cell)
                sheet.append(row)
            workbook.save(output)
        finally:
            workbook.close()
        return output.getvalue()
