"""
engine/integrations/__init__.py
=================================
Engine integrations layer — exposes OpenRouter client and the discovery
manager that orchestrates the AI-assisted selector discovery pipeline.
"""
from engine.integrations.openrouter_integration import OpenRouterClient
from engine.integrations.discovery_manager import DiscoveryManager

__all__ = [
    "OpenRouterClient",
    "DiscoveryManager",
]
