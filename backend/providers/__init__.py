"""Provider registry. Maps config provider type -> implementation."""
from __future__ import annotations

from config import ProviderConfig
from providers.anthropic import AnthropicProvider
from providers.base import Provider
from providers.ollama import OllamaProvider
from providers.openai_compat import OpenAICompatProvider

# vllm, llamacpp, openai, openrouter, compatible all speak OpenAI-compatible
OPENAI_COMPAT_TYPES = {"vllm", "llamacpp", "openai", "openrouter", "compatible"}


def build(cfg: ProviderConfig) -> Provider:
    if cfg.type in OPENAI_COMPAT_TYPES:
        return OpenAICompatProvider(cfg)
    if cfg.type == "ollama":
        return OllamaProvider(cfg)
    if cfg.type == "anthropic":
        return AnthropicProvider(cfg)
    raise ValueError(f"unknown provider type: {cfg.type}")
