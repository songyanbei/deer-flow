"""Formal Engine Registry for the lead agent builder.

Provides a centralized registry of engine builders with:
- canonical name → builder lookup
- alias → canonical name resolution
- unknown engine fallback to default with warning
- listing of all supported engine types
"""

from __future__ import annotations

import logging

from src.agents.lead_agent.engines import (
    BaseEngineBuilder,
    DefaultEngineBuilder,
    ReactEngineBuilder,
    ReadOnlyExplorerEngineBuilder,
    SopEngineBuilder,
)
from src.config.agents_config import AgentConfig

logger = logging.getLogger(__name__)


class EngineRegistry:
    """Registry that maps engine type strings to builder instances."""

    def __init__(self) -> None:
        self._builders: dict[str, BaseEngineBuilder] = {}
        self._aliases: dict[str, str] = {}

    def register(self, builder: BaseEngineBuilder) -> None:
        """Register a builder by its canonical name and aliases."""
        canonical = builder.canonical_name
        self._builders[canonical] = builder
        # Register canonical name as its own alias (case-insensitive)
        self._aliases[canonical.lower()] = canonical
        for alias in builder.aliases:
            self._aliases[alias.lower()] = canonical

    def normalize_engine_type(self, raw: str | None) -> str | None:
        """Resolve a raw engine type string to its canonical form.

        Returns None if the input is empty/None.
        Logs a warning and returns 'default' for unknown values.
        """
        if not raw or not raw.strip():
            return None

        key = raw.strip().lower()
        canonical = self._aliases.get(key)
        if canonical is not None:
            return canonical

        logger.warning("Unknown engine_type '%s'; falling back to 'default'.", raw)
        return "default"

    def get_engine_builder(self, raw: str | None) -> BaseEngineBuilder:
        """Return the builder for the given engine type string.

        Falls back to the default builder for unknown or empty values.
        """
        canonical = self.normalize_engine_type(raw)
        if canonical is None:
            canonical = "default"
        return self._builders.get(canonical, self._builders["default"])

    def list_supported_engine_types(self) -> list[str]:
        """Return all canonical engine type names."""
        return sorted(self._builders.keys())


def _create_default_registry() -> EngineRegistry:
    """Build and populate the default engine registry."""
    registry = EngineRegistry()
    registry.register(DefaultEngineBuilder())
    registry.register(ReactEngineBuilder())
    registry.register(ReadOnlyExplorerEngineBuilder())
    registry.register(SopEngineBuilder())
    return registry


# Module-level singleton
engine_registry = _create_default_registry()

# --- Public convenience functions ---


def normalize_engine_type(raw: str | None) -> str | None:
    """Resolve a raw engine type string to its canonical form."""
    return engine_registry.normalize_engine_type(raw)


def get_engine_builder(raw: str | None) -> BaseEngineBuilder:
    """Return the builder for the given engine type string."""
    return engine_registry.get_engine_builder(raw)


def list_supported_engine_types() -> list[str]:
    """Return all canonical engine type names."""
    return engine_registry.list_supported_engine_types()
