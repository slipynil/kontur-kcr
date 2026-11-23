class HistoryError(Exception):
    """A failure reported to the caller with an HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def unavailable(message: str = "Хроника временно недоступна") -> HistoryError:
    return HistoryError(503, "HISTORY_UNAVAILABLE", message)
