"""Provider-neutral types for talking to LLMs."""

import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    # Last turn of a prefix that other threads (or later turns) send byte-for-byte
    # too. Providers with explicit prompt caching cache up to here; others ignore it.
    cache_breakpoint: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class TokenCount:
    count: int
    estimated: bool  # True when approximated rather than counted by the provider


@dataclass
class Usage:
    """Token usage of one reply, filled in by the provider as far as it reports it."""

    input_tokens: int = 0  # prompt tokens processed at full price (not from cache)
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class LLMProvider(Protocol):
    """One configured model. Implementations live next to this file."""

    def stream_chat(
        self, messages: Sequence[ChatMessage], usage: Usage | None = None
    ) -> AsyncIterator[str]:
        """Yield the assistant reply as text deltas; fill `usage` once the reply is done.

        Raises if the reply ends abnormally (refused, cut off), after yielding
        whatever text arrived, so callers can keep the partial reply.
        """
        ...

    async def count_tokens(self, messages: Sequence[ChatMessage]) -> TokenCount:
        """Count the prompt tokens these messages would use."""
        ...


def estimate_tokens(messages: Sequence[ChatMessage]) -> TokenCount:
    """Rough count (~4 chars per token plus per-message overhead) for providers
    without a tokenizer endpoint. Good enough for budget warnings."""
    return TokenCount(sum(math.ceil(len(m.content) / 4) + 4 for m in messages), estimated=True)
