"""Provider-neutral types for talking to LLMs."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


class LLMProvider(Protocol):
    """One configured model. Implementations live next to this file."""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Yield the assistant reply as text deltas."""
        ...
