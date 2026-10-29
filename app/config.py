import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NE Florida Real Estate & Foreclosure Finder"
    database_url: str = "sqlite:///./realestatedeals.db"
    app_pin: str = ""
    secret_key: str = secrets.token_hex(32)
    session_max_age_seconds: int = 60 * 60 * 24 * 7  # 7 days
    auto_seed: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_pin and self.app_pin.strip())

    @property
    def signing_key(self) -> bytes:
        # Derive a deterministic key from secret_key + app_pin so restarts keep sessions valid if secret_key is set
        base = f"{self.secret_key}:{self.app_pin}".encode("utf-8")
        return base

    @property
    def sqlalchemy_database_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://") and "+psycopg" not in url and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


settings = Settings()
