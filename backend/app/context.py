"""assemble_context(): the single place where model prompts are built.

Pure function of DB state, no side effects. See CLAUDE.md section 5.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import ChatMessage
from app.models import Message, Thread
from app.prompts import SYSTEM_PROMPT


def assemble_context(session: Session, thread_id: int) -> list[ChatMessage]:
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise LookupError(f"thread {thread_id} not found")
    if thread.kind != "main":
        raise NotImplementedError("side-thread context arrives in M2")

    messages = session.scalars(
        select(Message).where(Message.thread_id == thread_id).order_by(Message.position)
    )
    context = [ChatMessage("system", SYSTEM_PROMPT)]
    for m in messages:
        # A failed generation with no text adds nothing; a partial reply is kept,
        # since the user saw it and may refer to it.
        if m.role == "assistant" and m.error and not m.content:
            continue
        context.append(ChatMessage("user" if m.role == "user" else "assistant", m.content))
    return context
