"""Adapter for OpenAI-compatible chat APIs (Ollama, OpenAI, OpenRouter, vLLM, LM Studio)."""

import os
from collections.abc import AsyncIterator, Sequence

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import ModelConfig
from app.llm.base import ChatMessage, TokenCount, Usage, estimate_tokens


class OpenAICompatProvider:
    def __init__(self, config: ModelConfig) -> None:
        api_key = "unused"  # local servers like Ollama ignore the key, but the SDK requires one
        if config.api_key_env:
            api_key = os.environ.get(config.api_key_env, "")
            if not api_key:
                raise RuntimeError(f"{config.api_key_env} is not set (needed by {config.id})")
        self._client = AsyncOpenAI(base_url=config.base_url, api_key=api_key)
        self._config = config

    async def stream_chat(
        self, messages: Sequence[ChatMessage], usage: Usage | None = None
    ) -> AsyncIterator[str]:
        # Prompt caching here is automatic (OpenAI caches long prefixes; Ollama
        # reuses its KV cache), so ChatMessage.cache_breakpoint is not needed.
        stream = await self._client.chat.completions.create(
            model=self._config.model,
            messages=[_to_param(m) for m in messages],
            stream=True,
            stream_options={"include_usage": True},
            extra_body=self._config.params,
        )
        async for chunk in stream:
            # Reasoning models may also send a separate `reasoning` delta; it is
            # not part of the reply, so only `content` is forwarded.
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage and usage is not None:  # the last chunk, if the server reports it
                details = chunk.usage.prompt_tokens_details
                cached = (details.cached_tokens or 0) if details else 0
                usage.input_tokens = chunk.usage.prompt_tokens - cached
                usage.cache_read_tokens = cached
                usage.output_tokens = chunk.usage.completion_tokens

    async def count_tokens(self, messages: Sequence[ChatMessage]) -> TokenCount:
        # OpenAI-compatible servers have no standard tokenize endpoint.
        return estimate_tokens(messages)


def _to_param(message: ChatMessage) -> ChatCompletionMessageParam:
    if message.role == "system":
        return {"role": "system", "content": message.content}
    if message.role == "user":
        return {"role": "user", "content": message.content}
    return {"role": "assistant", "content": message.content}
