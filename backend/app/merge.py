"""Merge-back: summarize a side thread into a note, then inject it (CLAUDE.md section 7).

The note is only proposed by the model; the user edits and approves it before
merge_thread() stores it. Where it shows up in context is decided by context.py.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context import assemble_summary_context
from app.llm import LLMProvider
from app.models import MergeNote, Message, Thread

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_PREAMBLE = re.compile(r"^\s*(\*\*)?note:?(\*\*)?:?\s*", re.IGNORECASE)


async def summarize_thread(session: Session, thread_id: int, provider: LLMProvider) -> str:
    """Ask the summarizer for a proposed note. ValueError if there is nothing to summarize."""
    context = assemble_summary_context(session, thread_id)
    parts = [delta async for delta in provider.stream_chat(context)]
    return clean_note("".join(parts))


def clean_note(text: str) -> str:
    """Drop reasoning blocks, a leading "Note:" label and blank lines from a model's note."""
    text = _PREAMBLE.sub("", _THINK.sub("", text).strip())
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def merge_thread(session: Session, thread: Thread, note_text: str) -> MergeNote:
    """Store an approved note for the thread's parent and mark the thread merged.

    The note attaches after the parent's last message as of now, so it only ever
    joins a future user turn and never rewrites what the model has already seen.
    """
    target = thread.parent_thread
    if thread.kind != "side" or target is None:
        raise ValueError("only side threads can be merged")
    last_message_id = session.scalar(
        select(Message.id)
        .where(Message.thread_id == target.id)
        .order_by(Message.position.desc())
        .limit(1)
    )
    note = MergeNote(
        thread_id=thread.id,
        target_thread_id=target.id,
        note_text=note_text.strip(),
        approved=True,
        inject_after_message_id=last_message_id,
    )
    session.add(note)
    thread.status = "merged"
    return note
