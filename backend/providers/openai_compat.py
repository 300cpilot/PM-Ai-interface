"""OpenAI-compatible provider. Covers: vllm, llamacpp, openai, openrouter, compatible."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from config import ProviderConfig
from providers.base import Provider

DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class OpenAICompatProvider(Provider):
    def __init__(self, cfg: ProviderConfig) -> None:
        super().__init__(cfg)
        self.base_url = (cfg.base_url or DEFAULT_URLS.get(cfg.type, "")).rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(), json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        if content := delta.get("content"):
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]

    async def health(self) -> tuple[bool, str]:
        try:
            models = await self.models()
            return True, f"ok ({len(models)} models)"
        except Exception as e:
            return False, str(e)
