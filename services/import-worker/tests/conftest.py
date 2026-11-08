from collections.abc import Callable
from io import BytesIO

import pytest
from openpyxl import Workbook

type WorkbookFactory = Callable[[dict[str, list[list[object]]]], bytes]


@pytest.fixture
def xlsx() -> WorkbookFactory:
    def create(sheets: dict[str, list[list[object]]]) -> bytes:
        book = Workbook()
        book.remove(book.active)
        for name, rows in sheets.items():
            sheet = book.create_sheet(name)
            for row in rows:
                sheet.append(row)
        output = BytesIO()
        book.save(output)
        book.close()
        return output.getvalue()

    return create
