from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import ModelConfig, Settings, parse_settings
from app.db import init_db, make_engine
from app.llm.base import ChatMessage, TokenCount
from app.main import create_app


class FakeProvider:
    """Replays scripted deltas, optionally failing partway; records each request."""

    def __init__(self) -> None:
        self.deltas: list[str] = ["Hello", ", world"]
        self.fail_after: int | None = None
        self.requests: list[list[ChatMessage]] = []

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.requests.append(list(messages))
        for i, delta in enumerate(self.deltas):
            if self.fail_after == i:
                raise RuntimeError("model went away")
            yield delta

    async def count_tokens(self, messages: Sequence[ChatMessage]) -> TokenCount:
        return TokenCount(sum(len(m.content.split()) for m in messages), estimated=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return parse_settings(
        {
            "database_path": "test.db",
            "default_model": "fake",
            "token_budget": 30,
            "models": [
                {"id": "fake", "label": "Fake", "provider": "fake", "model": "fake-1"},
                {
                    "id": "other",
                    "label": "Other",
                    "provider": "fake",
                    "model": "fake-2",
                    "context_window": 1000,
                },
            ],
        },
        root=tmp_path,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(settings: Settings, provider: FakeProvider) -> Iterator[TestClient]:
    def provider_for(_config: ModelConfig) -> FakeProvider:
        return provider

    with TestClient(create_app(settings, provider_for)) as c:
        yield c


@pytest.fixture
def session(settings: Settings) -> Iterator[Session]:
    with init_db(make_engine(settings.database_url))() as s:
        yield s
