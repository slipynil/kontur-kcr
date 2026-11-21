import re
from decimal import Decimal, InvalidOperation

QUARTER = re.compile(r"[0-9]{4}_Q[1-4]")
REGISTRY_STATUS_TAGS = {
    "vip": "VIP",
    "новичок": "NOVICE",
    "интегратор": "INTEGRATOR",
    "техпартнер": "TECH_PARTNER",
    "мпп": "MPP",
    "не работать": "DO_NOT_WORK",
}


class InvalidRow(ValueError):
    pass


def normalized_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def counter(value: str | None, label: str) -> int | None:
    if value is None:
        return None
    try:
        number = Decimal(value.replace(" ", "").replace("\xa0", "").replace(",", "."))
    except InvalidOperation as exc:
        raise InvalidRow(f"{label}: требуется целое неотрицательное число") from exc
    if not number.is_finite() or not 0 <= number <= 2_147_483_647 or number % 1:
        raise InvalidRow(f"{label}: требуется целое неотрицательное число")
    return int(number)
