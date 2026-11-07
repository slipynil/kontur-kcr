"""Pure merge rules shared by import application and its tests.

The persistence layer must select the inheritance basis at the observation date
and serialize concurrent updates. These functions perform no I/O.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from clients_service.import_models import SnapshotInput


class RowError(Exception):
    def __init__(self, code: str, field: str):
        super().__init__(code)
        self.code = code
        self.field = field


def is_missing(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


@dataclass(frozen=True)
class Source:
    batch_id: UUID
    quarter: str
    snapshot_date: date

    def as_dict(self) -> dict[str, str]:
        return {
            "batch_id": str(self.batch_id),
            "quarter": self.quarter,
            "snapshot_date": self.snapshot_date.isoformat(),
        }


@dataclass(frozen=True)
class ResolvedSnapshot:
    total_documents: int
    unsubmitted_documents: int
    inherited: dict[str, dict[str, str]]


def resolve_snapshot(
    incoming: SnapshotInput,
    *,
    first_total: int | None,
    previous_unsubmitted: int | None,
    previous_source: Source | None,
    previous_inherited: dict[str, dict[str, str]],
) -> ResolvedSnapshot:
    """The caller supplies previous values from this SAME original quarter.

    first_total is the immutable total fixed by the first import. A later input
    total never replaces it. Missing counts without a basis reject the row.
    """
    inherited: dict[str, dict[str, str]] = {}
    total = first_total if first_total is not None else incoming.total_documents
    if total is None:
        raise RowError("MISSING_REQUIRED_VALUE", "total_documents")
    remainder = incoming.unsubmitted_documents
    if remainder is None:
        if previous_unsubmitted is None or previous_source is None:
            raise RowError("MISSING_REQUIRED_VALUE", "unsubmitted_documents")
        remainder = previous_unsubmitted
        inherited["unsubmitted_documents"] = previous_inherited.get(
            "unsubmitted_documents", previous_source.as_dict()
        ).copy()
    # Keeping a fixed total on an ordinary repeat is not a newly missing field.
    if "total_documents" in previous_inherited:
        inherited["total_documents"] = previous_inherited["total_documents"].copy()
    return ResolvedSnapshot(total, remainder, inherited)


def merge_profile_fields(
    current: dict[str, Any],
    supplied: dict[str, Any],
    *,
    sources: dict[str, dict[str, str]],
    inherited: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Merge supplied fields only; an explicit zero/false/empty collection wins.

    Absent fields in a patch do not imply an instruction to inspect every
    optional profile field. Unknown mandatory fields are validated by the caller.
    """
    values = current.copy()
    warnings = {key: value.copy() for key, value in inherited.items()}
    for field, value in supplied.items():
        if is_missing(value):
            if not is_missing(current.get(field)) and field in sources:
                warnings[field] = warnings.get(field, sources[field]).copy()
        else:
            values[field] = value
            warnings.pop(field, None)
    return values, warnings
