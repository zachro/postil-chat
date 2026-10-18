"""Revise-in-place: rewrite a reply with its side threads' notes (CLAUDE.md section 8).

The model only proposes a rewrite (prompt built by context.py); the user reviews
the diff and accepts it, and only then accept_revision() stores a new version.
Anchors are not rewritten: the frontend re-locates them in the new content by
quote, and a quote it cannot find shows as detached.
"""

import re

from sqlalchemy.orm import Session

from app.context import note_label
from app.models import MergeNote, Message, MessageVersion

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_WRAPPING_FENCE = re.compile(r"\A```(?:markdown|md)?\n(.*)\n```\Z", re.DOTALL)


def clean_revision(text: str) -> str:
    """Drop reasoning blocks and a code fence wrapped around the whole reply."""
    text = _THINK.sub("", text).strip()
    wrapped = _WRAPPING_FENCE.match(text)
    if wrapped and "```" not in wrapped.group(1):
        text = wrapped.group(1).strip()
    return text


def revision_reason(notes: list[MergeNote]) -> str:
    return "Applied " + ", ".join(note_label(n.thread) for n in notes)


def accept_revision(
    session: Session, message: Message, content: str, reason: str | None
) -> MessageVersion:
    """Store `content` as the message's new latest version. Earlier versions are kept."""
    version = MessageVersion(version=message.version + 1, content=content, reason=reason)
    message.versions.append(version)
    message.content = content
    session.flush()
    return version
