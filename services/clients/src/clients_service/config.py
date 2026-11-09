from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIENTS_", extra="ignore")

    database_url: SecretStr
    # Development switch, not an authentication mechanism.
    enable_import_apply: bool = False
    enable_registry_read: bool = False
    enable_data_recovery: bool = False
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
