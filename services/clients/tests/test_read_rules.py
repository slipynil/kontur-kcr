import pytest

from clients_service.read_models import Dynamics
from clients_service.read_rules import calculate_dynamics, comparison_quarters


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], None),
        ([10], None),
        ([0], None),
        ([0, 10], Dynamics.ALL_SUBMITTED),
        ([0, 0], Dynamics.ALL_SUBMITTED),
        ([2, 10], Dynamics.REDUCING),
        ([12, 10], Dynamics.GROWING),
        ([10, 10], Dynamics.NO_CHANGE),
    ],
)
def test_dynamics(values, expected):
    assert calculate_dynamics(values) == expected


@pytest.mark.parametrize(
    ("quarter", "previous"),
    [
        ("2026_Q1", "2025_Q4"),
        ("2026_Q2", "2026_Q1"),
        ("2026_Q3", "2026_Q2"),
        ("2026_Q4", "2026_Q3"),
    ],
)
def test_previous_quarter(quarter, previous):
    assert comparison_quarters(quarter) == (quarter, previous)
