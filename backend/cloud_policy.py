"""Source-controlled cloud freeze shared with the desktop client.

Only the fixed repository manifest can enable cloud features. Old environment
variables, saved sessions and user configuration cannot bypass this policy.
This module deliberately uses only the standard library so cloud startup can
be rejected before application imports initialize data or background workers.
"""

import json
import os
from pathlib import Path


_POLICY_FILE = Path(__file__).resolve().parents[1] / "cloud-capabilities.json"
CLOUD_DISABLED_MESSAGE = "云同步与移动端暂时冻结，本机功能仍可使用。"


class CloudFeaturesDisabled(RuntimeError):
    """A safe diagnostic that contains no configuration or credential values."""


def cloud_features_enabled() -> bool:
    try:
        policy = json.loads(_POLICY_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(policy, dict) and policy.get("enabled") is True


def require_cloud_enabled() -> None:
    if not cloud_features_enabled():
        raise CloudFeaturesDisabled(CLOUD_DISABLED_MESSAGE)


def require_cloud_api_enabled() -> None:
    if not cloud_features_enabled():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail={"error": "cloud_disabled", "message": CLOUD_DISABLED_MESSAGE},
        )


def ensure_cloud_deployment_allowed() -> None:
    if os.environ.get("KNOWRA_DEPLOY_MODE", "local").strip().lower() == "cloud":
        require_cloud_enabled()
