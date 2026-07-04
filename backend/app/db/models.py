from datetime import date, datetime
import json

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
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
    access_mode: Mapped[str] = mapped_column(String(32), default="direct")
    agent_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    probe_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 通用 Provider 扩展字段
    url_path_suffix: Mapped[str | None] = mapped_column(String(256), nullable=True)
    extra_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_cookies: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_query_params: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    api_keys: Mapped[list["APIKey"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    model_maps: Mapped[list["ModelMap"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key: Mapped[str] = mapped_column(Text)
    rule_group: Mapped[str] = mapped_column(String(64), default="default", index=True)
    rule_groups_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    @staticmethod
    def normalize_rule_groups(
        raw_groups: object = None,
        *,
        fallback: str | None = None,
    ) -> list[str]:
        values: list[object] = []
        if isinstance(raw_groups, (list, tuple, set)):
            values.extend(raw_groups)
        elif isinstance(raw_groups, str):
            values.extend(raw_groups.split(","))
        elif raw_groups is not None:
            values.append(raw_groups)

        if fallback:
            values.append(fallback)

        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = str(item or "").strip()
            if not value:
                continue
            lower = value.lower()
            canonical = "default" if lower == "default" else value
            token = canonical.lower()
            if token in seen:
                continue
            seen.add(token)
            normalized.append(canonical)

        if "default" not in seen:
            normalized.insert(0, "default")
        elif normalized and normalized[0].lower() != "default":
            normalized = ["default", *[item for item in normalized if item.lower() != "default"]]

        return normalized or ["default"]

    @property
    def rule_groups(self) -> list[str]:
        parsed: object = None
        if self.rule_groups_json:
            try:
                parsed = json.loads(self.rule_groups_json)
            except (TypeError, ValueError):
                parsed = None
        return APIKey.normalize_rule_groups(parsed, fallback=self.rule_group)

    @property
    def primary_rule_group(self) -> str:
        for group in self.rule_groups:
            if group.lower() != "default":
                return group
        return "default"

    def assign_rule_groups(self, groups: object) -> list[str]:
        normalized = APIKey.normalize_rule_groups(groups)
        self.rule_group = next(
            (group for group in normalized if group.lower() != "default"),
            "default",
        )
        self.rule_groups_json = json.dumps(normalized, ensure_ascii=False)
        return normalized

    def in_rule_group(self, group_name: str) -> bool:
        target = str(group_name or "").strip().lower() or "default"
        return any(group.lower() == target for group in self.rule_groups)


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_pattern: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(64), default="default", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    dump_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dump_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    target_key_ids_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class FactoryAccessKey(Base):
    """对外访问 Key，支持绑定多个规则组。"""
    __tablename__ = "factory_access_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    rule_groups_json: Mapped[str] = mapped_column(Text, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def rule_groups(self) -> list[str]:
        try:
            return json.loads(self.rule_groups_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @rule_groups.setter
    def rule_groups(self, value: list[str]) -> None:
        normalized = list(dict.fromkeys([str(g).strip() for g in value if str(g).strip()]))
        self.rule_groups_json = json.dumps(normalized, ensure_ascii=False)

    def in_rule_group(self, group_name: str) -> bool:
        target = str(group_name or "").strip().lower() or "default"
        return any(g.lower() == target for g in self.rule_groups)


class ModelMap(Base):
    __tablename__ = "model_maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    model_alias: Mapped[str] = mapped_column(String(128), index=True)
    real_model: Mapped[str] = mapped_column(String(128))
    probe_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    endpoint: Mapped[Endpoint] = relationship(back_populates="model_maps")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    network_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    labels_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supports_gpt: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_gemini: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_claude: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_draining: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def labels(self) -> list[str]:
        if not self.labels_json:
            return []
        try:
            parsed = json.loads(self.labels_json)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    @labels.setter
    def labels(self, value: object) -> None:
        if value is None:
            self.labels_json = None
            return
        raw_items = value if isinstance(value, (list, tuple, set)) else [value]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            label = str(item or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            normalized.append(label)
        self.labels_json = json.dumps(normalized, ensure_ascii=False)


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_model_alias_created_at", "model_alias", "created_at"),
        Index("ix_request_logs_endpoint_id_created_at", "endpoint_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    model_alias: Mapped[str] = mapped_column(String(128), index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    requested_rule_group: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    rule_group: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    upstream_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RequestAttemptLog(Base):
    __tablename__ = "request_attempt_logs"
    __table_args__ = (
        Index("ix_request_attempt_logs_model_alias_created_at", "model_alias", "created_at"),
        Index("ix_request_attempt_logs_endpoint_id_created_at", "endpoint_id", "created_at"),
        Index("ix_request_attempt_logs_api_key_id_created_at", "api_key_id", "created_at"),
        Index("ix_request_attempt_logs_outcome_created_at", "outcome", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    model_alias: Mapped[str] = mapped_column(String(128), index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    requested_rule_group: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    rule_group: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    attempt_order: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    upstream_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_resource_created_at", "resource_type", "created_at"),
        Index("ix_audit_logs_action_created_at", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), default="admin", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
