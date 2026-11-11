import re
from decimal import Decimal, InvalidOperation

QUARTER = re.compile(r"[0-9]{4}_Q[1-4]")
TAGS = {
    "vip": "VIP",
    "новичок": "NOVICE",
    "novice": "NOVICE",
    "интегратор": "INTEGRATOR",
    "integrator": "INTEGRATOR",
    "техпартнер": "TECH_PARTNER",
    "tech_partner": "TECH_PARTNER",
    "мпп": "MPP",
    "общение через мпп": "MPP",
    "mpp": "MPP",
    "не работать": "DO_NOT_WORK",
    "do_not_work": "DO_NOT_WORK",
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


def client_status(value: str | None) -> str | None:
    if value is None:
        return None
    key = normalized_text(value)
    if key in {"active", "действующий", "дейсвующий", "действует"}:
        return "ACTIVE"
    if key in {"inactive", "недействующий", "не действующий", "не действует"}:
        return "INACTIVE"
    raise InvalidRow("Неизвестное значение действительности клиента")


def blocked(value: str | None) -> bool | None:
    if value is None:
        return None
    key = normalized_text(value)
    if key in {"1", "true", "да", "заблокирован", "+"}:
        return True
    if key in {"0", "false", "нет", "не заблокирован", "-"}:
        return False
    raise InvalidRow("Неизвестное значение блокировки клиента")


def tags(value: str | None) -> list[str] | None:
    if value is None:
        return None
    found = {
        TAGS[key] for part in re.split(r"[,;/\n]+", value) if (key := normalized_text(part)) in TAGS
    }
    return sorted(found) or None
