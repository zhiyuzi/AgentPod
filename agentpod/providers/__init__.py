"""Provider layer: model provider abstractions and implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpod.providers.base import (
    CachePricing,
    ChatResponse,
    ModelInfo,
    ModelProvider,
    PricingRule,
    calculate_cost,
)

if TYPE_CHECKING:
    from agentpod.config import ProviderConfig


class ProviderRegistry:
    """Registry that maps provider names to ModelProvider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, name: str, provider: ModelProvider) -> None:
        self._providers[name] = provider

    def get_provider(self, name: str) -> ModelProvider:
        if name not in self._providers:
            available = ", ".join(self._providers) or "(none)"
            raise KeyError(
                f"Provider '{name}' not registered. Available: {available}"
            )
        return self._providers[name]

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())


_PROVIDER_CLASSES: dict[str, str] = {
    "volcengine": "agentpod.providers.volcengine.VolcEngineProvider",
}


def _import_provider_class(dotted_path: str) -> type[ModelProvider]:
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def create_registry(
    configs: dict[str, ProviderConfig] | None = None,
) -> ProviderRegistry:
    """Build a ProviderRegistry, auto-registering providers whose API keys
    are present in *configs* (or loaded from environment when *configs* is
    ``None``).
    """
    if configs is None:
        from agentpod.config import load_provider_configs

        configs = load_provider_configs()

    registry = ProviderRegistry()
    for name, cfg in configs.items():
        if name not in _PROVIDER_CLASSES:
            continue
        cls = _import_provider_class(_PROVIDER_CLASSES[name])
        registry.register(name, cls(cfg))
    return registry


def get_provider(name: str, registry: ProviderRegistry | None = None) -> ModelProvider:
    """Convenience helper: return a provider by name from the given (or a
    freshly-created) registry."""
    if registry is None:
        registry = create_registry()
    return registry.get_provider(name)


__all__ = [
    "CachePricing",
    "ChatResponse",
    "ModelInfo",
    "ModelProvider",
    "PricingRule",
    "ProviderRegistry",
    "calculate_cost",
    "create_registry",
    "get_provider",
]
