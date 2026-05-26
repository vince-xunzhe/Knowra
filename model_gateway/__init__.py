from .catalog import (
    AVAILABLE_PROVIDER_TYPES,
    BUILTIN_MODEL_REGISTRY,
    DEFAULT_PROVIDER_REGISTRY,
    TASK_BINDING_DEFAULTS,
    TASK_SPECS,
)
from .config import (
    ensure_model_gateway_config,
    get_bound_model_id,
    get_task_binding,
    get_model_entry,
    get_provider_entry,
    mask_model_gateway_config,
    sync_legacy_fields,
)
from .health import run_provider_healthcheck
from .runtime import (
    ModelGatewayError,
    ProviderCapabilityError,
    call_text_model,
    create_openai_client_for_model,
    embed_text,
)
from .telemetry import task_context, track_call

__all__ = [
    "AVAILABLE_PROVIDER_TYPES",
    "BUILTIN_MODEL_REGISTRY",
    "DEFAULT_PROVIDER_REGISTRY",
    "TASK_BINDING_DEFAULTS",
    "TASK_SPECS",
    "ModelGatewayError",
    "ProviderCapabilityError",
    "call_text_model",
    "create_openai_client_for_model",
    "embed_text",
    "ensure_model_gateway_config",
    "get_bound_model_id",
    "get_task_binding",
    "get_model_entry",
    "get_provider_entry",
    "mask_model_gateway_config",
    "run_provider_healthcheck",
    "sync_legacy_fields",
    "task_context",
    "track_call",
]
