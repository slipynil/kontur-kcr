import re

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


def counter(value: object, label: str) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 2_147_483_647
        or value % 1
    ):
        raise InvalidRow(f"{label}: требуется целое неотрицательное число")
    return int(value)
