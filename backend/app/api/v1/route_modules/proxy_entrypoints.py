from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_modules.proxy_core import _proxy_openai_request
from app.api.v1.route_modules.proxy_gemini import (
    extract_gemini_model_alias,
    rewrite_gemini_model_path,
)
from app.api.v1.route_modules.proxy_models import (
    build_gemini_models_response,
    list_accessible_model_aliases,
    list_models,
    list_upstream_models_filtered,
)
from app.core.route_exposure import (
    EXPOSURE_FORMAT_CHAT,
    EXPOSURE_FORMAT_CLAUDE_CODE,
    EXPOSURE_FORMAT_GEMINI,
    EXPOSURE_FORMAT_MESSAGE,
    EXPOSURE_FORMAT_RESPONSE,
)
from app.db.session import get_session


def _is_claude_code_request(request: Request) -> bool:
    session_id = str(request.headers.get("x-claude-code-session-id") or "").strip()
    if session_id:
        return True
    x_app = str(request.headers.get("x-app") or "").strip().lower()
    if x_app == "cli":
        return True
    anthropic_beta = str(request.headers.get("anthropic-beta") or "").lower()
    if "claude-code" in anthropic_beta:
        return True
    user_agent = str(request.headers.get("user-agent") or "").lower()
    return "claude-code" in user_agent or "claude_cli" in user_agent


async def chat_completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        exposure_format=EXPOSURE_FORMAT_CHAT,
    )


async def completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        exposure_format=EXPOSURE_FORMAT_CHAT,
    )


async def embeddings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        exposure_format=EXPOSURE_FORMAT_CHAT,
    )


async def responses(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=False,
        strip_rule_group_from_payload=False,
        provider_filter=("openai", "codex", "custom"),
        provider_filter_fallback_to_any=False,
        exposure_format=EXPOSURE_FORMAT_RESPONSE,
        detect_codex_exposure=True,
    )


async def openai_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_path = path.strip("/")
    if request.method.upper() == "GET" and normalized_path == "models":
        try:
            payload = await list_upstream_models_filtered(
                request,
                session,
                provider="openai",
                path_prefix="/openai",
                provider_filter=("openai", "custom"),
                provider_filter_fallback_to_any=False,
            )
        except (AttributeError, AssertionError):
            payload = None
        if payload is None:
            try:
                payload = await list_models(
                    request,
                    session,
                    provider_filter=("openai", "custom"),
                    provider_filter_fallback_to_any=False,
                )
            except (AttributeError, AssertionError):
                payload = None
        if payload is not None:
            return JSONResponse(content=payload)

    is_responses_path = normalized_path in {"responses", "responses/compact"}
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=True,
        strip_rule_group_from_payload=False,
        path_prefix="/openai",
        provider_filter=("openai", "codex", "custom")
        if is_responses_path
        else ("openai", "custom"),
        provider_filter_fallback_to_any=False,
        exposure_format=EXPOSURE_FORMAT_RESPONSE
        if is_responses_path
        else EXPOSURE_FORMAT_CHAT,
        detect_codex_exposure=is_responses_path,
        allow_missing_model=True,
    )


async def anthropic_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_path = path.strip("/")
    if request.method.upper() == "GET" and normalized_path == "models":
        try:
            payload = await list_upstream_models_filtered(
                request,
                session,
                provider="anthropic",
                path_prefix="/anthropic",
                provider_filter=("anthropic", "custom"),
                provider_filter_fallback_to_any=False,
            )
        except (AttributeError, AssertionError):
            payload = None
        if payload is None:
            try:
                payload = await list_models(
                    request,
                    session,
                    provider_filter=("anthropic", "custom"),
                    provider_filter_fallback_to_any=False,
                )
            except (AttributeError, AssertionError):
                payload = None
        if payload is not None:
            return JSONResponse(content=payload)

    exposure_format = (
        EXPOSURE_FORMAT_CLAUDE_CODE
        if _is_claude_code_request(request)
        else EXPOSURE_FORMAT_MESSAGE
    )
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=True,
        strip_rule_group_from_payload=False,
        path_prefix="/anthropic",
        provider_filter=("anthropic", "custom"),
        provider_filter_fallback_to_any=False,
        exposure_format=exposure_format,
        allow_missing_model=True,
    )


async def gemini_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_path = path.strip("/")
    if request.method.upper() == "GET" and normalized_path == "models":
        try:
            payload = await list_upstream_models_filtered(
                request,
                session,
                provider="gemini",
                path_prefix="/gemini",
                provider_filter=("gemini", "custom"),
                provider_filter_fallback_to_any=False,
            )
        except (AttributeError, AssertionError):
            payload = None
        if payload is None:
            try:
                model_aliases = await list_accessible_model_aliases(
                    request,
                    session,
                    provider_filter=("gemini", "custom"),
                    provider_filter_fallback_to_any=False,
                )
            except (AttributeError, AssertionError):
                model_aliases = None
            if model_aliases is not None:
                payload = build_gemini_models_response(model_aliases)
        if payload is not None:
            return JSONResponse(content=payload)

    if request.method.upper() == "POST" and normalized_path == "interactions":
        return await _proxy_openai_request(
            request,
            session,
            rewrite_model=True,
            strip_rule_group_from_payload=False,
            path_prefix="/gemini",
            provider_filter=("gemini", "custom"),
            provider_filter_fallback_to_any=False,
            exposure_format=EXPOSURE_FORMAT_GEMINI,
            allow_missing_model=False,
            model_payload_keys=("model", "agent"),
        )

    model_alias = extract_gemini_model_alias(request.url.path)
    if model_alias is None:
        model_alias = request.headers.get("X-Model-Alias")

    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=False,
        strip_rule_group_from_payload=False,
        path_prefix="/gemini",
        provider_filter=("gemini", "custom"),
        provider_filter_fallback_to_any=False,
        exposure_format=EXPOSURE_FORMAT_GEMINI,
        allow_missing_model=False,
        model_alias_override=model_alias,
        target_path_rewriter=lambda raw_path, candidate: rewrite_gemini_model_path(
            raw_path,
            candidate.real_model,
        ),
    )


async def gemini_interactions(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=True,
        strip_rule_group_from_payload=False,
        path_prefix="/gemini",
        provider_filter=("gemini", "custom"),
        provider_filter_fallback_to_any=False,
        exposure_format=EXPOSURE_FORMAT_GEMINI,
        allow_missing_model=False,
        model_payload_keys=("model", "agent"),
        target_path_rewriter=lambda raw_path, candidate: "/v1beta/interactions",
    )
