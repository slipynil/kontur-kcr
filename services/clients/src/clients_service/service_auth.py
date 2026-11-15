from collections.abc import Callable
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from clients_service.config import Settings

bearer = HTTPBearer(auto_error=False, scheme_name="ServiceBearer")


class ServiceAccessDenied(Exception):
    def __init__(self, *, authenticated: bool = False):
        self.authenticated = authenticated


def require_service_access(settings: Settings, *, write: bool = False) -> Callable[..., None]:
    tokens = tuple(
        (token.get_secret_value().encode(), can_write)
        for token, can_write in (
            (settings.gateway_service_token, False),
            (settings.operator_work_service_token, False),
            (settings.import_service_token, True),
        )
        if token is not None
    )

    def check(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or len(request.headers.getlist("authorization")) != 1:
            raise ServiceAccessDenied()
        supplied = credentials.credentials.encode()
        for token, can_write in tokens:
            if compare_digest(supplied, token):
                if write and not can_write:
                    raise ServiceAccessDenied(authenticated=True)
                return
        raise ServiceAccessDenied()

    return check
