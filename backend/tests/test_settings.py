import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Setting
from tests.test_api import new_conversation


def patch(client: TestClient, **body: Any) -> Any:
    return client.patch("/api/settings", json=body)


def test_settings_start_from_config(client: TestClient) -> None:
    data = client.get("/api/settings").json()

    assert data["default_model"] == "fake"
    assert data["summarizer_model"] is None
    assert data["token_budget"] == 100
    assert data["send_with_enter"] is False
    assert data["overridden"] == []
    assert [(m["id"], m["context_window"], m["config_context_window"]) for m in data["models"]] == [
        ("fake", None, None),
        ("other", 1000, 1000),
    ]


def test_default_model_override_applies_to_new_conversations(client: TestClient) -> None:
    response = patch(client, default_model="other")

    assert response.status_code == 200
    assert response.json()["overridden"] == ["default_model"]
    assert client.get("/api/models").json()["default_model"] == "other"
    assert new_conversation(client)["threads"][0]["model"] == "other"


def test_budget_overrides_change_the_context_preview(client: TestClient) -> None:
    thread_id = new_conversation(client)["threads"][0]["id"]
    preview = f"/api/threads/{thread_id}/context-preview"

    patch(client, token_budget=5)
    assert client.get(preview).json()["token_budget"] == 5
    assert client.get(preview).json()["over_budget"] is True

    data = patch(client, context_windows={"fake": 5000}).json()
    assert client.get(preview).json()["token_budget"] == 5000
    assert data["models"][0]["context_window"] == 5000
    assert data["models"][0]["config_context_window"] is None

    data = patch(client, context_windows={"fake": None}).json()  # None drops one override
    assert client.get(preview).json()["token_budget"] == 5
    assert data["overridden"] == ["token_budget"]


def test_summarizer_can_be_set_and_cleared(client: TestClient) -> None:
    assert patch(client, summarizer_model="other").json()["summarizer_model"] == "other"
    assert patch(client, send_with_enter=True).json()["summarizer_model"] == "other"
    data = patch(client, summarizer_model=None).json()  # explicit null: the thread's own model
    assert data["summarizer_model"] is None
    assert data["send_with_enter"] is True
    assert data["overridden"] == ["send_with_enter", "summarizer_model"]


def test_invalid_settings_are_rejected(client: TestClient) -> None:
    assert patch(client, default_model="nope").status_code == 422
    assert patch(client, default_model=None).status_code == 422
    assert patch(client, summarizer_model="nope").status_code == 422
    assert patch(client, token_budget=0).status_code == 422
    assert patch(client, context_windows={"nope": 100}).status_code == 422
    assert patch(client, context_windows={"fake": -1}).status_code == 422
    assert client.get("/api/settings").json()["overridden"] == []


def test_reset_restores_config(client: TestClient) -> None:
    patch(client, default_model="other", token_budget=7, context_windows={"other": 9})

    data = client.delete("/api/settings").json()

    assert (data["default_model"], data["token_budget"], data["overridden"]) == ("fake", 100, [])
    assert data["models"][1]["context_window"] == 1000


def test_stale_overrides_are_ignored(client: TestClient, session: Session) -> None:
    # A model removed from config.yaml after it was chosen in the UI.
    session.add(Setting(key="default_model", value=json.dumps("gone")))
    session.add(Setting(key="context_windows", value=json.dumps({"gone": 5, "other": 9})))
    session.commit()

    data = client.get("/api/settings").json()

    assert data["default_model"] == "fake"
    assert data["models"][1]["context_window"] == 9
    assert data["overridden"] == ["context_windows"]
