from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/bot.db"


class DatabaseSettings(BaseSettings):
    """Только URL базы — для Alembic, которому не нужен BOT_TOKEN."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL


class Settings(BaseSettings):
    """Настройки бота. Читаются из переменных окружения и файла .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    database_url: str = DEFAULT_DATABASE_URL
    audd_token: SecretStr | None = None

    rate_limit_per_minute: int = Field(default=5, ge=1)
    daily_limit: int = Field(default=50, ge=1)
    max_file_mb: int = Field(default=20, ge=1, le=20)
    fragment_seconds: int = Field(default=20, ge=5, le=60)
    max_concurrent_recognitions: int = Field(default=10, ge=1)

    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    tmp_dir: Path = Path("tmp")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    @field_validator("audd_token", mode="before")
    @classmethod
    def _empty_token_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
