"""Plain substring search over conversation titles and current message content.

SQLite LIKE is enough at single-user scale (no FTS index to keep in sync). It is
case-insensitive for ASCII only; the query's % and _ are matched literally.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message, Thread
from app.schemas import SearchHit

SEARCH_LIMIT = 50
SNIPPET_BEFORE = 60  # characters of context kept around the match
SNIPPET_AFTER = 100


def search(session: Session, query: str, limit: int = SEARCH_LIMIT) -> list[SearchHit]:
    """Title hits first, then message hits, each newest first."""
    query = query.strip()
    if not query:
        return []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    hits = []
    conversations = session.scalars(
        select(Conversation)
        .where(Conversation.title.like(pattern, escape="\\"))
        .order_by(Conversation.id.desc())
        .limit(limit)
    )
    for c in conversations:
        main = next(t for t in c.threads if t.kind == "main")
        hits.append(_hit(c.title, query, c, main, None))
    messages = session.scalars(
        select(Message)
        .where(Message.content.like(pattern, escape="\\"))
        .order_by(Message.id.desc())
        .limit(limit - len(hits))
    )
    for m in messages:
        hits.append(_hit(m.content, query, m.thread.conversation, m.thread, m))
    return hits


def _hit(
    text: str, query: str, conversation: Conversation, thread: Thread, message: Message | None
) -> SearchHit:
    snippet, start, end = snippet_around(text, query)
    return SearchHit(
        conversation_id=conversation.id,
        conversation_title=conversation.title,
        thread_id=thread.id,
        thread_kind=thread.kind,
        message_id=message.id if message else None,
        role=message.role if message else None,
        snippet=snippet,
        match_start=start,
        match_end=end,
    )


def snippet_around(text: str, query: str) -> tuple[str, int, int]:
    """One line of `text` around the first case-insensitive `query`, and its span in it.

    Runs of whitespace (newlines, code indentation) collapse to one space.
    """
    found = text.lower().find(query.lower())
    start, end = (found, found + len(query)) if found >= 0 else (0, 0)
    a, b = max(0, start - SNIPPET_BEFORE), min(len(text), end + SNIPPET_AFTER)
    before = ("…" if a > 0 else "") + _collapse(text[a:start])
    match = _collapse(text[start:end])
    after = _collapse(text[end:b]) + ("…" if b < len(text) else "")
    return before + match + after, len(before), len(before) + len(match)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)
