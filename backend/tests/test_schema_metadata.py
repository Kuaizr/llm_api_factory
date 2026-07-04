from app.core.providers import normalize_provider_filters, normalize_provider_name
from app.db.models import APIKey, ModelMap, RequestAttemptLog, RequestLog


def _fk_ondelete(model: object, column_name: str) -> str | None:
    column = model.__table__.c[column_name]
    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    return foreign_keys[0].ondelete


def test_request_log_composite_indexes_are_declared() -> None:
    names = {index.name for index in RequestLog.__table__.indexes}

    assert "ix_request_logs_model_alias_created_at" in names
    assert "ix_request_logs_endpoint_id_created_at" in names


def test_request_attempt_log_composite_indexes_are_declared() -> None:
    names = {index.name for index in RequestAttemptLog.__table__.indexes}

    assert "ix_request_attempt_logs_model_alias_created_at" in names
    assert "ix_request_attempt_logs_endpoint_id_created_at" in names
    assert "ix_request_attempt_logs_api_key_id_created_at" in names
    assert "ix_request_attempt_logs_outcome_created_at" in names


def test_endpoint_child_foreign_keys_cascade_on_delete() -> None:
    assert _fk_ondelete(APIKey, "endpoint_id") == "CASCADE"
    assert _fk_ondelete(ModelMap, "endpoint_id") == "CASCADE"
    assert _fk_ondelete(RequestLog, "endpoint_id") == "CASCADE"
    assert _fk_ondelete(RequestLog, "api_key_id") == "CASCADE"
    assert _fk_ondelete(RequestAttemptLog, "endpoint_id") == "CASCADE"
    assert _fk_ondelete(RequestAttemptLog, "api_key_id") == "CASCADE"


def test_provider_name_normalization_is_shared() -> None:
    assert normalize_provider_name(None) == "openai"
    assert normalize_provider_name(" Anthropic ") == "anthropic"
    assert normalize_provider_filters((" OpenAI ", "gemini", "")) == {"openai", "gemini"}
