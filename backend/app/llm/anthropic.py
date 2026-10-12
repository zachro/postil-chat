"""Adapter for the Anthropic Messages API (Claude), with explicit prompt caching.

Cache layout (at most 4 breakpoints per request):
- an explicit breakpoint on each message assemble_context() marks as the end of
  a shared prefix (the anchor of each side thread in the chain), so sibling side
  threads read the parent history from cache;
- top-level automatic caching for the growing tail of the conversation.
"""

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam

from app.config import ModelConfig
from app.llm.base import ChatMessage, TokenCount, Usage

DEFAULT_MAX_TOKENS = 64000  # streaming, so a high cap does not risk HTTP timeouts
MAX_EXPLICIT_BREAKPOINTS = 3  # the API allows 4; automatic caching takes one


class AnthropicProvider:
    def __init__(self, config: ModelConfig, client: AsyncAnthropic | None = None) -> None:
        if client is None:
            api_key = None  # None: the SDK finds credentials itself (env, `ant` profile)
            if config.api_key_env:
                api_key = os.environ.get(config.api_key_env, "")
                if not api_key:
                    raise RuntimeError(f"{config.api_key_env} is not set (needed by {config.id})")
            client = AsyncAnthropic(api_key=api_key, base_url=config.base_url)
        self._client = client
        self._config = config
        params = dict(config.params)
        self._max_tokens: int = params.pop("max_tokens", DEFAULT_MAX_TOKENS)
        self._betas: list[str] = params.pop("betas", [])
        self._extra: dict[str, Any] = params  # e.g. fallbacks, output_config: sent as-is

    async def stream_chat(
        self, messages: Sequence[ChatMessage], usage: Usage | None = None
    ) -> AsyncIterator[str]:
        system, turns = to_request(messages)
        async with self._client.beta.messages.stream(
            model=self._config.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=turns,
            cache_control={"type": "ephemeral"},
            betas=self._betas,
            extra_body=self._extra,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()

        if usage is not None:
            usage.input_tokens = final.usage.input_tokens
            usage.output_tokens = final.usage.output_tokens
            usage.cache_read_tokens = final.usage.cache_read_input_tokens or 0
            usage.cache_write_tokens = final.usage.cache_creation_input_tokens or 0
        if final.stop_reason == "refusal":
            category = final.stop_details.category if final.stop_details else None
            raise RuntimeError(f"the model declined to answer ({category or 'no category'})")
        if final.stop_reason == "max_tokens":
            raise RuntimeError(f"reply cut off at max_tokens ({self._max_tokens})")

    async def count_tokens(self, messages: Sequence[ChatMessage]) -> TokenCount:
        system, turns = to_request(messages)
        result = await self._client.beta.messages.count_tokens(
            model=self._config.model, system=system, messages=turns
        )
        return TokenCount(result.input_tokens, estimated=False)


def to_request(
    messages: Sequence[ChatMessage],
) -> tuple[list[BetaTextBlockParam], list[BetaMessageParam]]:
    """Split out the system prompt and add cache_control to the marked prefix ends."""
    marked = [i for i, m in enumerate(messages) if m.cache_breakpoint and m.role != "system"]
    keep = set(marked[-MAX_EXPLICIT_BREAKPOINTS:])  # the deepest prefixes are the longest
    system: list[BetaTextBlockParam] = []
    turns: list[BetaMessageParam] = []
    for i, m in enumerate(messages):
        block: BetaTextBlockParam = {"type": "text", "text": m.content}
        if i in keep:
            block["cache_control"] = {"type": "ephemeral"}
        if m.role == "system":
            system.append(block)
        else:
            turns.append({"role": m.role, "content": [block]})
    return system, turns
