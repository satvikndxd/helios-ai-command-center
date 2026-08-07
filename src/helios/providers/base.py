from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from helios.config import Settings


@dataclass
class ProviderResult:
    """
    Normalized result from any model provider.
    """

    output_text: str
    provider: str
    model: str
    usage: dict[str, Any]
    raw: dict[str, Any]
    citations: list[Any] = field(default_factory=list)


class BaseProvider(ABC):
    """
    Base class for Helios model provider adapters.
    """

    @abstractmethod
    async def complete(
        self,
        request: dict[str, Any],
        settings: Settings,
    ) -> ProviderResult:
        raise NotImplementedError
