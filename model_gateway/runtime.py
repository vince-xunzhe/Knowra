from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import get_model_entry, get_provider_entry


RESPONSES_ONLY_MODELS = {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ModelGatewayError(RuntimeError):
    pass


class ProviderCapabilityError(ModelGatewayError):
    pass


_CODEX_REASONING_SUFFIX_RE = re.compile(r"^(?P<base>gpt-[0-9][^\s]*)-(high|medium|low)$")
VALID_REASONING_EFFORTS = {"low", "medium", "high"}


def _provider_api_key(cfg: dict[str, Any], provider: dict[str, Any], api_key_override: str | None = None) -> str:
    if api_key_override:
        return api_key_override
    api_key = str(provider.get("api_key") or "").strip()
    if api_key:
        return api_key
    if provider.get("id") == "openai":
        return str(cfg.get("openai_api_key") or "").strip()
    return ""


def _create_openai_client(cfg: dict[str, Any], provider: dict[str, Any], api_key_override: str | None = None) -> OpenAI:
    api_key = _provider_api_key(cfg, provider, api_key_override)
    if not api_key:
        raise ModelGatewayError(f"Provider '{provider.get('label') or provider.get('id')}' API key not configured")
    base_url = str(provider.get("base_url") or "").strip()
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _validate_codex_cli_model(upstream_model: str) -> None:
    raw = (upstream_model or "").strip()
    if not raw:
        return
    match = _CODEX_REASONING_SUFFIX_RE.match(raw)
    if not match:
        return
    base_model = match.group("base")
    raise ModelGatewayError(
        f"Codex CLI 当前不支持模型名 '{raw}'。请改用基础模型名（如 {base_model}），"
        "不要把 high / medium / low 直接写进 model 字段。"
    )


def _summarize_codex_cli_failure(text: str, upstream_model: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "Codex CLI request failed"

    json_message_matches = re.findall(r'"message":"([^"]+)"', cleaned)
    if json_message_matches:
        message = json_message_matches[-1].encode("utf-8").decode("unicode_escape")
        if "not supported when using Codex with a ChatGPT account" in message:
            suffix_match = _CODEX_REASONING_SUFFIX_RE.match((upstream_model or "").strip())
            if suffix_match:
                base_model = suffix_match.group("base")
                return (
                    f"Codex CLI 当前不支持模型名 '{upstream_model}'。如果你使用 ChatGPT 账号，"
                    f"请改成 {base_model} 这样的基础模型名；high / medium / low 不是可直接传给 codex exec 的模型名。"
                )
        return message

    lines = [
        line.strip()
        for line in cleaned.splitlines()
        if line.strip() and not line.startswith("OpenAI Codex v")
    ]
    return lines[-1][:500] if lines else cleaned[:500]


def _normalize_reasoning_effort(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in VALID_REASONING_EFFORTS:
        return raw
    return None


def _run_codex_cli(
    provider: dict[str, Any],
    upstream_model: str,
    prompt: str,
    *,
    reasoning_effort: str | None = None,
    image_paths: list[str] | None = None,
    timeout_s: int = 180,
) -> str:
    command = str(provider.get("command") or "codex").strip() or "codex"
    _validate_codex_cli_model(upstream_model)
    with tempfile.NamedTemporaryFile(prefix="codex-last-message-", suffix=".txt", delete=False) as handle:
        output_path = Path(handle.name)
    args = [
        command,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(output_path),
    ]
    normalized_effort = _normalize_reasoning_effort(reasoning_effort)
    if normalized_effort:
        args.extend(["-c", f'model_reasoning_effort="{normalized_effort}"'])
    if upstream_model:
        args.extend(["--model", upstream_model])
    for image_path in image_paths or []:
        path_text = str(image_path or "").strip()
        if path_text:
            args.extend(["--image", path_text])
    args.append("-")
    try:
        completed = subprocess.run(
            args,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ModelGatewayError(f"Codex CLI not found: {command}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ModelGatewayError("Codex CLI request timed out") from exc
    try:
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
            stdout = completed.stdout.decode("utf-8", errors="ignore").strip()
            raise ModelGatewayError(
                _summarize_codex_cli_failure("\n".join(part for part in [stderr, stdout] if part), upstream_model)
                or f"Codex CLI exited with code {completed.returncode}"
            )
        return output_path.read_text(encoding="utf-8").strip()
    finally:
        output_path.unlink(missing_ok=True)


def create_openai_client_for_model(
    cfg: dict[str, Any],
    model_id: str,
    *,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str, dict[str, Any], dict[str, Any]]:
    model_entry = get_model_entry(cfg, model_id)
    if model_entry is None:
        provider = {"id": "openai", "label": "OpenAI", "provider_type": "openai", "base_url": "https://api.openai.com/v1", "api_key": cfg.get("openai_api_key") or ""}
        client = _create_openai_client(cfg, provider, api_key_override)
        return client, model_id, provider, {
            "id": model_id,
            "label": model_id,
            "provider_id": "openai",
            "upstream_model": model_id,
            "model_kind": "chat",
            "supported_tasks": [],
            "supports_vision": False,
            "builtin": False,
        }

    provider = get_provider_entry(cfg, model_entry["provider_id"])
    if provider is None:
        raise ModelGatewayError(f"Provider '{model_entry['provider_id']}' not found for model '{model_id}'")
    if provider.get("provider_type") not in {"openai", "openai_compatible"}:
        raise ProviderCapabilityError(
            f"Provider '{provider.get('label') or provider.get('id')}' does not expose an OpenAI client surface"
        )
    client = _create_openai_client(cfg, provider, api_key_override)
    return client, model_entry["upstream_model"], provider, model_entry


def call_text_model(
    cfg: dict[str, Any],
    *,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int = 1800,
    temperature: float = 0.3,
    reasoning_effort: str | None = None,
    image_paths: list[str] | None = None,
    timeout_s: int = 180,
) -> str:
    model_entry = get_model_entry(cfg, model_id)
    if model_entry is None:
        client, upstream_model, _, _ = create_openai_client_for_model(cfg, model_id)
        if upstream_model in RESPONSES_ONLY_MODELS:
            kwargs: dict[str, Any] = {
                "model": upstream_model,
                "instructions": system,
                "input": user,
            }
            normalized_effort = _normalize_reasoning_effort(reasoning_effort)
            if normalized_effort:
                kwargs["reasoning"] = {"effort": normalized_effort}
            response = client.responses.create(**kwargs)
            return (getattr(response, "output_text", "") or "").strip()
        response = client.chat.completions.create(
            model=upstream_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    provider = get_provider_entry(cfg, model_entry["provider_id"])
    if provider is None:
        raise ModelGatewayError(f"Provider '{model_entry['provider_id']}' not found for model '{model_id}'")

    prompt = (
        "你是一个模型中转站的文本推理后端。请只输出最终答案，不要执行工具，也不要解释系统实现。\n\n"
        f"[SYSTEM]\n{system.strip()}\n\n[USER]\n{user.strip()}"
    )

    provider_type = provider.get("provider_type")
    upstream_model = model_entry["upstream_model"]
    if provider_type == "codex_cli":
        return _run_codex_cli(
            provider,
            upstream_model,
            prompt,
            reasoning_effort=reasoning_effort,
            image_paths=image_paths,
            timeout_s=timeout_s,
        )

    client = _create_openai_client(cfg, provider)
    if provider_type == "openai" and upstream_model in RESPONSES_ONLY_MODELS:
        kwargs = {
            "model": upstream_model,
            "instructions": system,
            "input": user,
        }
        normalized_effort = _normalize_reasoning_effort(reasoning_effort)
        if normalized_effort:
            kwargs["reasoning"] = {"effort": normalized_effort}
        response = client.responses.create(**kwargs)
        return (getattr(response, "output_text", "") or "").strip()

    response = client.chat.completions.create(
        model=upstream_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def embed_text(
    cfg: dict[str, Any],
    *,
    model_id: str,
    text: str,
    api_key_override: str | None = None,
) -> list[float]:
    client, upstream_model, _, model_entry = create_openai_client_for_model(
        cfg,
        model_id,
        api_key_override=api_key_override,
    )
    if model_entry.get("model_kind") != "embedding":
        raise ProviderCapabilityError(f"Model '{model_id}' is not an embedding model")
    response = client.embeddings.create(input=text, model=upstream_model)
    return response.data[0].embedding
