import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ModelConfig, Settings
from app.main import create_app
from app.models import MessageVersion, Thread
from app.prompts import SYSTEM_PROMPT
from tests.conftest import FakeProvider


def parse_sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def new_conversation(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/conversations", json={})
    assert response.status_code == 201
    data: dict[str, Any] = response.json()
    return data


def send(client: TestClient, thread_id: int, content: str, **extra: Any) -> list[tuple[str, Any]]:
    response = client.post(f"/api/threads/{thread_id}/messages", json={"content": content, **extra})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def test_done_event_reports_usage_and_saves_output_tokens(client: TestClient) -> None:
    conversation = new_conversation(client)

    events = send(client, conversation["threads"][0]["id"], "Hi")

    name, data = events[-1]
    assert name == "done"
    assert data["usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": 7,
        "cache_write_tokens": 3,
    }
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["threads"][0]["messages"][1]["token_count"] == 2


def test_models_lists_configured_models(client: TestClient) -> None:
    data = client.get("/api/models").json()
    assert data["default_model"] == "fake"
    assert [m["id"] for m in data["models"]] == ["fake", "other"]


def test_models_without_their_api_key_are_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyed = ModelConfig(
        id="keyed", label="Keyed", provider="fake", model="k", api_key_env="POSTIL_TEST_KEY"
    )
    with_key = replace(settings, models={**settings.models, "keyed": keyed})
    monkeypatch.delenv("POSTIL_TEST_KEY", raising=False)
    with TestClient(create_app(with_key)) as c:
        models = {m["id"]: m for m in c.get("/api/models").json()["models"]}
    assert models["fake"]["available"] is True
    assert models["keyed"] == {
        "id": "keyed",
        "label": "Keyed",
        "available": False,
        "missing_env": "POSTIL_TEST_KEY",
    }

    monkeypatch.setenv("POSTIL_TEST_KEY", "x")
    with TestClient(create_app(with_key)) as c:
        assert c.get("/api/models").json()["models"][-1]["available"] is True


def test_new_conversation_has_one_empty_main_thread(client: TestClient) -> None:
    conversation = new_conversation(client)
    [thread] = conversation["threads"]
    assert thread["kind"] == "main"
    assert thread["model"] == "fake"
    assert thread["messages"] == []


def test_send_streams_reply_and_persists_both_messages(client: TestClient) -> None:
    conversation = new_conversation(client)
    thread_id = conversation["threads"][0]["id"]

    events = send(client, thread_id, "Hi there")

    assert [name for name, _ in events] == ["user_message", "delta", "delta", "done"]
    assert events[0][1]["content"] == "Hi there"
    assert "".join(e[1]["text"] for e in events if e[0] == "delta") == "Hello, world"
    assert events[-1][1]["message"]["content"] == "Hello, world"

    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    messages = saved["threads"][0]["messages"]
    assert [(m["position"], m["role"], m["content"]) for m in messages] == [
        (0, "user", "Hi there"),
        (1, "assistant", "Hello, world"),
    ]
    assert messages[1]["model"] == "fake"
    assert saved["title"] == "Hi there"


def test_second_send_includes_history(client: TestClient, provider: FakeProvider) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    send(client, thread_id, "first")
    send(client, thread_id, "second")

    assert [(m.role, m.content) for m in provider.requests[1]] == [
        ("system", SYSTEM_PROMPT),
        ("user", "first"),
        ("assistant", "Hello, world"),
        ("user", "second"),
    ]


def test_provider_failure_saves_partial_reply_with_error(
    client: TestClient, provider: FakeProvider
) -> None:
    provider.fail_after = 1
    conversation = new_conversation(client)
    thread_id = conversation["threads"][0]["id"]

    events = send(client, thread_id, "Hi")

    name, data = events[-1]
    assert name == "error"
    assert data["error"] == "model went away"
    assert data["message"]["content"] == "Hello"
    assert data["message"]["error"] == "model went away"


def test_model_override_is_remembered_on_thread(client: TestClient) -> None:
    conversation = new_conversation(client)
    thread_id = conversation["threads"][0]["id"]

    events = send(client, thread_id, "Hi", model="other")

    assert events[-1][1]["message"]["model"] == "other"
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["threads"][0]["model"] == "other"


def test_unknown_model_is_rejected(client: TestClient) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    response = client.post(
        f"/api/threads/{thread_id}/messages", json={"content": "Hi", "model": "nope"}
    )
    assert response.status_code == 422


def test_missing_thread_and_conversation_return_404(client: TestClient) -> None:
    assert client.post("/api/threads/999/messages", json={"content": "x"}).status_code == 404
    assert client.get("/api/conversations/999").status_code == 404


def test_conversations_are_listed_newest_first(client: TestClient) -> None:
    first = new_conversation(client)
    second = new_conversation(client)
    ids = [c["id"] for c in client.get("/api/conversations").json()]
    assert ids == [second["id"], first["id"]]


def test_timestamps_are_utc(client: TestClient) -> None:
    conversation = new_conversation(client)
    thread_id = conversation["threads"][0]["id"]
    send(client, thread_id, "Hi")
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["created_at"].endswith("Z")
    assert saved["threads"][0]["messages"][0]["created_at"].endswith("Z")


def test_assistant_replies_get_a_v1_version(client: TestClient, session: Session) -> None:
    conversation = new_conversation(client)
    events = send(client, conversation["threads"][0]["id"], "Hi")
    reply_id = events[-1][1]["message"]["id"]

    versions = session.scalars(select(MessageVersion).where(MessageVersion.message_id == reply_id))
    assert [(v.version, v.content) for v in versions] == [(1, "Hello, world")]


def test_context_preview_shows_assembled_messages_and_tokens(client: TestClient) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    send(client, thread_id, "Hi")

    preview = client.get(f"/api/threads/{thread_id}/context-preview").json()

    assert preview["model"] == "fake"
    assert [(m["role"], m["content"]) for m in preview["messages"]] == [
        ("system", SYSTEM_PROMPT),
        ("user", "Hi"),
        ("assistant", "Hello, world"),
    ]
    # FakeProvider counts words.
    assert preview["token_count"] == len(SYSTEM_PROMPT.split()) + 3
    assert preview["token_count_estimated"] is False
    assert preview["token_budget"] == 100
    assert preview["over_budget"] is False


def test_context_preview_flags_over_budget(client: TestClient) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    send(client, thread_id, "word " * 120)

    assert client.get(f"/api/threads/{thread_id}/context-preview").json()["over_budget"] is True


def test_context_preview_for_side_thread_inherits_parent_model(
    client: TestClient, session: Session
) -> None:
    conversation = new_conversation(client)
    main_id = conversation["threads"][0]["id"]
    events = send(client, main_id, "Hi", model="other")
    anchor_id = events[-1][1]["message"]["id"]
    side = Thread(
        conversation_id=conversation["id"],
        kind="side",
        parent_thread_id=main_id,
        anchor_message_id=anchor_id,
        anchor_quote="world",
        anchor_version=1,
    )
    session.add(side)
    session.commit()

    preview = client.get(f"/api/threads/{side.id}/context-preview").json()

    assert preview["model"] == "other"
    assert [m["role"] for m in preview["messages"]] == ["system", "user", "assistant", "user"]
    assert '<focus message="msg_' in preview["messages"][-1]["content"]


def test_context_preview_errors(client: TestClient, session: Session) -> None:
    assert client.get("/api/threads/999/context-preview").status_code == 404

    conversation = new_conversation(client)
    side = Thread(conversation_id=conversation["id"], kind="side")  # no parent/anchor
    session.add(side)
    session.commit()
    assert client.get(f"/api/threads/{side.id}/context-preview").status_code == 409


def test_context_preview_budget_is_the_models_context_window(client: TestClient) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    send(client, thread_id, "word " * 40, model="other")

    preview = client.get(f"/api/threads/{thread_id}/context-preview").json()

    assert preview["token_budget"] == 1000
    assert preview["over_budget"] is False
