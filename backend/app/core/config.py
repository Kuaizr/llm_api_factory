from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "llm_api_factory"
    app_timezone: str = "Asia/Shanghai"
    database_url: str = (
        f"sqlite+aiosqlite:///{(Path(__file__).resolve().parents[2] / 'llm_api_factory.db').as_posix()}"
    )
    redis_url: str = "redis://localhost:6379/0"
    sqlite_busy_timeout_ms: int = 5000
    sqlite_journal_mode: str = "WAL"
    pg_pool_size: int = 10
    pg_max_overflow: int = 5
    master_auth_token: str | None = None
    admin_session_ttl_seconds: int = 86400
    admin_legacy_master_bearer_enabled: bool = False
    data_encryption_key: str | None = None
    agent_auth_token: str | None = None
    agent_allowed_targets: str = "*"
    agent_heartbeat_timeout_seconds: int = 120
    agent_request_timeout_seconds: float = 60.0
    agent_stream_idle_timeout_seconds: float = 300.0
    agent_ws_url: str | None = None
    agent_heartbeat_url: str | None = None
    agent_name: str | None = None
    agent_region: str | None = None
    agent_network_group: str | None = None
    agent_labels: str | None = None
    agent_endpoint_url: str | None = None
    agent_heartbeat_interval_seconds: int = 20
    agent_reconnect_delay_seconds: int = 5
    agent_public_base_url: str | None = None
    agent_install_script_url: str | None = None
    agent_install_repo_url: str | None = None
    agent_install_repo_ref: str | None = None
    cors_allow_origins: str = "http://localhost:5173"
    http_timeout_seconds: float = 60.0
    circuit_breaker_failures: int = 3
    circuit_breaker_ttl_seconds: int = 3600
    memory_redis_max_keys: int = 4096
    health_probe_enabled: bool = True
    health_probe_interval_seconds: int = 60
    health_probe_timeout_seconds: float = 10.0
    health_probe_prompt: str = "ping"
    health_probe_max_tokens: int = 1
    health_probe_latency_threshold_ms: int = 2000
    health_probe_result_ttl_seconds: int = 86400
    health_probe_series_ttl_seconds: int = 86400
    health_probe_series_max_entries: int = 500
    proxy_dump_root: str = str(Path(__file__).resolve().parents[2] / "proxy_dumps")
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    codex_oauth_token_url: str = "https://auth.openai.com/oauth/token"
    codex_oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    codex_oauth_refresh_leeway_seconds: int = 300

    model_config = SettingsConfigDict(env_prefix="LLM_", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
