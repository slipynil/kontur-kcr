from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from clients_service.read_models import Dynamics


def comparison_quarters(quarter: str | None = None) -> tuple[str, str]:
    if quarter is None:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        quarter = f"{today.year}_Q{(today.month - 1) // 3 + 1}"
    year, number = int(quarter[:4]), int(quarter[-1])
    previous = f"{year}_Q{number - 1}" if number > 1 else f"{year - 1:04}_Q4"
    return quarter, previous


def calculate_dynamics(current_previous: Sequence[int | None]) -> Dynamics | None:
    """Compare latest balances of two different original quarters, current first."""
    if len(current_previous) < 2 or any(value is None for value in current_previous[:2]):
        return None
    latest, previous = current_previous[:2]
    if latest == 0:
        return Dynamics.ALL_SUBMITTED
    if latest < previous:
        return Dynamics.REDUCING
    if latest > previous:
        return Dynamics.GROWING
    return Dynamics.NO_CHANGE
