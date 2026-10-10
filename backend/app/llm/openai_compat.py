"""Adapter for OpenAI-compatible chat APIs (Ollama, OpenAI, OpenRouter, vLLM, LM Studio)."""

import os
from collections.abc import AsyncIterator, Sequence

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import ModelConfig
from app.llm.base import ChatMessage


class OpenAICompatProvider:
    def __init__(self, config: ModelConfig) -> None:
        api_key = "unused"  # local servers like Ollama ignore the key, but the SDK requires one
        if config.api_key_env:
            api_key = os.environ.get(config.api_key_env, "")
            if not api_key:
                raise RuntimeError(f"{config.api_key_env} is not set (needed by {config.id})")
        self._client = AsyncOpenAI(base_url=config.base_url, api_key=api_key)
        self._config = config

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._config.model,
            messages=[_to_param(m) for m in messages],
            stream=True,
            extra_body=self._config.params,
        )
        async for chunk in stream:
            # Reasoning models may also send a separate `reasoning` delta; it is
            # not part of the reply, so only `content` is forwarded.
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


def _to_param(message: ChatMessage) -> ChatCompletionMessageParam:
    if message.role == "system":
        return {"role": "system", "content": message.content}
    if message.role == "user":
        return {"role": "user", "content": message.content}
    return {"role": "assistant", "content": message.content}
