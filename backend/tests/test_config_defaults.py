from app.core.config import Settings


def test_default_database_url_is_sqlite() -> None:
    settings = Settings()
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert settings.database_url.endswith("llm_api_factory.db")


def test_default_app_timezone_is_shanghai() -> None:
    settings = Settings()
    assert settings.app_timezone == "Asia/Shanghai"
