from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import FakeProvider
from tests.test_api import new_conversation, send
from tests.test_side_threads import create_side, main_reply


def search(client: TestClient, q: str) -> list[dict[str, Any]]:
    response = client.get("/api/search", params={"q": q})
    assert response.status_code == 200
    hits: list[dict[str, Any]] = response.json()
    return hits


def test_search_finds_messages_in_main_and_side_threads(client: TestClient) -> None:
    conversation, answer = main_reply(client)
    side = create_side(client, answer["id"]).json()
    send(client, side["id"], "Which WORLD is this?")

    hits = search(client, "world")

    # Newest first; side-thread hits carry their thread so the UI can open it.
    assert [(h["role"], h["thread_kind"], h["snippet"]) for h in hits] == [
        ("assistant", "side", "Hello, world"),
        ("user", "side", "Which WORLD is this?"),
        ("assistant", "main", "Hello, world"),
    ]
    assert hits[1]["thread_id"] == side["id"]
    assert hits[1]["conversation_id"] == conversation["id"]
    assert hits[1]["conversation_title"] == "Hi"
    assert (hits[1]["match_start"], hits[1]["match_end"]) == (6, 11)


def test_search_matches_titles_first(client: TestClient) -> None:
    main_reply(client)  # titled "Hi"
    other = new_conversation(client)
    send(client, other["threads"][0]["id"], "Other topic")

    hits = search(client, "hi")

    assert [(h["message_id"] is None, h["snippet"]) for h in hits] == [(True, "Hi"), (False, "Hi")]


def test_snippets_are_trimmed_around_the_match(client: TestClient, provider: FakeProvider) -> None:
    conversation = new_conversation(client)
    provider.deltas = ["a" * 200 + "  \n  needle\n\n   in the " + "b" * 200]
    send(client, conversation["threads"][0]["id"], "Find it")

    (hit,) = search(client, "NEEDLE")

    snippet = hit["snippet"]
    assert snippet.startswith("…") and snippet.endswith("…")
    assert snippet[hit["match_start"] : hit["match_end"]] == "needle"
    assert "\n" not in snippet and "  " not in snippet  # whitespace runs collapse to one space
    assert " needle in the b" in snippet
    assert len(snippet) < 200


def test_search_treats_wildcards_literally(client: TestClient, provider: FakeProvider) -> None:
    conversation = new_conversation(client)
    provider.deltas = ["100% sure, snake_case"]
    send(client, conversation["threads"][0]["id"], "Go")

    assert len(search(client, "0% s")) == 1
    assert len(search(client, "e_c")) == 1
    assert search(client, "1%e") == []  # % is not "anything"
    assert search(client, "100_") == []  # _ is not "any character"
    assert search(client, "   ") == []
