"""AI provider interface."""

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        pass

    def call_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        raise NotImplementedError("Tool calling not implemented for this provider")
