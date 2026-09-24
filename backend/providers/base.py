"""Provider abstraction. All providers yield text chunks via async chat()."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from config import ProviderConfig


class Provider(ABC):
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream assistant text chunks for a chat-completions style message list."""
        ...

    @abstractmethod
    async def models(self) -> list[str]:
        ...

    @abstractmethod
    async def health(self) -> tuple[bool, str]:
        ...
