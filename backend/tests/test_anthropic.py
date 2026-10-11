from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anthropic import AsyncAnthropic

from app.config import ModelConfig
from app.llm.anthropic import AnthropicProvider, to_request
from app.llm.base import ChatMessage, Usage

CONFIG = ModelConfig(
    id="claude",
    label="Claude",
    provider="anthropic",
    model="claude-opus-5-5",
    params={
        "max_tokens": 1000,
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    },
)


class FakeStream:
    def __init__(self, texts: list[str], final: Any) -> None:
        self._texts, self._final = texts, final

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            for t in self._texts:
                yield t

        return gen()

    async def get_final_message(self) -> Any:
        return self._final


class FakeMessages:
    """Stands in for client.beta.messages; records the keyword arguments of each call."""

    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self.final = SimpleNamespace(
            stop_reason=stop_reason,
            stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
            usage=SimpleNamespace(
                input_tokens=12,
                output_tokens=5,
                cache_read_input_tokens=900,
                cache_creation_input_tokens=None,
            ),
        )

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        return FakeStream(["Hel", "lo"], self.final)

    async def count_tokens(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(input_tokens=321)


def provider(messages: FakeMessages) -> AnthropicProvider:
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return AnthropicProvider(CONFIG, client=cast(AsyncAnthropic, client))


CONTEXT = [
    ChatMessage("system", "sys"),
    ChatMessage("user", "q1"),
    ChatMessage("assistant", "a1", cache_breakpoint=True),
    ChatMessage("user", "focus + question"),
]


def test_to_request_splits_system_and_marks_prefix_ends() -> None:
    system, turns = to_request(CONTEXT)

    assert system == [{"type": "text", "text": "sys"}]
    assert turns == [
        {"role": "user", "content": [{"type": "text", "text": "q1"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "a1", "cache_control": {"type": "ephemeral"}}],
        },
        {"role": "user", "content": [{"type": "text", "text": "focus + question"}]},
    ]


def test_to_request_keeps_only_the_deepest_three_breakpoints() -> None:
    messages = [ChatMessage("system", "sys")]
    for i in range(4):
        messages += [
            ChatMessage("user", f"q{i}"),
            ChatMessage("assistant", f"a{i}", cache_breakpoint=True),
        ]

    _, turns = to_request(messages)

    marked = [t["content"][0]["text"] for t in turns if "cache_control" in t["content"][0]]  # type: ignore[index]
    assert marked == ["a1", "a2", "a3"]


async def test_stream_chat_yields_text_and_reports_usage() -> None:
    messages = FakeMessages()
    usage = Usage()

    texts = [t async for t in provider(messages).stream_chat(CONTEXT, usage)]

    assert texts == ["Hel", "lo"]
    assert usage == Usage(input_tokens=12, output_tokens=5, cache_read_tokens=900)
    [call] = messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 1000
    assert call["cache_control"] == {"type": "ephemeral"}  # automatic caching for the tail
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["extra_body"] == {"fallbacks": "default"}
    assert call["system"] == [{"type": "text", "text": "sys"}]


@pytest.mark.parametrize(("stop_reason", "error"), [("refusal", "cyber"), ("max_tokens", "1000")])
async def test_abnormal_stops_raise_after_the_partial_text(stop_reason: str, error: str) -> None:
    texts: list[str] = []
    with pytest.raises(RuntimeError, match=error):
        async for t in provider(FakeMessages(stop_reason)).stream_chat(CONTEXT):
            texts.append(t)
    assert texts == ["Hel", "lo"]


async def test_count_tokens_is_exact() -> None:
    messages = FakeMessages()

    count = await provider(messages).count_tokens(CONTEXT)

    assert (count.count, count.estimated) == (321, False)
    assert messages.calls[0]["model"] == "claude-opus-5-5"
    assert "extra_body" not in messages.calls[0]  # fallbacks are for generation only


def test_missing_api_key_env_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTIL_TEST_KEY", raising=False)
    config = ModelConfig(
        id="c", label="C", provider="anthropic", model="m", api_key_env="POSTIL_TEST_KEY"
    )
    with pytest.raises(RuntimeError, match="POSTIL_TEST_KEY is not set"):
        AnthropicProvider(config)
