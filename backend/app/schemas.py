"""Pydantic request/response models."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# SQLite drops tzinfo; stored times are UTC, so say so in the API.
UTCDatetime = Annotated[
    datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=UTC))
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModelInfo(BaseModel):
    id: str
    label: str


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    default_model: str


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationSummary(ORMModel):
    id: int
    title: str
    created_at: UTCDatetime


class MessageOut(ORMModel):
    id: int
    thread_id: int
    position: int
    role: str
    content: str
    model: str | None
    error: str | None
    created_at: UTCDatetime


class ThreadOut(ORMModel):
    id: int
    kind: str
    parent_thread_id: int | None
    anchor_message_id: int | None
    anchor_quote: str | None
    anchor_start: int | None
    anchor_end: int | None
    anchor_section: str | None
    context_mode: str
    status: str
    title: str | None
    model: str | None
    messages: list[MessageOut]


class ConversationOut(ConversationSummary):
    threads: list[ThreadOut]


class SendMessage(BaseModel):
    content: str = Field(min_length=1)
    model: str | None = None  # config model id; defaults to the thread's model
