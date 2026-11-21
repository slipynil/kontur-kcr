from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command as migration
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from clients_service.config import Settings
from clients_service.import_application import ImportAlreadyRolledBack, ImportNotFound, apply_import
from clients_service.import_models import ImportEnvelope
from clients_service.main import create_app
from clients_service.read_repository import ClientNotFound, get_card, get_history
from clients_service.recovery_application import (
    RecoveryConflict,
    execute_recovery,
)
from clients_service.recovery_models import RecoveryCommand
from clients_service.recovery_retention import purge_expired_copies

pytestmark = pytest.mark.postgres


def command():
    return RecoveryCommand(operation_id=uuid4(), requested_by=uuid4())


def snapshot(quarter, remainder):
    return {
        "client": {"kcr_code": "a"},
        "snapshot": {
            "quarter": quarter,
            "total_documents": 100,
            "unsubmitted_documents": remainder,
        },
    }


@pytest.fixture
def setup(database):
    engine, config, url = database
    migration.upgrade(config, "head")

    def apply(items, *, kind="SNAPSHOT", day="2026-04-10"):
        metadata = (
            {"selected_quarter": "2026_Q2", "snapshot_date": day} if kind == "SNAPSHOT" else {}
        )
        envelope = ImportEnvelope(
            batch_id=uuid4(),
            uploaded_by=uuid4(),
            import_type=kind,
            items=items,
            **metadata,
        )
        result = apply_import(engine, envelope)
        assert result["rejected_rows"] == 0, result
        return envelope

    registry = apply(
        [
            {"client": {"kcr_code": "a", "organization_name": "before", "tags": ["VIP"]}},
            {"client": {"kcr_code": "b", "organization_name": "unaffected"}},
        ],
        kind="REGISTRY",
        day="2026-04-01",
    )
    return engine, url, apply, registry


def rollback(engine, envelope, cmd=None):
    return execute_recovery(engine, cmd or command(), action="ROLLBACK", batch_id=envelope.batch_id)


def delete(engine, *, code="a", quarter=None, cmd=None):
    return execute_recovery(
        engine,
        cmd or command(),
        code=code,
        quarter=quarter,
        action="DELETE_QUARTER" if quarter else "DELETE_CLIENT",
    )


def test_restore_values_tags_and_remove_created_client(setup):
    engine, _, apply, _ = setup
    baseline = get_card(engine, "a").model_dump()
    envelope = apply(
        [
            {"client": {"kcr_code": "a", "organization_name": "after", "tags": []}},
            {"client": {"kcr_code": "new"}},
        ],
        kind="REGISTRY",
    )
    result = rollback(engine, envelope)
    assert result["removed_clients"] == 1 and result["restored_clients"] == 1
    assert get_card(engine, "a").model_dump() == baseline
    with pytest.raises(ClientNotFound):
        get_card(engine, "new")
    assert get_card(engine, "b").organization_name == "unaffected"


def test_rollback_history_corrections_warnings_and_immutable_total(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    before_card = get_card(engine, "a").model_dump()
    before_history = get_history(engine, "a").model_dump()
    correction = apply([snapshot("2026_Q1", None)])
    assert get_card(engine, "a").needs_review
    result = rollback(engine, correction)
    assert result["removed_history_records"] == 1
    assert get_card(engine, "a").model_dump() == before_card
    assert get_history(engine, "a").model_dump() == before_history


def test_manual_quarter_deletion_then_rollback_restores_both_periods(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10), snapshot("2026_Q2", 20)])
    before = get_card(engine, "a").model_dump()
    history_before = get_history(engine, "a").model_dump()
    target = apply([snapshot("2026_Q1", 7), snapshot("2026_Q2", 15)], day="2026-04-20")
    removed = delete(engine, quarter="2026_Q1")
    assert removed["removed_snapshots"] == 1
    assert get_card(engine, "a").outstanding_documents == 15
    assert get_history(engine, "a", quarter="2026_Q1").total == 0
    assert get_card(engine, "a").tags == ["VIP"]
    restored = rollback(engine, target)
    assert restored["restored_history_records"] == 1
    assert get_card(engine, "a").model_dump() == before
    assert get_history(engine, "a").model_dump() == history_before


def test_manual_full_deletion_restores_only_clients_touched_by_batch(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    before = get_card(engine, "a").model_dump()
    history_before = get_history(engine, "a").model_dump()
    target = apply([snapshot("2026_Q1", 5)], day="2026-04-20")
    delete(engine)
    delete(engine, code="b")
    with pytest.raises(ClientNotFound):
        get_card(engine, "a")
    rollback(engine, target)
    assert get_card(engine, "a").model_dump() == before
    assert get_history(engine, "a").model_dump() == history_before
    with pytest.raises(ClientNotFound):
        get_card(engine, "b")


def test_removing_blocking_preserves_legacy_replay_and_rollback(database):
    engine, config, _ = database
    migration.upgrade(config, "0006_registry_provenance")
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO clients(kcr_code, organization_name, is_blocked, inherited_fields,
                                    field_sources, needs_review)
                VALUES ('a', 'before', true, '{"is_blocked": {}}', '{"is_blocked": {}}', true),
                       ('b', 'unaffected', true, '{"is_blocked": {}, "manager_name": {}}',
                        '{"is_blocked": {}, "manager_name": {}}', true)
            """)
        )
    original = ImportEnvelope(
        batch_id=uuid4(),
        uploaded_by=uuid4(),
        import_type="SNAPSHOT",
        selected_quarter="2026_Q2",
        snapshot_date="2026-04-10",
        items=[snapshot("2026_Q1", 10)],
    )
    assert apply_import(engine, original)["accepted_rows"] == 1
    with engine.begin() as connection:
        connection.execute(
            text("""UPDATE quarter_snapshot_history SET inherited_fields = '{"is_blocked": {}}'""")
        )
    legacy = ImportEnvelope(
        batch_id=uuid4(),
        uploaded_by=uuid4(),
        import_type="REGISTRY",
        items=[{"client": {"kcr_code": "a", "organization_name": "after", "is_blocked": True}}],
    )
    applied = apply_import(engine, legacy)
    assert applied["accepted_rows"] == 1
    delete(engine)

    migration.upgrade(config, "head")
    assert apply_import(engine, legacy) == applied
    assert get_card(engine, "b").model_dump()["inherited_fields"] == {"manager_name": {}}
    with engine.connect() as connection:
        undo = connection.scalar(
            text("SELECT previous_state FROM clients_import_undo WHERE batch_id = :batch"),
            {"batch": legacy.batch_id},
        )["clients"][0]
        assert "is_blocked" not in undo
        assert undo["field_sources"] == undo["inherited_fields"] == {}
        assert undo["needs_review"] is False
        archived = connection.scalar(text("SELECT payload FROM clients_deleted_history"))
        assert archived["inherited_fields"] == {}

    assert rollback(engine, legacy)["restored_history_records"] == 1
    restored = get_card(engine, "a")
    assert restored.organization_name == "before"
    assert restored.outstanding_documents == 10
    assert not restored.needs_review
    assert "is_blocked" not in restored.model_dump()


def test_deletion_before_new_import_is_not_undone_by_its_rollback(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    delete(engine, quarter="2026_Q1")
    later = apply([snapshot("2026_Q1", 4)], day="2026-04-20")
    rollback(engine, later)
    assert get_card(engine, "a").snapshots == []
    assert get_history(engine, "a").total == 0


def test_repeated_delete_does_not_delete_reimported_data(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    cmd = command()
    original = delete(engine, quarter="2026_Q1", cmd=cmd)
    apply([snapshot("2026_Q1", 4)], day="2026-04-20")
    assert delete(engine, quarter="2026_Q1", cmd=cmd) == original
    assert get_card(engine, "a").outstanding_documents == 4
    with pytest.raises(RecoveryConflict) as error:
        delete(engine, cmd=cmd)
    assert error.value.code == "OPERATION_PAYLOAD_CONFLICT"


def test_latest_guard_replay_and_no_reapply_rolled_back_batch(setup):
    engine, _, apply, registry = setup
    target = apply([snapshot("2026_Q1", 10)])
    with pytest.raises(RecoveryConflict) as error:
        rollback(engine, registry)
    assert error.value.code == "IMPORT_NOT_LATEST"
    cmd = command()
    result = rollback(engine, target, cmd)
    later = apply([snapshot("2026_Q2", 3)], day="2026-04-20")
    assert rollback(engine, target, cmd) == result
    assert rollback(engine, target)["already_rolled_back"]
    assert get_card(engine, "a").outstanding_documents == 3
    with pytest.raises(ImportAlreadyRolledBack):
        apply_import(engine, target)
    rollback(engine, later)
    assert get_card(engine, "a").outstanding_documents == 0


def test_newer_apply_for_other_clients_does_not_block_rollback(setup):
    engine, _, apply, _ = setup
    target = apply([snapshot("2026_Q1", 10)])
    apply(
        [
            {
                "client": {"kcr_code": "b"},
                "snapshot": {
                    "quarter": "2026_Q2",
                    "total_documents": 100,
                    "unsubmitted_documents": 4,
                },
            }
        ],
        day="2026-04-20",
    )
    apply([{"client": {"kcr_code": "c", "organization_name": "later"}}], kind="REGISTRY")
    assert rollback(engine, target)["removed_history_records"] == 1
    assert get_card(engine, "a").outstanding_documents == 0
    assert get_history(engine, "b").total == 1
    assert get_card(engine, "c").organization_name == "later"


def test_later_apply_sharing_a_client_still_blocks_rollback(setup):
    engine, _, apply, _ = setup
    target = apply([snapshot("2026_Q1", 10)])
    later = apply([snapshot("2026_Q1", 3)], day="2026-04-20")
    apply([{"client": {"kcr_code": "c", "organization_name": "unrelated"}}], kind="REGISTRY")
    with pytest.raises(RecoveryConflict, match="IMPORT_NOT_LATEST"):
        rollback(engine, target)
    rollback(engine, later)
    assert get_card(engine, "a").outstanding_documents == 10


def test_only_batches_with_accepted_rows_block_rollback(setup):
    engine, _, apply, _ = setup
    first = apply([snapshot("2026_Q1", 10)])
    rejected_row = {**snapshot("2026_Q1", 5), "client": {"kcr_code": "unknown"}}
    rejected = first.model_copy(update={"batch_id": uuid4(), "items": [rejected_row]})
    report = apply_import(engine, rejected)
    assert (report["accepted_rows"], report["rejected_rows"]) == (0, 1)

    partial = first.model_copy(
        update={"batch_id": uuid4(), "items": [snapshot("2026_Q1", 5), rejected_row]}
    )
    report_partial = apply_import(engine, partial)
    assert (report_partial["accepted_rows"], report_partial["rejected_rows"]) == (1, 1)
    with pytest.raises(RecoveryConflict, match="IMPORT_NOT_LATEST"):
        rollback(engine, first)

    rollback(engine, partial)
    with pytest.raises(RecoveryConflict, match="IMPORT_NOT_LATEST"):
        rollback(engine, rejected)
    assert rollback(engine, first)["removed_history_records"] == 1
    assert get_history(engine, "a").total == 0
    assert apply_import(engine, rejected) == report


def test_retention_expires_only_confirmed_rollback_copies(setup):
    engine, url, apply, registry = setup
    active = apply([snapshot("2026_Q2", 7)])
    delete(engine, quarter="2026_Q2")

    def roll_back_archived_snapshot():
        batch = apply([snapshot("2026_Q1", 10)])
        delete(engine, quarter="2026_Q1")
        return batch, rollback(engine, batch)

    expired, expired_result = roll_back_archived_snapshot()
    retained, _ = roll_back_archived_snapshot()
    unconfirmed, _ = roll_back_archived_snapshot()
    settings = Settings(database_url=url)
    with TestClient(create_app(settings)) as client:

        def confirmation(batch_id):
            return client.put(f"/internal/v1/import/{batch_id}/rollback-confirmation")

        missing = confirmation(uuid4())
        assert missing.status_code == 404 and missing.json()["code"] == "IMPORT_NOT_FOUND"
        pending = confirmation(active.batch_id)
        assert (
            pending.status_code == 409 and pending.json()["code"] == "IMPORT_ROLLBACK_NOT_APPLIED"
        )
        first = confirmation(expired.batch_id)
        assert first.status_code == 200 and first.json()["rollback_confirmed_at"]
        assert confirmation(expired.batch_id).json() == first.json()
        assert confirmation(retained.batch_id).status_code == 200

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE clients_import_operations
                SET rollback_confirmed_at = clock_timestamp() - INTERVAL '2160 hours'
                WHERE batch_id = :batch_id
            """),
            {"batch_id": expired.batch_id},
        )
        connection.execute(
            text("""
                UPDATE clients_import_operations
                SET rollback_confirmed_at = clock_timestamp() - INTERVAL '89 days'
                WHERE batch_id = :batch_id
            """),
            {"batch_id": retained.batch_id},
        )
        connection.execute(
            text("UPDATE clients_import_operations SET created_at = now() - INTERVAL '180 days'")
        )
    assert not purge_expired_copies(engine)
    assert not purge_expired_copies(engine)
    with engine.connect() as connection:
        surviving = {str(batch.batch_id) for batch in (active, retained, unconfirmed)}
        undo_ids = connection.scalars(text("SELECT batch_id::text FROM clients_import_undo"))
        assert set(undo_ids) == surviving | {str(registry.batch_id)}
        archive_ids = connection.scalars(
            text("SELECT payload->>'import_batch_id' FROM clients_deleted_history")
        )
        assert set(archive_ids) == surviving
    replay = rollback(engine, expired)
    assert replay["already_rolled_back"]
    assert replay["removed_history_records"] == expired_result["removed_history_records"]


def test_sequence_of_rollbacks_does_not_resurrect_cancelled_history(setup):
    engine, _, apply, _ = setup
    first = apply([snapshot("2026_Q1", 10)])
    second = apply([snapshot("2026_Q1", 5)], day="2026-04-20")
    delete(engine)
    rollback(engine, second)
    assert get_history(engine, "a").total == 1
    rollback(engine, first)
    assert get_history(engine, "a").total == 0
    assert get_card(engine, "a").outstanding_documents == 0


def test_absent_delete_and_unknown_rollback(setup):
    engine, _, _, _ = setup
    result = delete(engine, code="missing")
    assert result["removed_clients"] == result["removed_snapshots"] == 0
    result = delete(engine, quarter="2026_Q4")
    assert result["removed_snapshots"] == result["removed_history_records"] == 0
    with pytest.raises(ImportNotFound):
        execute_recovery(engine, command(), action="ROLLBACK", batch_id=uuid4())


def test_delete_after_completed_rollback_is_not_reverted_by_replay(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    target = apply([snapshot("2026_Q1", 5)], day="2026-04-20")
    rollback(engine, target)
    delete(engine, quarter="2026_Q1")
    assert rollback(engine, target)["already_rolled_back"]
    assert get_history(engine, "a").total == 0


def test_single_legacy_package_can_be_rolled_back(database):
    engine, config, _ = database
    migration.upgrade(config, "0002_import_apply")
    batch_id = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO clients_import_operations(batch_id,payload_hash,result) "
                "VALUES (:id,'legacy','{}')"
            ),
            {"id": batch_id},
        )
    migration.upgrade(config, "head")
    result = execute_recovery(engine, command(), action="ROLLBACK", batch_id=batch_id)
    assert not result["already_rolled_back"] and result["restored_clients"] == 0


def test_failed_restore_is_atomic_then_retry_works(setup):
    engine, _, apply, _ = setup
    apply([snapshot("2026_Q1", 10)])
    target = apply([snapshot("2026_Q1", 5)], day="2026-04-20")
    delete(engine)
    cmd = command()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE clients_import_operations"):
            raise RuntimeError("simulated interruption")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            rollback(engine, target, cmd)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with pytest.raises(ClientNotFound):
        get_card(engine, "a")
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM clients_data_operations WHERE operation_id=:id"),
                {"id": cmd.operation_id},
            )
            == 0
        )
    rollback(engine, target, cmd)
    assert get_card(engine, "a").outstanding_documents == 10


def test_concurrent_rollback_repeat_is_applied_once(setup):
    engine, _, apply, _ = setup
    target = apply([snapshot("2026_Q1", 10)])
    cmd = command()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rollback(engine, target, cmd), range(2)))
    assert results[0] == results[1]
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM clients_data_operations")) == 1


def test_recovery_http_available_by_default_and_safe_validation(setup):
    _, url, apply, _ = setup
    target = apply([snapshot("2026_Q1", 10)])
    path = f"/internal/v1/import/{target.batch_id}/rollback"
    with TestClient(create_app(Settings(database_url=url))) as client:
        assert client.post(path, json={"private": "secret"}).status_code == 400
        response = client.request(
            "DELETE",
            "/internal/v1/clients/a/snapshots/2026_Q1",
            json=command().model_dump(mode="json"),
        )
        assert response.status_code == 200 and response.json()["local_only"]
        assert client.post(path, json=command().model_dump(mode="json")).status_code == 200
        response = client.request(
            "DELETE", "/internal/v1/clients/missing", json=command().model_dump(mode="json")
        )
        assert response.status_code == 200


def test_legacy_order_is_not_guessed(database):
    engine, config, _ = database
    migration.upgrade(config, "0002_import_apply")
    ids = [uuid4(), uuid4()]
    with engine.begin() as conn:
        for batch_id in ids:
            conn.execute(
                text(
                    "INSERT INTO clients_import_operations(batch_id,payload_hash,result) "
                    "VALUES (:id,'legacy','{}')"
                ),
                {"id": batch_id},
            )
    migration.upgrade(config, "head")
    for batch_id in ids:
        with pytest.raises(RecoveryConflict) as error:
            execute_recovery(engine, command(), action="ROLLBACK", batch_id=batch_id)
        assert error.value.code == "IMPORT_ORDER_UNKNOWN"
