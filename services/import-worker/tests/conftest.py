from collections.abc import Callable
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

type WorkbookFactory = Callable[[dict[str, list[list[object]]]], bytes]


@pytest.fixture
def db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def sessions(db: AsyncMock) -> MagicMock:
    factory = MagicMock(spec=async_sessionmaker)
    factory.begin.return_value.__aenter__.return_value = db
    return factory


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
