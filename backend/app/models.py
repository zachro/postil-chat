"""ORM models. See CLAUDE.md section 4 for the data model."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    threads: Mapped[list["Thread"]] = relationship(
        back_populates="conversation", order_by="Thread.id"
    )


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    kind: Mapped[str] = mapped_column(String(10))  # 'main' | 'side'
    parent_thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"))
    anchor_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", use_alter=True))
    anchor_quote: Mapped[str | None] = mapped_column(Text)
    anchor_start: Mapped[int | None]
    anchor_end: Mapped[int | None]
    anchor_section: Mapped[str | None] = mapped_column(String(20))
    anchor_version: Mapped[int | None]
    context_mode: Mapped[str] = mapped_column(String(20), default="as_of_anchor")
    status: Mapped[str] = mapped_column(String(10), default="open")
    title: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(100))  # config model id
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="threads")
    parent_thread: Mapped["Thread | None"] = relationship(remote_side=[id])
    anchor_message: Mapped["Message | None"] = relationship(foreign_keys=[anchor_message_id])
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread",
        order_by="Message.position",
        foreign_keys="Message.thread_id",
    )
    merge_notes: Mapped[list["MergeNote"]] = relationship(
        back_populates="thread", foreign_keys="MergeNote.thread_id", order_by="MergeNote.id"
    )

    @property
    def merge_note(self) -> "MergeNote | None":
        """The approved note this thread was merged with, if any."""
        return next((n for n in self.merge_notes if n.approved), None)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("thread_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"))
    position: Mapped[int]  # 0-based order within the thread
    role: Mapped[str] = mapped_column(String(10))  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    token_count: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)  # set if generation failed
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    thread: Mapped[Thread] = relationship(back_populates="messages", foreign_keys=[thread_id])
    inline_replies: Mapped[list["InlineReply"]] = relationship(
        foreign_keys="InlineReply.message_id", order_by="InlineReply.position"
    )


class MessageVersion(Base):
    """Content history for revise-in-place. v1 mirrors the original assistant reply."""

    __tablename__ = "message_versions"
    __table_args__ = (UniqueConstraint("message_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    version: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class MergeNote(Base):
    """A resolved side thread compressed into a short note for its target thread."""

    __tablename__ = "merge_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"))  # the merged side thread
    target_thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"))
    note_text: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(default=False)
    # Target thread's last message at merge time (None if it was empty). The note
    # is prepended to the first user turn after it, never to an earlier turn.
    inject_after_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    thread: Mapped[Thread] = relationship(back_populates="merge_notes", foreign_keys=[thread_id])


class InlineReply(Base):
    """One comment of a multi-anchor reply: a user message's comment on a span of an
    assistant message (CLAUDE.md section 5). The user message's content holds only
    the free text the user added to the batch; context.py renders the comments."""

    __tablename__ = "inline_replies"
    __table_args__ = (UniqueConstraint("message_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))  # the user message
    position: Mapped[int]  # 0-based order within the batch
    target_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    quote: Mapped[str] = mapped_column(Text)
    start: Mapped[int]
    end: Mapped[int]
    section: Mapped[str | None] = mapped_column(String(20))
    target_version: Mapped[int]  # target message version the anchor was made against
    comment: Mapped[str] = mapped_column(Text)
