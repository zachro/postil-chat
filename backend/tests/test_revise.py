from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.revise import clean_revision
from tests.conftest import FakeProvider
from tests.test_api import parse_sse
from tests.test_side_threads import create_side, main_reply


def merged_reply(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A main-thread reply ("Hello, world") with one merged side thread on "world"."""
    conversation, reply = main_reply(client)
    side = create_side(client, reply["id"], section="§1").json()
    client.post(f"/api/threads/{side['id']}/messages", json={"content": "Which world?"})
    client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "Earth."})
    return conversation, reply


def revise(client: TestClient, message_id: int) -> list[tuple[str, Any]]:
    response = client.post(f"/api/messages/{message_id}/revise")
    assert response.status_code == 200
    return parse_sse(response.text)


def accept(client: TestClient, message_id: int, **body: Any) -> Any:
    return client.post(f"/api/messages/{message_id}/versions", json=body)


def test_revise_streams_a_proposal_and_stores_nothing(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, reply = merged_reply(client)
    provider.deltas = ["Hello, ", "Earth"]

    events = revise(client, reply["id"])

    assert [e for e, _ in events] == ["delta", "delta", "done"]
    assert events[-1][1] == {"content": "Hello, Earth", "model": "fake", "reason": "Applied §1"}
    request = provider.requests[-1]
    assert request[-2].content == "Hello, world"
    assert "- [re §1] Earth." in request[-1].content
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["threads"][0]["messages"][1]["content"] == "Hello, world"
    assert saved["threads"][0]["messages"][1]["version"] == 1


def test_revise_needs_merged_side_threads(client: TestClient) -> None:
    _, reply = main_reply(client)
    response = client.post(f"/api/messages/{reply['id']}/revise")
    assert response.status_code == 422
    assert client.post("/api/messages/9999/revise").status_code == 404


def test_revise_reports_provider_failure(client: TestClient, provider: FakeProvider) -> None:
    _, reply = merged_reply(client)
    provider.fail_after = 1
    events = revise(client, reply["id"])
    assert events[-1] == ("error", {"error": "model went away"})


def test_accepting_a_version_updates_the_message(client: TestClient) -> None:
    conversation, reply = merged_reply(client)

    response = accept(
        client, reply["id"], content="Hello, Earth", reason="Applied §1", base_version=1
    )

    assert response.status_code == 200
    message = response.json()
    assert (message["content"], message["version"]) == ("Hello, Earth", 2)
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["threads"][0]["messages"][1]["content"] == "Hello, Earth"
    versions = client.get(f"/api/messages/{reply['id']}/versions").json()
    assert [(v["version"], v["content"], v["reason"]) for v in versions] == [
        (1, "Hello, world", None),
        (2, "Hello, Earth", "Applied §1"),
    ]
    # The applied note is no longer pending.
    assert client.post(f"/api/messages/{reply['id']}/revise").status_code == 422


def test_side_thread_keeps_the_version_it_was_anchored_to(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, reply = main_reply(client)
    side = create_side(client, reply["id"]).json()
    accept(client, reply["id"], content="Hello, Earth")

    client.post(f"/api/threads/{side['id']}/messages", json={"content": "Which world?"})

    assert provider.requests[-1][2].content == "Hello, world"
    main_id = conversation["threads"][0]["id"]
    client.post(f"/api/threads/{main_id}/messages", json={"content": "And now?"})
    assert provider.requests[-1][2].content == "Hello, Earth"


def test_stale_or_invalid_versions_are_rejected(client: TestClient) -> None:
    conversation, reply = main_reply(client)
    question = client.get(f"/api/conversations/{conversation['id']}").json()["threads"][0][
        "messages"
    ][0]

    assert accept(client, reply["id"], content="x", base_version=2).status_code == 409
    assert accept(client, reply["id"], content=" ").status_code == 422
    assert accept(client, question["id"], content="x").status_code == 422
    assert accept(client, 9999, content="x").status_code == 404


def test_messages_in_closed_threads_cannot_be_revised(client: TestClient) -> None:
    _, reply = main_reply(client)
    side = create_side(client, reply["id"]).json()
    side_reply = parse_sse(
        client.post(f"/api/threads/{side['id']}/messages", json={"content": "Which world?"}).text
    )[-1][1]["message"]
    client.patch(f"/api/threads/{side['id']}", json={"status": "resolved"})

    assert accept(client, side_reply["id"], content="x").status_code == 409
    assert client.post(f"/api/messages/{side_reply['id']}/revise").status_code == 409


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>hm</think>\n\n§1 Text\n", "§1 Text"),
        ("```markdown\n§1 Text\n```", "§1 Text"),
        ("```\n§1 Text\n```\n", "§1 Text"),
        ("Intro\n\n```py\ncode\n```", "Intro\n\n```py\ncode\n```"),
    ],
)
def test_clean_revision(raw: str, clean: str) -> None:
    assert clean_revision(raw) == clean
