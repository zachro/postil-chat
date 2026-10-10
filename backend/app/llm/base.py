"""Provider-neutral types for talking to LLMs."""

import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class TokenCount:
    count: int
    estimated: bool  # True when approximated rather than counted by the provider


class LLMProvider(Protocol):
    """One configured model. Implementations live next to this file."""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Yield the assistant reply as text deltas."""
        ...

    async def count_tokens(self, messages: Sequence[ChatMessage]) -> TokenCount:
        """Count the prompt tokens these messages would use."""
        ...


def estimate_tokens(messages: Sequence[ChatMessage]) -> TokenCount:
    """Rough count (~4 chars per token plus per-message overhead) for providers
    without a tokenizer endpoint. Good enough for budget warnings."""
    return TokenCount(sum(math.ceil(len(m.content) / 4) + 4 for m in messages), estimated=True)
