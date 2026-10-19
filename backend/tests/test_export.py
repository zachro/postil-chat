from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import FakeProvider
from tests.test_api import new_conversation, send
from tests.test_inline_replies import reply
from tests.test_side_threads import create_side, main_reply


def branching_conversation(client: TestClient) -> dict[str, Any]:
    """Main exchange, a merged side thread (with a nested one), a revision and a comment."""
    conversation, answer = main_reply(client)
    main_id = conversation["threads"][0]["id"]
    side = create_side(client, answer["id"], section="§1").json()
    side_reply = send(client, side["id"], "Which world?")[-1][1]["message"]
    nested = create_side(client, side_reply["id"], context_mode="as_of_now").json()
    send(client, nested["id"], "Deeper?")
    client.post(f"/api/threads/{side['id']}/merge", json={"note_text": "Earth."})
    client.post(f"/api/messages/{answer['id']}/versions", json={"content": "Hello, Earth"})
    send(client, main_id, "Thanks", inline_replies=[reply(answer["id"], "Hello", 0, comment="Hi?")])
    return conversation


def export(client: TestClient, conversation_id: int, fmt: str) -> Any:
    return client.get(f"/api/conversations/{conversation_id}/export", params={"format": fmt})


def test_markdown_export_nests_side_threads_under_their_anchor(client: TestClient) -> None:
    conversation = branching_conversation(client)

    response = export(client, conversation["id"], "md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == 'attachment; filename="hi.md"'
    header, body = response.text.split("\n\n---\n\n", 1)
    assert header.startswith("# Hi\n\nExported from Postil. Started ")
    assert body == (
        "**You**\n\nHi\n\n---\n\n"
        "**Assistant** · fake · v2\n\nHello, Earth\n\n"
        "> **Side thread on §1** · merged\n>\n> > world\n>\n"
        "> **You**\n>\n> Which world?\n>\n"
        "> **Assistant** · fake\n>\n> Hello, world\n>\n"
        "> > **Side thread** · sees later messages\n> >\n> > > world\n> >\n"
        "> > **You**\n> >\n> > Deeper?\n> >\n"
        "> > **Assistant** · fake\n> >\n> > Hello, world\n>\n"
        "> **Merge note:** Earth.\n\n---\n\n"
        '**You**\n\n- re "Hello": Hi?\n\nThanks\n\n---\n\n'
        "**Assistant** · fake\n\nHello, world\n"
    )


def test_markdown_export_marks_failed_replies(client: TestClient, provider: FakeProvider) -> None:
    conversation = new_conversation(client)
    provider.fail_after = 1
    send(client, conversation["threads"][0]["id"], "Hi")

    text = export(client, conversation["id"], "md").text

    assert text.endswith("**Assistant** · fake\n\nHello\n\n_Generation failed: model went away_\n")


def test_json_export_has_the_whole_tree_and_every_version(client: TestClient) -> None:
    conversation = branching_conversation(client)

    response = export(client, conversation["id"], "json")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="hi.json"'
    data = response.json()
    assert data["format"] == "postil-chat/1"
    assert "exported_at" in data
    assert [(t["kind"], t["status"]) for t in data["threads"]] == [
        ("main", "open"),
        ("side", "merged"),
        ("side", "open"),
    ]
    assert data["threads"][1]["merge_note"]["note_text"] == "Earth."
    answer = data["threads"][0]["messages"][1]
    assert answer["content"] == "Hello, Earth"
    assert [(v["version"], v["content"]) for v in answer["versions"]] == [
        (1, "Hello, world"),
        (2, "Hello, Earth"),
    ]
    assert data["threads"][0]["messages"][0]["versions"] == []  # user messages have none


def test_export_errors_and_filenames(client: TestClient) -> None:
    conversation = client.post("/api/conversations", json={"title": "Ünïcode / plans!"}).json()
    response = export(client, conversation["id"], "md")
    assert response.headers["content-disposition"] == 'attachment; filename="unicode-plans.md"'
    untitled = client.post("/api/conversations", json={"title": "???"}).json()
    response = export(client, untitled["id"], "json")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="conversation-{untitled["id"]}.json"'
    )

    assert export(client, 9999, "md").status_code == 404
    assert export(client, conversation["id"], "pdf").status_code == 422
