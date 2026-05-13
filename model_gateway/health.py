from __future__ import annotations

from datetime import datetime, timezone

from .config import get_provider_entry
from .runtime import ModelGatewayError, _create_openai_client, _run_codex_cli


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_provider_healthcheck(cfg: dict, provider_id: str) -> dict:
    provider = get_provider_entry(cfg, provider_id)
    if provider is None:
        raise ModelGatewayError(f"Provider '{provider_id}' not found")

    tested_at = _now_iso()
    status = "ok"
    message = "Provider is healthy"

    try:
        provider_type = provider.get("provider_type")
        healthcheck_model = str(provider.get("healthcheck_model") or "").strip()
        if provider_type == "codex_cli":
            raw = _run_codex_cli(
                provider,
                healthcheck_model,
                "你正在进行健康检查。请只回复 OK。",
            )
            if "ok" not in raw.strip().lower():
                raise ModelGatewayError(f"Unexpected Codex CLI output: {raw[:120]}")
            message = raw[:240] or "OK"
        else:
            client = _create_openai_client(cfg, provider)
            if not healthcheck_model:
                raise ModelGatewayError("healthcheck_model not configured")
            response = client.chat.completions.create(
                model=healthcheck_model,
                messages=[
                    {"role": "system", "content": "You are a healthcheck probe. Reply with OK only."},
                    {"role": "user", "content": "Reply with OK only."},
                ],
                temperature=0,
                max_tokens=8,
            )
            content = (response.choices[0].message.content or "").strip()
            if "ok" not in content.lower():
                raise ModelGatewayError(f"Unexpected healthcheck output: {content[:120]}")
            message = content[:240] or "OK"
    except Exception as exc:
        status = "error"
        message = f"{type(exc).__name__}: {exc}"

    provider["last_tested_at"] = tested_at
    provider["last_test_status"] = status
    provider["last_test_message"] = message[:500]
    return {
        "provider_id": provider_id,
        "status": status,
        "message": provider["last_test_message"],
        "tested_at": tested_at,
    }
