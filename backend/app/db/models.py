from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Endpoint(Base):
    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    auth_header_name: Mapped[str] = mapped_column(String(64), default="Authorization")
    auth_header_prefix: Mapped[str] = mapped_column(String(32), default="Bearer")
    provider: Mapped[str] = mapped_column(String(32), default="openai")
    strategy: Mapped[str] = mapped_column(String(32), default="weighted_round_robin")
    agent_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    api_keys: Mapped[list["APIKey"]] = relationship(back_populates="endpoint")
    model_maps: Mapped[list["ModelMap"]] = relationship(back_populates="endpoint")


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id"))
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key: Mapped[str] = mapped_column(Text)
    rule_group: Mapped[str] = mapped_column(String(64), default="default", index=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_today: Mapped[int] = mapped_column(Integer, default=0)
    used_today_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_usage: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    endpoint: Mapped[Endpoint] = relationship(back_populates="api_keys")


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pattern: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(64), default="default", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_key_ids_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModelMap(Base):
    __tablename__ = "model_maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id"))
    model_alias: Mapped[str] = mapped_column(String(128), index=True)
    real_model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    endpoint: Mapped[Endpoint] = relationship(back_populates="model_maps")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supports_gpt: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_gemini: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_claude: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    model_alias: Mapped[str] = mapped_column(String(128), index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id"))
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"))
    rule_group: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
