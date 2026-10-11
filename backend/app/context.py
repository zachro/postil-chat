"""assemble_context(): the single place where model prompts are built.

Pure function of DB state, no side effects. See CLAUDE.md section 5.

Every thread's context is the same system prompt followed by its turns:
- main thread: its own messages, with approved merge notes prepended to the
  first user turn after the point they were merged;
- side thread: its parent's turns (recursively, so nested threads get the whole
  chain) up to and including the anchor message, then a focus turn, then its own
  messages. Volatile parts come last, so siblings share a byte-identical prefix.

assemble_summary_context() builds the merge-back summarizer's request: just the
focus span and the side thread's own transcript, not the parent history.
"""

from collections import defaultdict
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import ChatMessage
from app.models import MergeNote, Message, MessageVersion, Thread
from app.prompts import (
    SIDE_THREAD_INSTRUCTION,
    SUMMARIZE_REQUEST,
    SUMMARIZER_PROMPT,
    SYSTEM_PROMPT,
)

NOTE_QUOTE_CHARS = 40  # note label fallback when the side thread has no section


@dataclass(frozen=True)
class _Turn:
    message_id: int | None  # None for synthesized turns (a focus with no question yet)
    message: ChatMessage


def assemble_context(session: Session, thread_id: int) -> list[ChatMessage]:
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise LookupError(f"thread {thread_id} not found")
    return [ChatMessage("system", SYSTEM_PROMPT), *(t.message for t in _turns(session, thread))]


def assemble_summary_context(session: Session, thread_id: int) -> list[ChatMessage]:
    """The summarizer's prompt for a side thread's merge note (CLAUDE.md section 7)."""
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise LookupError(f"thread {thread_id} not found")
    if thread.kind != "side":
        raise ValueError("only side threads are summarized")
    turns = _own_turns(session, thread)
    if not turns:
        raise ValueError(f"side thread {thread_id} has no messages to summarize")
    speaker = {"user": "User", "assistant": "Assistant"}
    transcript = "\n\n".join(f"{speaker[t.message.role]}: {t.message.content}" for t in turns)
    request = (
        f"<focus>\n{_escape(thread.anchor_quote or '')}\n</focus>\n"
        f"<{_TRANSCRIPT_TAG}>\n{_escape(transcript, (_TRANSCRIPT_TAG,))}\n</{_TRANSCRIPT_TAG}>\n"
        f"{SUMMARIZE_REQUEST}"
    )
    return [ChatMessage("system", SUMMARIZER_PROMPT), ChatMessage("user", request)]


def _turns(session: Session, thread: Thread) -> list[_Turn]:
    own = _own_turns(session, thread)
    if thread.kind == "main":
        return own

    parent, anchor = thread.parent_thread, thread.anchor_message
    if parent is None or anchor is None:
        raise ValueError(f"side thread {thread.id} has no parent or anchor")
    prefix = _turns(session, parent)
    anchor_index = next((i for i, t in enumerate(prefix) if t.message_id == anchor.id), None)
    if anchor_index is None:
        raise ValueError(f"anchor message {anchor.id} is not in thread {parent.id}'s context")

    if thread.context_mode == "as_of_anchor":
        prefix = prefix[: anchor_index + 1]
        if thread.anchor_version is not None:
            content = _version_content(session, anchor.id, thread.anchor_version)
            if content is not None:
                prefix[anchor_index] = _Turn(anchor.id, ChatMessage("assistant", content))
    # Sibling threads and this thread's later turns all repeat the prefix exactly.
    last = prefix[-1]
    prefix[-1] = _Turn(last.message_id, replace(last.message, cache_breakpoint=True))

    focus = _focus_block(thread, anchor.id)
    if own and own[0].message.role == "user":
        first = own[0]
        own[0] = _Turn(first.message_id, ChatMessage("user", f"{focus}\n{first.message.content}"))
    else:
        own.insert(0, _Turn(None, ChatMessage("user", focus)))
    return prefix + own


def _own_turns(session: Session, thread: Thread) -> list[_Turn]:
    messages = list(
        session.scalars(
            select(Message).where(Message.thread_id == thread.id).order_by(Message.position)
        )
    )
    notes = _notes_by_message(session, thread.id, messages)
    turns = []
    for m in messages:
        # A failed generation with no text adds nothing; a partial reply is kept,
        # since the user saw it and may refer to it.
        if m.role == "assistant" and m.error and not m.content:
            continue
        if m.role == "user":
            content = m.content
            if m.id in notes:
                content = f"{_notes_block(notes[m.id])}\n{content}"
            turns.append(_Turn(m.id, ChatMessage("user", content)))
        else:
            turns.append(_Turn(m.id, ChatMessage("assistant", m.content)))
    return turns


def _notes_by_message(
    session: Session, thread_id: int, messages: list[Message]
) -> dict[int, list[MergeNote]]:
    """Map each user message id to the approved notes injected before it."""
    notes = session.scalars(
        select(MergeNote)
        .where(MergeNote.target_thread_id == thread_id, MergeNote.approved)
        .order_by(MergeNote.id)
    )
    positions = {m.id: m.position for m in messages}
    user_messages = [m for m in messages if m.role == "user"]
    result: dict[int, list[MergeNote]] = defaultdict(list)
    for note in notes:
        after = note.inject_after_message_id
        after_position = -1 if after is None else positions.get(after)
        if after_position is None:
            continue  # merged after a message that is not in this thread
        target = next((m for m in user_messages if m.position > after_position), None)
        if target is not None:
            result[target.id].append(note)
    return result


def _notes_block(notes: list[MergeNote]) -> str:
    lines = []
    for n in notes:
        text = "\n  ".join(line for line in n.note_text.strip().splitlines() if line.strip())
        lines.append(f"- [re {_note_label(n.thread)}] {_escape(text)}")
    body = "\n".join(lines)
    return f"<resolved_side_threads>\n{body}\n</resolved_side_threads>"


def _note_label(side: Thread) -> str:
    if side.anchor_section:
        return _escape(side.anchor_section)
    quote = " ".join((side.anchor_quote or "").split())
    if len(quote) > NOTE_QUOTE_CHARS:
        quote = quote[:NOTE_QUOTE_CHARS].rstrip() + "…"
    return f'"{_escape(quote)}"'


def _focus_block(thread: Thread, anchor_id: int) -> str:
    section = ""
    if thread.anchor_section:
        section = f' section="{_escape(thread.anchor_section).replace(chr(34), "")}"'
    return (
        f"<side_thread>\n{SIDE_THREAD_INSTRUCTION}\n</side_thread>\n"
        f'<focus message="msg_{anchor_id}"{section}>\n'
        f"{_escape(thread.anchor_quote or '')}\n"
        "</focus>"
    )


def _version_content(session: Session, message_id: int, version: int) -> str | None:
    return session.scalar(
        select(MessageVersion.content).where(
            MessageVersion.message_id == message_id, MessageVersion.version == version
        )
    )


_TAGS = ("side_thread", "focus", "resolved_side_threads")
_TRANSCRIPT_TAG = "side_thread_transcript"


def _escape(text: str, tags: tuple[str, ...] = _TAGS) -> str:
    """Stop stored text (quotes, notes) from closing the tags that wrap it."""
    for tag in tags:
        text = text.replace(f"</{tag}", f"<\\/{tag}")
    return text
