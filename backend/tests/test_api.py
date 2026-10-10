import json
from typing import Any

from fastapi.testclient import TestClient

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


def test_models_lists_configured_models(client: TestClient) -> None:
    data = client.get("/api/models").json()
    assert data["default_model"] == "fake"
    assert [m["id"] for m in data["models"]] == ["fake", "other"]


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
