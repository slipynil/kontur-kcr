from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clients_service.import_models import ClientInput, SnapshotInput
from clients_service.import_rules import (
    RowError,
    Source,
    merge_profile_fields,
    resolve_snapshot,
)


def source():
    return Source(uuid4(), "2026_Q1", date(2026, 4, 20))


def test_missing_remainder_inherits_with_original_source():
    original = source().as_dict()
    result = resolve_snapshot(
        SnapshotInput(quarter="2026_Q1"),
        first_total=100,
        previous_unsubmitted=7,
        previous_source=source(),
        previous_inherited={"unsubmitted_documents": original},
    )
    assert result.total_documents == 100
    assert result.unsubmitted_documents == 7
    assert result.inherited == {"unsubmitted_documents": original}


def test_explicit_zero_clears_remainder_warning_and_total_never_changes():
    result = resolve_snapshot(
        SnapshotInput(quarter="2026_Q1", total_documents=200, unsubmitted_documents=0),
        first_total=100,
        previous_unsubmitted=7,
        previous_source=source(),
        previous_inherited={"unsubmitted_documents": source().as_dict()},
    )
    assert (result.total_documents, result.unsubmitted_documents, result.inherited) == (100, 0, {})


@pytest.mark.parametrize(
    ("incoming", "field"),
    [
        ({"unsubmitted_documents": 4}, "total_documents"),
        ({"total_documents": 10}, "unsubmitted_documents"),
    ],
)
def test_new_quarter_cannot_invent_counts(incoming, field):
    with pytest.raises(RowError) as error:
        resolve_snapshot(
            SnapshotInput(quarter="2026_Q2", **incoming),
            first_total=None,
            previous_unsubmitted=None,
            previous_source=None,
            previous_inherited={},
        )
    assert error.value.field == field


def test_profile_confirmation_clears_only_supplied_field():
    origin = source().as_dict()
    current = {"manager_name": "old", "contacts": {"phone": "123"}, "is_blocked": True}
    warnings = {"manager_name": origin, "contacts": origin}
    values, remaining = merge_profile_fields(
        current,
        {"manager_name": "new", "is_blocked": False},
        sources={key: origin for key in current},
        inherited=warnings,
    )
    assert values["is_blocked"] is False
    assert remaining == {"contacts": origin}
    assert warnings == {"manager_name": origin, "contacts": origin}


def test_blank_does_not_erase_profile_but_marks_source():
    origin = source().as_dict()
    values, warnings = merge_profile_fields(
        {"manager_name": "old"},
        {"manager_name": "  "},
        sources={"manager_name": origin},
        inherited={},
    )
    assert values == {"manager_name": "old"}
    assert warnings == {"manager_name": origin}


@pytest.mark.parametrize("value", [-1, 1.2, True, "12", 2_147_483_648])
def test_counters_require_normalized_nonnegative_integers(value):
    with pytest.raises(ValidationError):
        SnapshotInput(quarter="2026_Q1", unsubmitted_documents=value)


def test_client_code_is_a_string_and_inn_preserves_zeroes():
    client = ClientInput(kcr_code=" 00042 ", inn="00123")
    assert client.kcr_code == "00042"
    assert client.inn == "00123"
    with pytest.raises(ValidationError):
        ClientInput(kcr_code=42)
