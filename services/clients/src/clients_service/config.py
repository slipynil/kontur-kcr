import re

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIENTS_", env_ignore_empty=True, extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr
    enable_import_apply: bool = False
    enable_registry_read: bool = False
    enable_data_recovery: bool = False
    allow_unauthenticated_local: bool = False
    gateway_service_token: SecretStr | None = None
    operator_work_service_token: SecretStr | None = None
    import_service_token: SecretStr | None = None
    database_connect_timeout: int = Field(default=3, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=3000, ge=100, le=30000)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("Expected a PostgreSQL psycopg URL") from None
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("Expected postgresql+psycopg URL with a database name")
        return value

    @field_validator("gateway_service_token", "operator_work_service_token", "import_service_token")
    @classmethod
    def validate_service_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9._~+/-]+=*", value.get_secret_value()
        ):
            raise ValueError("Expected a non-empty Bearer token")
        return value

    @model_validator(mode="after")
    def validate_service_access(self):
        tokens = [
            token.get_secret_value()
            for token in (
                self.gateway_service_token,
                self.operator_work_service_token,
                self.import_service_token,
            )
            if token is not None
        ]
        if len(tokens) != len(set(tokens)):
            raise ValueError("Use a distinct token for each calling service")
        if self.allow_unauthenticated_local:
            if tokens:
                raise ValueError(
                    "Local unauthenticated mode cannot be combined with service tokens"
                )
            return self
        if self.enable_registry_read and not tokens:
            raise ValueError("Configure a service token before enabling registry reads")
        if (
            self.enable_import_apply or self.enable_data_recovery
        ) and self.import_service_token is None:
            raise ValueError("Configure CLIENTS_IMPORT_SERVICE_TOKEN before enabling writes")
        return self
