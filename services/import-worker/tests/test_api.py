from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from conftest import WorkbookFactory
from openpyxl import load_workbook
from sqlalchemy.exc import SQLAlchemyError

from import_worker.api import create_app
from import_worker.config import Settings
from import_worker.error_report import errors_xlsx
from import_worker.models import ImportErrorRow


@pytest.mark.parametrize(
    ("quarter", "date", "filename", "content", "code"),
    [
        ("2026_Q5", "2026-04-10", "a.xlsx", b"test", "INVALID_IMPORT_REQUEST"),
        ("2026_Q2", "2026-02-30", "a.xlsx", b"test", "INVALID_IMPORT_REQUEST"),
        ("2026_Q2", "2026-04-10", "a.xls", b"test", "INVALID_IMPORT_FILE"),
        ("2026_Q2", "2026-04-10", "a.xlsx", b"test", "INVALID_IMPORT_FILE"),
    ],
)
async def test_invalid_upload(
    tmp_path: Path, quarter: str, date: str, filename: str, content: bytes, code: str
) -> None:
    app = create_app(Settings("postgresql+asyncpg://unused", "amqp://unused", tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://import"
    ) as client:
        response = await client.post(
            "/internal/v1/imports",
            headers={"X-User-Id": str(uuid4())},
            data={"selected_quarter": quarter, "snapshot_date": date, "import_type": "SNAPSHOT"},
            files={"file": (filename, content)},
        )
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("valid", [False, True])
async def test_upload_discards_file_if_rejected_before_commit(
    tmp_path: Path, xlsx: WorkbookFactory, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    insert = AsyncMock(side_effect=SQLAlchemyError("insert failed"))
    monkeypatch.setattr("import_worker.api.insert_batch", insert)
    if valid:
        content = xlsx({"Реестр": [["001"]]})
    else:
        with BytesIO() as output:
            with ZipFile(output, "w") as archive:
                archive.writestr("example.txt", "not a workbook")
            content = output.getvalue()
    app = create_app(Settings("postgresql+asyncpg://unused", "amqp://unused", tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://import"
    ) as client:
        response = await client.post(
            "/internal/v1/imports",
            headers={"X-User-Id": str(uuid4())},
            data={
                "selected_quarter": "2026_Q2",
                "snapshot_date": "2026-04-10",
                "import_type": "REGISTRY",
            },
            files={"file": ("registry.xlsx", content)},
        )
    assert response.status_code == (503 if valid else 400)
    assert insert.await_count == int(valid)
    assert not list(tmp_path.iterdir())


def test_error_report_preserves_text_and_excel_row_number() -> None:
    content = errors_xlsx(
        [
            ImportErrorRow(sheet="=1+1", row_number=4, kcr_code="001", reason="Дубликат"),
            ImportErrorRow(sheet="#N/A", row_number=0, reason="=1+1"),
        ]
    )
    workbook = load_workbook(BytesIO(content), read_only=True)
    try:
        header, first, second = list(workbook.active.iter_rows())
        assert tuple(cell.value for cell in header) == (
            "Лист",
            "Строка Excel",
            "Код КЦР",
            "Причина",
        )
        assert tuple(cell.value for cell in first) == ("=1+1", 4, "001", "Дубликат")
        assert tuple(cell.value for cell in second) == ("#N/A", 0, None, "=1+1")
        assert (
            first[0].data_type
            == first[2].data_type
            == second[0].data_type
            == second[3].data_type
            == "s"
        )
    finally:
        workbook.close()
