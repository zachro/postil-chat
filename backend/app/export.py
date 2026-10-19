"""Export a conversation tree as Markdown (to read) or JSON (everything, to keep).

Markdown follows the main thread in order. Each side thread is a blockquote
right after the message it is anchored to, so nested threads nest as quotes.
JSON is the conversation as the API returns it, plus every message version.
"""

import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.models import Conversation, InlineReply, Message, Thread
from app.schemas import ConversationOut, VersionOut

EXPORT_FORMAT = "postil-chat/1"
QUOTE_LABEL_CHARS = 40
FILENAME_CHARS = 60

_Children = dict[int, list[Thread]]  # anchor message id -> side threads anchored there


def export_filename(conversation: Conversation, extension: str) -> str:
    """An ASCII slug of the title, e.g. "postgres-upgrade.md"."""
    ascii_title = unicodedata.normalize("NFKD", conversation.title).encode("ascii", "ignore")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.decode().lower()).strip("-")[:FILENAME_CHARS]
    return f"{slug.rstrip('-') or f'conversation-{conversation.id}'}.{extension}"


def export_json(conversation: Conversation) -> dict[str, Any]:
    data = ConversationOut.model_validate(conversation).model_dump(mode="json")
    versions = {
        m.id: [VersionOut.model_validate(v).model_dump(mode="json") for v in m.versions]
        for t in conversation.threads
        for m in t.messages
    }
    for thread in data["threads"]:
        for message in thread["messages"]:
            message["versions"] = versions[message["id"]]
    return {"format": EXPORT_FORMAT, "exported_at": datetime.now(UTC).isoformat(), **data}


def export_markdown(conversation: Conversation) -> str:
    children: _Children = defaultdict(list)
    for t in conversation.threads:
        if t.anchor_message_id is not None:
            children[t.anchor_message_id].append(t)
    main = next(t for t in conversation.threads if t.kind == "main")
    started = f"{conversation.created_at:%Y-%m-%d %H:%M} UTC"
    header = f"# {conversation.title}\n\nExported from Postil. Started {started}."
    body = _thread(main, children)
    return f"{header}\n\n---\n\n{body}\n" if body else f"{header}\n"


def _thread(thread: Thread, children: _Children) -> str:
    blocks = []
    if thread.kind == "side":
        blocks += [_side_header(thread), _quoted(thread.anchor_quote or "")]
    turns = [
        "\n\n".join([_message(m), *(_quoted(_thread(c, children)) for c in children[m.id])])
        for m in thread.messages
    ]
    blocks.append(("\n\n---\n\n" if thread.kind == "main" else "\n\n").join(turns))
    if thread.merge_note is not None:
        blocks.append(f"**Merge note:** {thread.merge_note.note_text}")
    return "\n\n".join(b for b in blocks if b)


def _side_header(thread: Thread) -> str:
    on = f" on {thread.anchor_section}" if thread.anchor_section else ""
    title = f"**Side thread{on}**"
    notes = [thread.status] if thread.status != "open" else []
    if thread.context_mode == "as_of_now":
        notes.append("sees later messages")
    return " · ".join([title, *notes])


def _message(message: Message) -> str:
    if message.role == "user":
        head = "**You**"
    else:
        version = f"v{message.version}" if message.version > 1 else None
        head = " · ".join(p for p in ["**Assistant**", message.model, version] if p)
    blocks = [head]
    if message.inline_replies:
        blocks.append("\n".join(_comment(r) for r in message.inline_replies))
    if message.content.strip():
        blocks.append(message.content.strip("\n"))
    if message.error:
        stopped = message.error == "cancelled"
        blocks.append("_Stopped._" if stopped else f"_Generation failed: {message.error}_")
    return "\n\n".join(blocks)


def _comment(reply: InlineReply) -> str:
    """A multi-anchor comment as a list item; continuation lines stay inside it."""
    comment = reply.comment.strip().replace("\n", "\n  ")
    return f"- re {_label(reply.section, reply.quote)}: {comment}"


def _label(section: str | None, quote: str) -> str:
    if section:
        return section
    text = " ".join(quote.split())
    cut = text[:QUOTE_LABEL_CHARS] + ("…" if len(text) > QUOTE_LABEL_CHARS else "")
    return f'"{cut}"'


def _quoted(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))
