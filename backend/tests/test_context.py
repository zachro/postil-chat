import pytest
from sqlalchemy.orm import Session

from app.context import assemble_context
from app.llm.base import ChatMessage
from app.models import Conversation, Message, Thread
from app.prompts import SYSTEM_PROMPT


def make_thread(session: Session, kind: str = "main") -> Thread:
    thread = Thread(kind=kind)
    session.add(Conversation(title="t", threads=[thread]))
    session.flush()
    return thread


def add(
    session: Session, thread: Thread, role: str, content: str, error: str | None = None
) -> None:
    position = len(thread.messages)
    thread.messages.append(Message(position=position, role=role, content=content, error=error))
    session.flush()


def test_main_thread_is_system_prompt_then_messages_in_order(session: Session) -> None:
    thread = make_thread(session)
    add(session, thread, "user", "q1")
    add(session, thread, "assistant", "a1")
    add(session, thread, "user", "q2")

    assert assemble_context(session, thread.id) == [
        ChatMessage("system", SYSTEM_PROMPT),
        ChatMessage("user", "q1"),
        ChatMessage("assistant", "a1"),
        ChatMessage("user", "q2"),
    ]


def test_messages_are_ordered_by_position_not_insertion(session: Session) -> None:
    thread = make_thread(session)
    session.add_all(
        [
            Message(thread_id=thread.id, position=1, role="assistant", content="a1"),
            Message(thread_id=thread.id, position=0, role="user", content="q1"),
        ]
    )
    session.flush()

    assert [m.content for m in assemble_context(session, thread.id)[1:]] == ["q1", "a1"]


def test_failed_empty_reply_is_skipped_but_partial_reply_is_kept(session: Session) -> None:
    thread = make_thread(session)
    add(session, thread, "user", "q1")
    add(session, thread, "assistant", "", error="timeout")
    add(session, thread, "user", "q2")
    add(session, thread, "assistant", "half an ans", error="cancelled")

    assert [m.content for m in assemble_context(session, thread.id)[1:]] == [
        "q1",
        "q2",
        "half an ans",
    ]


def test_unknown_thread_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        assemble_context(session, 999)


def test_side_threads_not_supported_yet(session: Session) -> None:
    thread = make_thread(session, kind="side")
    with pytest.raises(NotImplementedError):
        assemble_context(session, thread.id)
