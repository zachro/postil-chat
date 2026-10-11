from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.merge import clean_note
from tests.conftest import FakeProvider
from tests.test_api import send
from tests.test_side_threads import create_side, main_reply


def side_with_exchange(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """(conversation, side thread) where the side thread has one exchange."""
    conversation, reply = main_reply(client)
    side = create_side(client, reply["id"], section="§2").json()
    send(client, side["id"], "Which world?")
    return conversation, side


def test_summarize_proposes_a_note_without_storing_it(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, side = side_with_exchange(client)
    provider.deltas = ["Note: User means ", "Earth.\n\n"]

    response = client.post(f"/api/threads/{side['id']}/summarize")

    assert response.status_code == 200
    assert response.json() == {"note_text": "User means Earth.", "model": "fake"}
    request = provider.requests[-1]
    assert [m.role for m in request] == ["system", "user"]
    assert "User: Which world?\n\nAssistant: Hello, world" in request[1].content
    thread = client.get(f"/api/conversations/{conversation['id']}").json()["threads"][1]
    assert thread["status"] == "open"
    assert thread["merge_note"] is None


def test_summarize_rejects_main_empty_and_closed_threads(client: TestClient) -> None:
    conversation, reply = main_reply(client)
    empty = create_side(client, reply["id"]).json()
    main_id = conversation["threads"][0]["id"]

    assert client.post(f"/api/threads/{main_id}/summarize").status_code == 422
    assert client.post(f"/api/threads/{empty['id']}/summarize").status_code == 422
    client.patch(f"/api/threads/{empty['id']}", json={"status": "resolved"})
    assert client.post(f"/api/threads/{empty['id']}/summarize").status_code == 409
    assert client.post("/api/threads/999/summarize").status_code == 404


def test_summarizer_failure_is_a_502(client: TestClient, provider: FakeProvider) -> None:
    _, side = side_with_exchange(client)
    provider.fail_after = 0

    response = client.post(f"/api/threads/{side['id']}/summarize")

    assert response.status_code == 502
    assert "model went away" in response.json()["detail"]


def test_merge_injects_the_note_into_the_next_main_turn(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, side = side_with_exchange(client)
    main_id = conversation["threads"][0]["id"]
    last_main_id = conversation_threads(client, conversation)[0]["messages"][-1]["id"]

    response = client.post(f"/api/threads/{side['id']}/merge", json={"note_text": " Earth. "})

    assert response.status_code == 200
    merged = response.json()
    assert merged["status"] == "merged"
    assert merged["merge_note"]["note_text"] == "Earth."
    assert merged["merge_note"]["target_thread_id"] == main_id
    assert merged["merge_note"]["inject_after_message_id"] == last_main_id

    send(client, main_id, "Next question")
    expected = "<resolved_side_threads>\n- [re §2] Earth.\n</resolved_side_threads>\nNext question"
    assert provider.requests[-1][-1].content == expected
    assert "Which world?" not in str(provider.requests[-1])


def test_merged_note_is_smaller_than_the_side_thread(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, side = side_with_exchange(client)
    main_id = conversation["threads"][0]["id"]
    provider.deltas = ["A long answer " * 50]
    send(client, side["id"], "Tell me everything")
    provider.deltas = ["ok"]
    before = client.get(f"/api/threads/{main_id}/context-preview").json()["token_count"]

    client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "Earth."})
    send(client, main_id, "Next")
    after = client.get(f"/api/threads/{main_id}/context-preview").json()["token_count"]

    side_tokens = client.get(f"/api/threads/{side['id']}/context-preview").json()["token_count"]
    # The note adds a handful of tokens; pasting the side thread would add hundreds.
    assert after - before < 20 < side_tokens - before


def test_merge_validation(client: TestClient) -> None:
    conversation, side = side_with_exchange(client)
    main_id = conversation["threads"][0]["id"]

    assert (
        client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "  "}).status_code == 422
    )
    assert client.post(f"/api/threads/{main_id}/merge", json={"note_text": "x"}).status_code == 422
    client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "x"})
    assert (
        client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "y"}).status_code == 409
    )


def test_resolve_without_merging_and_reopen(client: TestClient, provider: FakeProvider) -> None:
    conversation, side = side_with_exchange(client)
    main_id = conversation["threads"][0]["id"]
    url = f"/api/threads/{side['id']}"

    assert client.patch(url, json={"status": "resolved"}).json()["status"] == "resolved"
    assert client.post(f"{url}/messages", json={"content": "more?"}).status_code == 409
    send(client, main_id, "Next")
    assert "resolved_side_threads" not in provider.requests[-1][-1].content

    assert client.patch(url, json={"status": "open"}).json()["status"] == "open"
    assert send(client, side["id"], "more?")[-1][0] == "done"


def test_merged_threads_are_final(client: TestClient) -> None:
    conversation, side = side_with_exchange(client)
    url = f"/api/threads/{side['id']}"
    client.post(f"{url}/merge", json={"note_text": "x"})

    assert client.patch(url, json={"status": "open"}).status_code == 409
    assert client.post(f"{url}/messages", json={"content": "more?"}).status_code == 409
    main_id = conversation["threads"][0]["id"]
    assert client.patch(f"/api/threads/{main_id}", json={"status": "resolved"}).status_code == 422
    assert client.patch(url, json={"status": "merged"}).status_code == 422


def test_nested_thread_merges_into_its_parent_side_thread(
    client: TestClient, provider: FakeProvider
) -> None:
    _, side = side_with_exchange(client)
    side_reply = send(client, side["id"], "More")[-1][1]["message"]
    nested = create_side(client, side_reply["id"]).json()
    send(client, nested["id"], "Deeper")

    merged = client.post(f"/api/threads/{nested['id']}/merge", json={"note_text": "Deep fact."})

    assert merged.json()["merge_note"]["target_thread_id"] == side["id"]
    send(client, side["id"], "Back in the side thread")
    assert "Deep fact." in provider.requests[-1][-1].content


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("<think>\nhmm\n</think>\n\nUser runs Postgres 16.", "User runs Postgres 16."),
        ("**Note:** Prefer B.\n\n\nAvoid A.  ", "Prefer B.\nAvoid A."),
        ("  plain  ", "plain"),
    ],
)
def test_clean_note(raw: str, clean: str) -> None:
    assert clean_note(raw) == clean


def conversation_threads(client: TestClient, conversation: dict[str, Any]) -> list[Any]:
    data: list[Any] = client.get(f"/api/conversations/{conversation['id']}").json()["threads"]
    return data
