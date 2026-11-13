"""Resolve the live paper_extract route and gate OpenAI file_search billing.

The Codex CLI path does not require this confirmation. OpenAI-compatible
providers are not assumed to implement file_search.
"""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from config import load_config, task_model_id, task_model_name
from model_gateway import get_model_entry, get_provider_entry, get_task_binding
from model_gateway.catalog import openai_model_uses_responses_api


FILE_SEARCH_PRICING_URL = "https://platform.openai.com/docs/pricing"
FILE_SEARCH_GUIDE_URL = "https://platform.openai.com/docs/guides/tools-file-search"

CONFIRMATION_REQUIRED_CODE = "file_search_confirmation_required"
ROUTE_CHANGED_CODE = "file_search_route_changed"
UNSUPPORTED_ROUTE_CODE = "paper_extract_route_unsupported"

_active_confirmation: ContextVar[dict[str, Any] | None] = ContextVar(
    "paper_extract_file_search_confirmation",
    default=None,
)


class FileSearchConfirmation(BaseModel):
    acknowledged: bool = False
    route_fingerprint: str = ""


class FileSearchConfirmationBody(BaseModel):
    file_search_confirmation: FileSearchConfirmation | None = None


class PipelineRunRequest(BaseModel):
    use_llm: bool = True
    file_search_confirmation: FileSearchConfirmation | None = None


class JobResumeRequest(BaseModel):
    file_search_confirmation: FileSearchConfirmation | None = None


class PaperExtractRouteError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        route: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.route = route or {}

    def http_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "route": public_route_view(self.route) if self.route else None,
        }


def confirmation_as_dict(
    confirmation: FileSearchConfirmation | Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if confirmation is None:
        return None
    if isinstance(confirmation, FileSearchConfirmation):
        return confirmation.model_dump()
    if isinstance(confirmation, Mapping):
        return {
            "acknowledged": bool(confirmation.get("acknowledged")),
            "route_fingerprint": str(confirmation.get("route_fingerprint") or "").strip(),
        }
    return None


def _normalized_base_url(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    # Drop userinfo and query/fragment so credentials never enter the fingerprint.
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host.lower(), path, "", ""))


def _file_search_api(upstream_model: str) -> str:
    return "responses" if openai_model_uses_responses_api(upstream_model) else "assistants"


def public_route_view(route: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "provider_id",
        "provider_type",
        "provider_label",
        "model_id",
        "model_label",
        "upstream_model",
        "runtime_model_name",
        "surface",
        "file_search_api",
        "requires_file_search_confirmation",
        "supported",
        "unsupported_reason",
        "route_fingerprint",
        "has_custom_base_url",
        "pricing_url",
        "file_search_guide_url",
    )
    return {key: route.get(key) for key in keys}


def resolve_paper_extract_route(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    binding = get_task_binding(cfg, "paper_extract")
    model_id = task_model_id(cfg, "paper_extract") or str(binding.get("model_id") or "")
    runtime_name = task_model_name(cfg, "paper_extract")
    model_entry = get_model_entry(cfg, model_id) or get_model_entry(cfg, runtime_name)
    provider = None
    if model_entry:
        provider = get_provider_entry(cfg, str(model_entry.get("provider_id") or ""))

    provider_id = str((provider or {}).get("id") or (model_entry or {}).get("provider_id") or "")
    provider_type = str((provider or {}).get("provider_type") or "")
    provider_label = str((provider or {}).get("label") or provider_id or "")
    model_label = str((model_entry or {}).get("label") or model_id or runtime_name)
    upstream_model = str((model_entry or {}).get("upstream_model") or runtime_name or model_id)
    base_url = _normalized_base_url((provider or {}).get("base_url"))
    default_openai_url = "https://api.openai.com/v1"
    has_custom_base_url = bool(base_url and base_url != default_openai_url)

    if not model_entry or not provider:
        unsupported_reason = (
            "当前 paper_extract 绑定无法解析到有效的模型或提供方。"
            "请在设置的模型网关中重新选择论文抽取模型。"
        )
        surface = "unresolved"
        supported = False
        requires_confirmation = False
        file_search_api = None
    elif provider_type == "codex_cli":
        unsupported_reason = None
        surface = "codex_cli_hierarchy"
        supported = True
        requires_confirmation = False
        file_search_api = None
    elif provider_type == "openai":
        unsupported_reason = None
        surface = "openai_file_search"
        supported = True
        requires_confirmation = True
        file_search_api = _file_search_api(upstream_model)
    elif provider_type == "openai_compatible":
        unsupported_reason = (
            "当前论文抽取绑定的是 OpenAI 兼容 API。"
            "Knowra 的结构化抽取不会假定兼容提供方实现了 OpenAI file_search、"
            "Assistants 或 Responses 检索。请在设置中改绑 OpenAI 原生模型，或使用本机 Codex CLI。"
        )
        surface = "unsupported_compatible_api"
        supported = False
        requires_confirmation = False
        file_search_api = None
    else:
        unsupported_reason = (
            f"当前论文抽取提供方类型 {provider_type or '未知'} 不受支持。"
            "请在设置中改绑 OpenAI 原生模型，或使用本机 Codex CLI。"
        )
        surface = "unsupported"
        supported = False
        requires_confirmation = False
        file_search_api = None

    fingerprint_payload = {
        "provider_id": provider_id,
        "provider_type": provider_type,
        "model_id": model_id,
        "upstream_model": upstream_model,
        "base_url": base_url,
        "surface": surface,
    }
    route_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "provider_id": provider_id,
        "provider_type": provider_type,
        "provider_label": provider_label,
        "model_id": model_id,
        "model_label": model_label,
        "upstream_model": upstream_model,
        "runtime_model_name": runtime_name,
        "surface": surface,
        "file_search_api": file_search_api,
        "requires_file_search_confirmation": requires_confirmation,
        "supported": supported,
        "unsupported_reason": unsupported_reason,
        "route_fingerprint": route_fingerprint,
        "has_custom_base_url": has_custom_base_url,
        "pricing_url": FILE_SEARCH_PRICING_URL,
        "file_search_guide_url": FILE_SEARCH_GUIDE_URL,
        "reasoning_effort": binding.get("reasoning_effort") or "medium",
    }


def require_paper_extract_route(
    cfg: dict[str, Any] | None = None,
    confirmation: FileSearchConfirmation | Mapping[str, Any] | None = None,
    *,
    stage: str = "submit",
) -> dict[str, Any]:
    """Validate the live route and, when needed, a fingerprint-bound confirmation.

    ``stage`` is ``submit`` (HTTP enqueue) or ``execute`` (worker, before upload).
    A confirmation is bound to the current provider/model/base URL fingerprint, so
    an older acknowledgement cannot authorize a later, different paid route.
    """
    route = resolve_paper_extract_route(cfg)
    if not route["supported"]:
        raise PaperExtractRouteError(
            UNSUPPORTED_ROUTE_CODE,
            route["unsupported_reason"] or "当前论文抽取路径不受支持。",
            status_code=400,
            route=route,
        )
    if not route["requires_file_search_confirmation"]:
        return route

    payload = confirmation_as_dict(confirmation) or {}
    acknowledged = bool(payload.get("acknowledged"))
    fingerprint = str(payload.get("route_fingerprint") or "").strip()
    if not acknowledged or not fingerprint:
        action = "创建抽取任务或恢复任务" if stage == "submit" else "上传 PDF 或调用模型"
        raise PaperExtractRouteError(
            CONFIRMATION_REQUIRED_CODE,
            (
                "当前论文抽取将通过 OpenAI API 的 file_search 按量付费进行，"
                f"该费用不包含在 ChatGPT 或 Codex 订阅额度中。请先确认后再{action}。"
                f"定价见 {FILE_SEARCH_PRICING_URL}。"
            ),
            status_code=409,
            route=route,
        )
    if fingerprint != route["route_fingerprint"]:
        raise PaperExtractRouteError(
            ROUTE_CHANGED_CODE,
            "论文抽取的提供方或模型已变化，先前的 file_search 付费确认已失效。请按当前绑定重新确认后再继续。",
            status_code=409,
            route=route,
        )
    return route


def set_active_file_search_confirmation(
    confirmation: FileSearchConfirmation | Mapping[str, Any] | None,
):
    return _active_confirmation.set(confirmation_as_dict(confirmation))


def reset_active_file_search_confirmation(token) -> None:
    _active_confirmation.reset(token)


def get_active_file_search_confirmation() -> dict[str, Any] | None:
    return _active_confirmation.get()


def ensure_file_search_execution_allowed(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Worker-side guard: refuse paid file_search work without a matching confirmation."""
    return require_paper_extract_route(
        cfg,
        get_active_file_search_confirmation(),
        stage="execute",
    )
