from app.core.config import Settings


def test_default_jwt_secret_is_detected() -> None:
    assert Settings(jwt_secret_key="change-me").uses_default_jwt_secret
    assert Settings(jwt_secret_key="short").uses_default_jwt_secret
    assert not Settings(jwt_secret_key="a-long-and-random-secret-value").uses_default_jwt_secret


def test_database_url_is_built_from_parts() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=1,
        postgres_db="d",
    )
    assert settings.database_url == "postgresql+asyncpg://u:p@h:1/d"
