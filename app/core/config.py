from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    # Public base URL used to build links in emails (activation, password reset).
    app_base_url: str = "http://localhost:8000"

    # Database
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_user: str = "cinema"
    postgres_password: str = "changeme"
    postgres_db: str = "cinema"

    # Redis / Celery
    redis_host: str = "redis"
    redis_port: int = 6379
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # JWT / tokens
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    activation_token_ttl_hours: int = 24
    password_reset_token_ttl_hours: int = 1

    # Mail
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_from: str = "no-reply@cinema.local"

    # Stripe (test mode only)
    stripe_secret_key: str = "sk_test_placeholder"
    stripe_webhook_secret: str = "whsec_placeholder"
    stripe_success_url: str = "http://localhost:8000/api/v1/payments/success"
    stripe_cancel_url: str = "http://localhost:8000/api/v1/payments/cancel"

    @property
    def uses_default_jwt_secret(self) -> bool:
        return self.jwt_secret_key in {"change-me", ""} or len(self.jwt_secret_key) < 32

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
