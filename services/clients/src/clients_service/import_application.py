"""Atomic local application. Global completion is coordinated by Import."""

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import MetaData, Table, delete, insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DataError

from clients_service.import_models import ImportEnvelope, ImportItem, ImportType
from clients_service.import_rules import (
    RowError,
    Source,
    is_missing,
    merge_profile_fields,
    resolve_snapshot,
)

# Shared by all local writes, including future rollback/delete implementations.
IMPORT_LOCK = 4267590301
COUNTERS = (
    "created_clients",
    "updated_clients",
    "created_snapshots",
    "updated_snapshots",
    "created_history_records",
)


class PayloadConflict(Exception):
    pass


def json_value(value: Any) -> Any:
    """Serialize undo without relying on an engine-specific JSON encoder."""

    def default(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, UUID):
            return str(item)
        raise TypeError(type(item).__name__)

    return json.loads(json.dumps(value, default=default, allow_nan=False))


class Repository:
    def __init__(self, connection: Connection):
        self.connection = connection
        metadata = MetaData()
        for name in (
            "clients",
            "tags",
            "client_tags",
            "quarter_snapshots",
            "quarter_snapshot_history",
            "snapshot_field_provenance",
            "clients_import_operations",
            "clients_import_undo",
        ):
            setattr(self, name, Table(name, metadata, autoload_with=connection))

    def rows(self, table, code):
        return [
            dict(row)
            for row in self.connection.execute(
                select(table).where(table.c.kcr_code == code)
            ).mappings()
        ]

    def before(self, code):
        return json_value(
            {
                name: self.rows(getattr(self, name), code)
                for name in (
                    "clients",
                    "client_tags",
                    "quarter_snapshots",
                    "snapshot_field_provenance",
                )
            }
        )

    def apply_row(self, envelope: ImportEnvelope, item: ImportItem):
        conn = self.connection
        code = item.client.kcr_code
        existing = self.rows(self.clients, code)
        if not existing and envelope.import_type == ImportType.SNAPSHOT:
            raise RowError("UNKNOWN_KCR_CODE", "client.kcr_code")
        if envelope.import_type == ImportType.SNAPSHOT and item.snapshot is None:
            raise RowError("SNAPSHOT_REQUIRED", "snapshot")
        if envelope.import_type == ImportType.REGISTRY and item.snapshot is not None:
            raise RowError("UNEXPECTED_SNAPSHOT", "snapshot")
        before = self.before(code)
        delta = dict.fromkeys(COUNTERS, 0)
        observation_date = (
            item.snapshot.snapshot_date if item.snapshot else None
        ) or envelope.snapshot_date
        origin = Source(envelope.batch_id, envelope.selected_quarter, observation_date)
        profile = dict(existing[0]) if existing else {}
        tag_codes = list(
            conn.execute(
                select(self.tags.c.code)
                .join(self.client_tags)
                .where(self.client_tags.c.kcr_code == code)
            ).scalars()
        )
        current = {**profile, "tags": tag_codes}
        sources = profile.get("field_sources", {}).copy()
        supplied = item.client.model_dump(mode="json", exclude_unset=True, exclude={"kcr_code"})
        # An older field cannot replace or confirm a newer current field.
        supplied = {
            key: value
            for key, value in supplied.items()
            if sources.get(key, {}).get("snapshot_date", "") <= observation_date.isoformat()
        }
        values, warnings = merge_profile_fields(
            current,
            supplied,
            sources=sources,
            inherited=profile.get("inherited_fields", {}),
        )
        changes = {}
        for field, value in supplied.items():
            if not is_missing(value):
                sources[field] = origin.as_dict()
                if field != "tags":
                    changes[field] = values[field]
        changes.update(
            field_sources=sources, inherited_fields=warnings, needs_review=bool(warnings)
        )
        if existing:
            conn.execute(
                update(self.clients)
                .where(self.clients.c.kcr_code == code)
                .values(
                    **changes,
                    updated_at=text("now()"),
                )
            )
            delta["updated_clients"] = 1
        else:
            conn.execute(insert(self.clients).values(kcr_code=code, source="REGISTRY", **changes))
            delta["created_clients"] = 1
        if "tags" in supplied and not is_missing(supplied["tags"]):
            conn.execute(delete(self.client_tags).where(self.client_tags.c.kcr_code == code))
            for tag in set(values["tags"]):
                tag_id = conn.execute(
                    select(self.tags.c.id).where(self.tags.c.code == tag)
                ).scalar_one()
                conn.execute(insert(self.client_tags).values(kcr_code=code, tag_id=tag_id))
        history_id = None
        if item.snapshot is not None:
            history_id = self.apply_snapshot(envelope, item, origin, warnings, delta)
        return before, delta, history_id

    def apply_snapshot(self, envelope, item, origin, warnings, delta):
        conn = self.connection
        snapshot = item.snapshot
        code, quarter = item.client.kcr_code, snapshot.quarter
        table, history = self.quarter_snapshots, self.quarter_snapshot_history
        where = (table.c.kcr_code == code) & (table.c.quarter == quarter)
        current = conn.execute(select(table).where(where)).mappings().first()
        previous = (
            conn.execute(
                select(history)
                .where(
                    (history.c.kcr_code == code)
                    & (history.c.quarter == quarter)
                    & (history.c.snapshot_date <= origin.snapshot_date)
                )
                .order_by(history.c.snapshot_date.desc(), history.c.observation_order.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        previous_source = (
            Source(
                previous["import_batch_id"],
                previous["observation_quarter"],
                previous["snapshot_date"],
            )
            if previous
            else None
        )
        resolved = resolve_snapshot(
            snapshot,
            first_total=current["total_documents"] if current else None,
            previous_unsubmitted=previous["unsubmitted_documents"] if previous else None,
            previous_source=previous_source,
            previous_inherited=previous["inherited_fields"] if previous else {},
        )
        history_id = uuid4()
        conn.execute(
            insert(history).values(
                id=history_id,
                kcr_code=code,
                quarter=quarter,
                observation_quarter=envelope.selected_quarter,
                unsubmitted_documents=resolved.unsubmitted_documents,
                import_batch_id=envelope.batch_id,
                snapshot_date=origin.snapshot_date,
                inherited_fields=resolved.inherited,
            )
        )
        delta["created_history_records"] = 1
        if current is None or origin.snapshot_date >= current["snapshot_date"]:
            payload = dict(
                total_documents=resolved.total_documents,
                unsubmitted_documents=resolved.unsubmitted_documents,
                last_observation_quarter=envelope.selected_quarter,
                import_batch_id=envelope.batch_id,
                snapshot_date=origin.snapshot_date,
            )
            if current is None:
                conn.execute(insert(table).values(kcr_code=code, quarter=quarter, **payload))
                delta["created_snapshots"] = 1
            else:
                conn.execute(update(table).where(where).values(**payload, updated_at=text("now()")))
                delta["updated_snapshots"] = 1
            provenance = self.snapshot_field_provenance
            conn.execute(
                delete(provenance).where(
                    (provenance.c.kcr_code == code) & (provenance.c.quarter == quarter)
                )
            )
            prefix = f"snapshots.{quarter}."
            warnings = {key: value for key, value in warnings.items() if not key.startswith(prefix)}
            for field, source in resolved.inherited.items():
                conn.execute(
                    insert(provenance).values(
                        kcr_code=code,
                        quarter=quarter,
                        field_name=field,
                        source_batch_id=UUID(source["batch_id"]),
                        source_quarter=source["quarter"],
                        source_snapshot_date=date.fromisoformat(source["snapshot_date"]),
                    )
                )
                warnings[prefix + field] = source
            conn.execute(
                update(self.clients)
                .where(self.clients.c.kcr_code == code)
                .values(
                    inherited_fields=warnings,
                    needs_review=bool(warnings),
                )
            )
        return history_id


def apply_import(engine: Engine, envelope: ImportEnvelope) -> dict[str, Any]:
    canonical = json.dumps(
        envelope.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK})
        repo = Repository(connection)
        operations = repo.clients_import_operations
        stored = (
            connection.execute(select(operations).where(operations.c.batch_id == envelope.batch_id))
            .mappings()
            .first()
        )
        if stored:
            if stored["payload_hash"] != fingerprint:
                raise PayloadConflict
            return stored["result"]
        result = dict.fromkeys(COUNTERS, 0)
        result.update(batch_id=str(envelope.batch_id), accepted_rows=0, rejected_rows=0, errors=[])
        undo = {}
        seen = set()
        created_clients = set()
        updated_clients = set()
        for row_number, raw in enumerate(envelope.items, start=1):
            try:
                item = ImportItem.model_validate(raw)
                key = (item.client.kcr_code, item.snapshot.quarter if item.snapshot else None)
                if key in seen:
                    raise RowError("DUPLICATE_ROW", "client.kcr_code")
                with connection.begin_nested():
                    before, delta, history_id = repo.apply_row(envelope, item)
                seen.add(key)
                code = item.client.kcr_code
                state = undo.setdefault(code, {"previous_state": before, "created_history_ids": []})
                if history_id:
                    state["created_history_ids"].append(str(history_id))
                for counter in COUNTERS:
                    if counter not in ("created_clients", "updated_clients"):
                        result[counter] += delta[counter]
                if delta["created_clients"]:
                    created_clients.add(code)
                elif delta["updated_clients"]:
                    updated_clients.add(code)
                result["accepted_rows"] += 1
            except ValidationError as error:
                result["errors"].append(
                    {
                        "row_number": row_number,
                        "code": "INVALID_ROW",
                        "field": ".".join(map(str, error.errors()[0]["loc"])),
                    }
                )
            except RowError as error:
                result["errors"].append(
                    {
                        "row_number": row_number,
                        "code": error.code,
                        "field": error.field,
                    }
                )
            except DataError:
                # PostgreSQL text/JSON encoding failures concern this row only.
                # Keep diagnostics and input values out of the public report.
                result["errors"].append(
                    {"row_number": row_number, "code": "INVALID_ROW_DATA", "field": ""}
                )
        result["rejected_rows"] = len(result["errors"])
        result["created_clients"] = len(created_clients)
        result["updated_clients"] = len(updated_clients - created_clients)
        connection.execute(
            insert(operations).values(
                batch_id=envelope.batch_id,
                payload_hash=fingerprint,
                result=result,
            )
        )
        for code, state in undo.items():
            connection.execute(
                insert(repo.clients_import_undo).values(
                    batch_id=envelope.batch_id,
                    kcr_code=code,
                    **state,
                )
            )
        return result
