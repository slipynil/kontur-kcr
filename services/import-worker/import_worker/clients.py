import hashlib
import json
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from import_worker.models import ImportParameters, ParsedImport, SourceRow


class ClientsRowError(BaseModel):
    row_number: int = Field(ge=1)
    code: str
    field: str = ""


class ApplyResult(BaseModel):
    batch_id: UUID
    accepted_rows: int = Field(ge=0)
    rejected_rows: int = Field(ge=0)
    errors: list[ClientsRowError]


class ApplyRejected(Exception):
    pass


def fingerprint(params: ImportParameters, row: SourceRow) -> str:
    normalized = {
        **params.model_dump(mode="json"),
        "item": row.item.model_dump(mode="json", exclude_unset=True),
    }
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def apply_import(
    client: httpx.AsyncClient,
    batch_id: UUID,
    uploaded_by: UUID,
    params: ImportParameters,
    parsed: ParsedImport,
) -> ApplyResult:
    response = await client.post(
        "/internal/v1/import/apply",
        json={
            **params.model_dump(mode="json"),
            "batch_id": str(batch_id),
            "uploaded_by": str(uploaded_by),
            "items": [row.item.model_dump(mode="json", exclude_unset=True) for row in parsed.rows],
        },
    )
    if response.status_code in {400, 422}:
        raise ApplyRejected
    response.raise_for_status()
    result = ApplyResult.model_validate(response.json())
    indices = {error.row_number for error in result.errors}
    if (
        result.batch_id != batch_id
        or result.accepted_rows + result.rejected_rows != len(parsed.rows)
        or result.rejected_rows != len(result.errors)
        or len(indices) != result.rejected_rows
        or max(indices, default=0) > len(parsed.rows)
    ):
        raise ValueError("Несогласованный отчёт Clients Service")
    return result
