"""ORM models. See CLAUDE.md section 4 for the data model.

M1 only uses the main thread, but the thread columns for side threads exist
already so M2 does not need a migration. message_versions and merge_notes are
new tables and arrive with the milestones that use them.
"""

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
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread",
        order_by="Message.position",
        foreign_keys="Message.thread_id",
    )


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
