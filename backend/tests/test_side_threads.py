from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import MessageVersion
from tests.conftest import FakeProvider
from tests.test_api import new_conversation, send


def main_reply(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A conversation whose main thread has one exchange; returns (conversation, reply)."""
    conversation = new_conversation(client)
    events = send(client, conversation["threads"][0]["id"], "Hi")
    reply: dict[str, Any] = events[-1][1]["message"]  # content "Hello, world"
    return conversation, reply


def create_side(
    client: TestClient, message_id: int, quote: str = "world", start: int = 7, **extra: Any
) -> Any:
    return client.post(
        f"/api/messages/{message_id}/threads",
        json={"quote": quote, "start": start, "end": start + len(quote), **extra},
    )


def test_create_side_thread(client: TestClient) -> None:
    conversation, reply = main_reply(client)

    response = create_side(client, reply["id"], section="§2")

    assert response.status_code == 201
    thread = response.json()
    assert thread["kind"] == "side"
    assert thread["parent_thread_id"] == conversation["threads"][0]["id"]
    assert thread["anchor_message_id"] == reply["id"]
    assert (thread["anchor_quote"], thread["anchor_start"], thread["anchor_end"]) == (
        "world",
        7,
        12,
    )
    assert thread["anchor_section"] == "§2"
    assert thread["anchor_version"] == 1
    assert thread["context_mode"] == "as_of_anchor"
    assert thread["status"] == "open"
    assert thread["model"] is None  # inherits from the parent at request time
    assert thread["messages"] == []

    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [t["kind"] for t in saved["threads"]] == ["main", "side"]


def test_side_thread_chat_sends_focus_and_parent_context(
    client: TestClient, provider: FakeProvider
) -> None:
    _, reply = main_reply(client)
    side = create_side(client, reply["id"]).json()

    events = send(client, side["id"], "What world?")

    assert events[-1][0] == "done"
    sent = provider.requests[-1]
    assert [m.content for m in sent[1:3]] == ["Hi", "Hello, world"]
    assert f'<focus message="msg_{reply["id"]}">\nworld\n</focus>\nWhat world?' in sent[3].content
    assert events[-1][1]["message"]["model"] == "fake"


def test_anchor_version_is_the_latest_version(client: TestClient, session: Session) -> None:
    _, reply = main_reply(client)
    session.add(MessageVersion(message_id=reply["id"], version=2, content="Hello, world"))
    session.commit()

    assert create_side(client, reply["id"]).json()["anchor_version"] == 2


def test_quote_must_match_offsets(client: TestClient) -> None:
    _, reply = main_reply(client)

    assert create_side(client, reply["id"], quote="world", start=0).status_code == 422
    assert create_side(client, reply["id"], quote="world!", start=7).status_code == 422
    bad_range = {"quote": "x", "start": 5, "end": 5}
    assert client.post(f"/api/messages/{reply['id']}/threads", json=bad_range).status_code == 422


def test_only_assistant_messages_can_be_anchored(client: TestClient) -> None:
    conversation = new_conversation(client)
    events = send(client, conversation["threads"][0]["id"], "Hi there")
    user_message = events[0][1]

    assert create_side(client, user_message["id"], quote="there", start=3).status_code == 422
    assert create_side(client, 999).status_code == 404


def test_side_threads_nest_at_most_three_deep(client: TestClient) -> None:
    _, reply = main_reply(client)
    for _depth in range(3):
        thread = create_side(client, reply["id"]).json()
        reply = send(client, thread["id"], "Q")[-1][1]["message"]

    assert create_side(client, reply["id"]).status_code == 422


def test_update_thread_context_mode_and_title(client: TestClient) -> None:
    conversation, reply = main_reply(client)
    side = create_side(client, reply["id"]).json()

    response = client.patch(
        f"/api/threads/{side['id']}", json={"context_mode": "as_of_now", "title": "World?"}
    )

    assert response.status_code == 200
    assert response.json()["context_mode"] == "as_of_now"
    assert response.json()["title"] == "World?"
    main_id = conversation["threads"][0]["id"]
    assert (
        client.patch(f"/api/threads/{main_id}", json={"context_mode": "as_of_now"}).status_code
        == 422
    )
    assert (
        client.patch(f"/api/threads/{side['id']}", json={"context_mode": "bogus"}).status_code
        == 422
    )
    assert client.patch("/api/threads/999", json={"title": "x"}).status_code == 404
