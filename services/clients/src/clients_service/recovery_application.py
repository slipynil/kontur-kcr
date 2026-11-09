"""Local recovery only. Import must authorize the globally latest batch."""

import hashlib
import json

from pydantic import TypeAdapter
from sqlalchemy import MetaData, Table, delete, insert, select, text, update
from sqlalchemy.engine import Engine

from clients_service.import_application import IMPORT_LOCK, Repository, json_value
from clients_service.import_models import Quarter
from clients_service.recovery_models import RecoveryCommand


class RecoveryConflict(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ImportNotFound(Exception):
    pass


RESTORABLE = {
    "clients",
    "client_tags",
    "quarter_snapshots",
    "snapshot_field_provenance",
    "quarter_snapshot_history",
}


def restore_rows(connection, table, rows, *, upsert_client=False):
    """Decode stored UUID/date/timestamp fields using PostgreSQL record types."""
    if table.name not in RESTORABLE:
        raise ValueError("Unexpected recovery table")
    if not rows:
        return 0
    identity = " OVERRIDING SYSTEM VALUE" if table.name == "quarter_snapshot_history" else ""
    statement = (
        f"INSERT INTO {table.name}{identity} SELECT * FROM "
        f"jsonb_populate_recordset(NULL::{table.name}, CAST(:records AS jsonb))"
    )
    if upsert_client:
        assignments = ", ".join(
            f"{column.name}=EXCLUDED.{column.name}"
            for column in table.c
            if column.name != "kcr_code"
        )
        statement += " ON CONFLICT (kcr_code) DO UPDATE SET " + assignments
    elif table.name == "quarter_snapshot_history":
        statement += " ON CONFLICT (id) DO NOTHING"
    return connection.execute(text(statement), {"records": json.dumps(rows)}).rowcount


def _restore_client(repo, undo):
    code = undo["kcr_code"]
    state = undo["previous_state"]
    connection = repo.connection
    if not state["clients"]:
        return connection.execute(
            delete(repo.clients).where(repo.clients.c.kcr_code == code)
        ).rowcount
    restore_rows(connection, repo.clients, state["clients"], upsert_client=True)
    # Preserve existing older history while restoring all mutable current values.
    for table in (repo.snapshot_field_provenance, repo.quarter_snapshots, repo.client_tags):
        connection.execute(delete(table).where(table.c.kcr_code == code))
    for table in (repo.quarter_snapshots, repo.client_tags, repo.snapshot_field_provenance):
        restore_rows(connection, table, state[table.name])
    return 0


def _rollback(repo, batch_id, operation_id):
    connection, operations = repo.connection, repo.clients_import_operations
    batch = (
        connection.execute(select(operations).where(operations.c.batch_id == batch_id))
        .mappings()
        .first()
    )
    if batch is None:
        raise ImportNotFound
    if batch["rolled_back"]:
        return {
            **batch["rollback_result"],
            "operation_id": str(operation_id),
            "already_rolled_back": True,
        }
    active = connection.execute(
        select(operations.c.batch_id, operations.c.apply_order)
        .where(operations.c.rolled_back.is_(False))
        .order_by(operations.c.apply_order.desc().nulls_last())
    ).all()
    if batch["apply_order"] is None:
        if any(row.apply_order is not None for row in active):
            raise RecoveryConflict("IMPORT_NOT_LATEST")
        if len(active) > 1:
            raise RecoveryConflict("IMPORT_ORDER_UNKNOWN")
    elif active[0].batch_id != batch_id:
        raise RecoveryConflict("IMPORT_NOT_LATEST")
    undo_rows = (
        connection.execute(
            select(repo.clients_import_undo).where(repo.clients_import_undo.c.batch_id == batch_id)
        )
        .mappings()
        .all()
    )
    result = {
        "operation_id": str(operation_id),
        "batch_id": str(batch_id),
        "local_only": True,
        "already_rolled_back": False,
        "removed_clients": 0,
        "restored_clients": 0,
        "removed_snapshots": 0,
        "restored_snapshots": 0,
        "removed_history_records": 0,
        "restored_history_records": 0,
    }
    history = repo.quarter_snapshot_history
    # Delete target observations before restoring deleted older history.
    result["removed_history_records"] = connection.execute(
        delete(history).where(history.c.import_batch_id == batch_id)
    ).rowcount
    for undo in undo_rows:
        code = undo["kcr_code"]
        previous = undo["previous_state"]["quarter_snapshots"]
        previous_periods = {row["quarter"] for row in previous}
        result["removed_snapshots"] += sum(
            row["quarter"] not in previous_periods
            for row in repo.rows(repo.quarter_snapshots, code)
        )
        result["removed_clients"] += _restore_client(repo, undo)
        if not undo["previous_state"]["clients"]:
            continue
        result["restored_clients"] += 1
        result["restored_snapshots"] += len(previous)
        deleted_rows = (
            connection.execute(
                text("""
            SELECT h.payload FROM clients_deleted_history h
            JOIN clients_data_operations d ON d.operation_id=h.operation_id
            WHERE h.kcr_code=:code AND d.change_order > :applied_order
              AND CAST(h.payload->>'import_batch_id' AS uuid) != :batch_id
              AND NOT EXISTS (
                SELECT 1 FROM clients_import_operations i
                WHERE i.batch_id=CAST(h.payload->>'import_batch_id' AS uuid) AND i.rolled_back
              )
            ORDER BY d.change_order DESC
        """),
                {"code": code, "applied_order": batch["apply_order"] or 0, "batch_id": batch_id},
            )
            .scalars()
            .all()
        )
        # A record may have been deleted more than once after an intermediate restore.
        unique_rows = {row["id"]: row for row in deleted_rows}
        result["restored_history_records"] += restore_rows(
            connection,
            history,
            list(unique_rows.values()),
        )
    connection.execute(
        update(operations)
        .where(operations.c.batch_id == batch_id)
        .values(
            rolled_back=True,
            rollback_result=result,
        )
    )
    return result


def _delete(repo, audit_id, code, quarter):
    connection, history = repo.connection, repo.quarter_snapshot_history
    predicate = history.c.kcr_code == code
    if quarter is not None:
        predicate &= history.c.quarter == quarter
    rows = [dict(row) for row in connection.execute(select(history).where(predicate)).mappings()]
    archive = Table("clients_deleted_history", MetaData(), autoload_with=connection)
    for row in rows:
        connection.execute(
            insert(archive).values(
                operation_id=audit_id,
                history_id=row["id"],
                kcr_code=code,
                quarter=row["quarter"],
                payload=json_value(row),
            )
        )
    snapshots = repo.rows(repo.quarter_snapshots, code)
    result = {
        "operation_id": str(audit_id),
        "kcr_code": code,
        "quarter": quarter,
        "local_only": True,
        "removed_clients": 0,
        "removed_snapshots": sum(quarter is None or row["quarter"] == quarter for row in snapshots),
        "removed_history_records": len(rows),
    }
    if quarter is None:
        result["removed_clients"] = connection.execute(
            delete(repo.clients).where(repo.clients.c.kcr_code == code)
        ).rowcount
    else:
        connection.execute(delete(history).where(predicate))
        connection.execute(
            delete(repo.quarter_snapshots).where(
                (repo.quarter_snapshots.c.kcr_code == code)
                & (repo.quarter_snapshots.c.quarter == quarter)
            )
        )
        client = repo.rows(repo.clients, code)
        if client:
            warnings = {
                key: value
                for key, value in client[0]["inherited_fields"].items()
                if not key.startswith(f"snapshots.{quarter}.")
            }
            connection.execute(
                update(repo.clients)
                .where(repo.clients.c.kcr_code == code)
                .values(
                    inherited_fields=warnings,
                    needs_review=bool(warnings),
                    updated_at=text("now()"),
                )
            )
    return result


def execute_recovery(
    engine: Engine, command: RecoveryCommand, *, action: str, batch_id=None, code=None, quarter=None
):
    if action not in {"ROLLBACK", "DELETE_CLIENT", "DELETE_QUARTER"}:
        raise ValueError("Unexpected recovery action")
    if action == "ROLLBACK":
        if batch_id is None or code is not None or quarter is not None:
            raise ValueError("Rollback requires only a batch ID")
    else:
        if not isinstance(code, str) or not code.strip() or batch_id is not None:
            raise ValueError("Deletion requires a client code")
        if action == "DELETE_QUARTER":
            TypeAdapter(Quarter).validate_python(quarter)
        elif quarter is not None:
            raise ValueError("Client deletion has no quarter")
    payload = {
        "action": action,
        "batch_id": str(batch_id) if batch_id else None,
        "kcr_code": code,
        "quarter": quarter,
        "requested_by": str(command.requested_by),
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK})
        audit = Table("clients_data_operations", MetaData(), autoload_with=connection)
        stored = (
            connection.execute(select(audit).where(audit.c.operation_id == command.operation_id))
            .mappings()
            .first()
        )
        if stored:
            if stored["payload_hash"] != fingerprint:
                raise RecoveryConflict("OPERATION_PAYLOAD_CONFLICT")
            return stored["result"]
        connection.execute(
            insert(audit).values(
                operation_id=command.operation_id,
                payload_hash=fingerprint,
                action=action,
                kcr_code=code,
                quarter=quarter,
                batch_id=batch_id,
                requested_by=command.requested_by,
                result={},
            )
        )
        repo = Repository(connection)
        if action == "ROLLBACK":
            result = _rollback(repo, batch_id, command.operation_id)
        else:
            result = _delete(repo, command.operation_id, code, quarter)
        connection.execute(
            update(audit)
            .where(audit.c.operation_id == command.operation_id)
            .values(
                result=result,
            )
        )
        return result
