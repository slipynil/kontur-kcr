from datetime import UTC, datetime
from uuid import uuid4

import pytest
from conftest import NOW, batch, sample
from pydantic import TypeAdapter, ValidationError

from client_history.models import (
    HistoryCleanupRequest,
    HistoryEvent,
    HistoryEventBatch,
    fingerprint,
)

EVENTS = TypeAdapter(HistoryEvent)


@pytest.mark.parametrize(
    "event_type",
    [
        "CALL_RESULT",
        "HISTORICAL_COMMENT",
        "COMMENT",
        "OPERATOR_ASSIGN",
        "PUBLICATION",
        "STAGE_CHANGE",
        "EMAIL_SENT",
        "CLAIM_UPDATE",
    ],
)
def test_every_catalogue_type_is_accepted(event_type):
    event = EVENTS.validate_python(sample(event_type))
    assert event.event_type == event_type


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": "NOTE"},
        {"work_quarter": None},
        {"work_quarter": "2026_Q5"},
        {"payload": {"comment_text": "x", "extra": 1}},
        {"payload": {}},
        {"payload": {"comment_text": ""}},
        {"extra": 1},
        {"occurred_at": "2026-11-23T10:00:00"},
        {"document_quarters": ["2026_Q3", "2026_Q3"]},
        {"kcr_code": "with space"},
        {"kcr_code": ""},
    ],
)
def test_invalid_comment_record_is_rejected(overrides):
    with pytest.raises(ValidationError):
        EVENTS.validate_python(sample("COMMENT") | overrides)


def test_quarters_are_sorted_and_kept():
    event = EVENTS.validate_python(sample(document_quarters=["2026_Q3", "2026_Q1"]))
    assert event.document_quarters == ["2026_Q1", "2026_Q3"]


def test_historical_comment_may_have_no_cycle():
    event = EVENTS.validate_python(sample("HISTORICAL_COMMENT"))
    assert event.work_quarter is None


def test_empty_batch_is_rejected():
    with pytest.raises(ValidationError):
        HistoryEventBatch.model_validate({"source": "OPERATOR_WORK", "events": []})


def test_oversized_batch_is_rejected():
    with pytest.raises(ValidationError):
        HistoryEventBatch.model_validate({"source": "OPERATOR_WORK", "events": [sample()] * 1001})


def test_unknown_source_is_rejected():
    with pytest.raises(ValidationError):
        batch(sample(), source="CLIENTS")


def test_fingerprint_ignores_offset_and_added_name():
    left = EVENTS.validate_python(sample())
    right = EVENTS.validate_python(
        sample(
            id=left.id,
            occurred_at=left.occurred_at.astimezone(UTC).isoformat(),
            recorded_at=left.recorded_at.isoformat(),
        )
    )
    assert fingerprint(left) == fingerprint(right)
    named = EVENTS.validate_python(sample(id=left.id, actor_name="operator"))
    assert fingerprint(left) != fingerprint(named)


def test_fingerprint_follows_content():
    left = EVENTS.validate_python(sample())
    other = EVENTS.validate_python(sample(id=left.id, payload={"comment_text": "Другой текст"}))
    assert fingerprint(left) != fingerprint(other)


def test_cleanup_requires_targets_and_unique_ids():
    operation = str(uuid4())
    with pytest.raises(ValidationError):
        HistoryCleanupRequest.model_validate(
            {"operation_id": operation, "kcr_code": "1", "scope": "TARGETED"}
        )
    with pytest.raises(ValidationError):
        HistoryCleanupRequest.model_validate(
            {
                "operation_id": operation,
                "kcr_code": "1",
                "scope": "TARGETED",
                "event_ids": [operation, operation],
            }
        )


def test_cleanup_fingerprint_ignores_target_order():
    first, second = str(uuid4()), str(uuid4())
    operation = str(uuid4())

    def command(event_ids, cycle_ids):
        return HistoryCleanupRequest.model_validate(
            {
                "operation_id": operation,
                "kcr_code": "1",
                "scope": "TARGETED",
                "event_ids": event_ids,
                "cycle_ids": cycle_ids,
            }
        )

    assert command([first, second], []).fingerprint() == command([second, first], []).fingerprint()
    assert command([first], []).fingerprint() != command([first], [second]).fingerprint()


def test_datetimes_are_normalised_to_utc():
    event = EVENTS.validate_python(sample(occurred_at="2026-11-23T13:00:00+03:00"))
    assert event.occurred_at == NOW
    assert event.occurred_at.tzinfo is UTC


def test_naive_recorded_at_is_rejected():
    with pytest.raises(ValidationError):
        EVENTS.validate_python(sample(recorded_at=datetime(2026, 11, 23, 10).isoformat()))
