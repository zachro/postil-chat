from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import FakeProvider
from tests.test_api import new_conversation, send
from tests.test_side_threads import create_side, main_reply


def reply(message_id: int, quote: str = "world", start: int = 7, **extra: Any) -> dict[str, Any]:
    return {
        "target_message_id": message_id,
        "quote": quote,
        "start": start,
        "end": start + len(quote),
        "comment": "Which world?",
        **extra,
    }


def post(client: TestClient, thread_id: int, body: dict[str, Any]) -> Any:
    return client.post(f"/api/threads/{thread_id}/messages", json=body)


def test_multi_anchor_reply_is_stored_and_sent_as_inline_replies(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation, answer = main_reply(client)
    thread_id = conversation["threads"][0]["id"]

    replies = [reply(answer["id"], section="§1"), reply(answer["id"], "Hello", 0, comment="Hi?")]
    events = send(client, thread_id, "", inline_replies=replies)

    user_message = events[0][1]
    assert user_message["content"] == ""
    assert [
        (r["target_message_id"], r["quote"], r["start"], r["end"], r["section"], r["comment"])
        for r in user_message["inline_replies"]
    ] == [
        (answer["id"], "world", 7, 12, "§1", "Which world?"),
        (answer["id"], "Hello", 0, 5, None, "Hi?"),
    ]
    assert user_message["inline_replies"][0]["target_version"] == 1
    assert provider.requests[-1][-1].content == (
        f'<inline_replies to="msg_{answer["id"]}">\n'
        '  <reply anchor="§1">Which world?</reply>\n'
        '  <reply quote="Hello">Hi?</reply>\n'
        "</inline_replies>"
    )

    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["threads"][0]["messages"][2]["inline_replies"] == user_message["inline_replies"]


def test_plain_messages_have_no_inline_replies(client: TestClient) -> None:
    conversation, _ = main_reply(client)
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [m["inline_replies"] for m in saved["threads"][0]["messages"]] == [[], []]


def test_message_needs_text_or_inline_replies(client: TestClient) -> None:
    conversation = new_conversation(client)
    response = post(client, conversation["threads"][0]["id"], {"content": "  "})
    assert response.status_code == 422


def test_inline_reply_quote_must_match_offsets(client: TestClient) -> None:
    conversation, answer = main_reply(client)
    body = {"content": "", "inline_replies": [reply(answer["id"], "world", 6)]}
    response = post(client, conversation["threads"][0]["id"], body)
    assert response.status_code == 422


def test_inline_reply_comment_must_not_be_blank(client: TestClient) -> None:
    conversation, answer = main_reply(client)
    body = {"content": "", "inline_replies": [reply(answer["id"], comment=" ")]}
    response = post(client, conversation["threads"][0]["id"], body)
    assert response.status_code == 422


def test_inline_replies_target_assistant_messages_in_the_same_thread(client: TestClient) -> None:
    conversation, answer = main_reply(client)
    main_id = conversation["threads"][0]["id"]
    side = create_side(client, answer["id"]).json()
    side_answer = send(client, side["id"], "Why?")[-1][1]["message"]
    question = client.get(f"/api/conversations/{conversation['id']}").json()["threads"][0][
        "messages"
    ][0]

    for target, thread_id in [(side_answer, main_id), (answer, side["id"])]:
        body = {"content": "", "inline_replies": [reply(target["id"])]}
        assert post(client, thread_id, body).status_code == 422
    body = {"content": "", "inline_replies": [reply(question["id"], "Hi", 0)]}
    assert post(client, main_id, body).status_code == 422
    body = {"content": "", "inline_replies": [reply(9999)]}
    assert post(client, main_id, body).status_code == 422
