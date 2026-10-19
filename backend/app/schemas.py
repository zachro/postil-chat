"""Pydantic request/response models."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# SQLite drops tzinfo; stored times are UTC, so say so in the API.
UTCDatetime = Annotated[
    datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=UTC))
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModelInfo(BaseModel):
    id: str
    label: str
    available: bool  # False when its API key env var is unset
    missing_env: str | None = None


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    default_model: str


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationSummary(ORMModel):
    id: int
    title: str
    created_at: UTCDatetime


class InlineReplyOut(ORMModel):
    target_message_id: int
    quote: str
    start: int
    end: int
    section: str | None
    target_version: int
    comment: str


class MessageOut(ORMModel):
    id: int
    thread_id: int
    position: int
    role: str
    content: str
    model: str | None
    token_count: int | None  # output tokens of an assistant reply, if the provider reported them
    error: str | None
    created_at: UTCDatetime
    inline_replies: list[InlineReplyOut]  # a multi-anchor reply's comments, in order
    version: int  # the message_versions row `content` holds
    version_created_at: UTCDatetime


class VersionOut(ORMModel):
    version: int
    content: str
    reason: str | None
    created_at: UTCDatetime


class VersionCreate(BaseModel):
    """Accept a (possibly edited) revision, or restore an old version, as the new latest."""

    content: str = Field(min_length=1)
    reason: str | None = None
    base_version: int | None = None  # rejected (409) if the message has moved on since

    @model_validator(mode="after")
    def _not_blank(self) -> "VersionCreate":
        if not self.content.strip():
            raise ValueError("a version needs content")
        return self


class RevisionProposal(BaseModel):
    """The `done` event of POST /messages/{id}/revise. Nothing is stored until accepted."""

    content: str
    model: str
    reason: str


class MergeNoteOut(ORMModel):
    id: int
    thread_id: int
    target_thread_id: int
    note_text: str
    inject_after_message_id: int | None
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
    anchor_version: int | None
    context_mode: str
    status: str
    title: str | None
    model: str | None
    messages: list[MessageOut]
    merge_note: MergeNoteOut | None


class ConversationOut(ConversationSummary):
    threads: list[ThreadOut]


ContextMode = Literal["as_of_anchor", "as_of_now"]


class SideThreadCreate(BaseModel):
    """An anchor into the message's raw markdown: quote == content[start:end]."""

    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int
    section: str | None = Field(default=None, max_length=20)
    context_mode: ContextMode = "as_of_anchor"


class ThreadUpdate(BaseModel):
    title: str | None = None
    context_mode: ContextMode | None = None
    # 'merged' is set only by POST /threads/{id}/merge.
    status: Literal["open", "resolved"] | None = None


class NoteProposal(BaseModel):
    note_text: str
    model: str  # the summarizer model that wrote it


class MergeRequest(BaseModel):
    note_text: str = Field(min_length=1)


class InlineReplyIn(BaseModel):
    """A comment on a span of an assistant message: quote == content[start:end]."""

    target_message_id: int
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int
    section: str | None = Field(default=None, max_length=20)
    comment: str = Field(min_length=1)


class SendMessage(BaseModel):
    content: str = ""  # free text; may be empty when inline_replies carries the turn
    inline_replies: list[InlineReplyIn] = []  # a multi-anchor reply (CLAUDE.md section 5)
    model: str | None = None  # config model id; defaults to the thread's model

    @model_validator(mode="after")
    def _not_empty(self) -> "SendMessage":
        if not self.content.strip() and not self.inline_replies:
            raise ValueError("a message needs text or inline replies")
        if any(not r.comment.strip() for r in self.inline_replies):
            raise ValueError("inline reply comments must not be blank")
        return self


class ContextMessage(BaseModel):
    role: str
    content: str
    cache_breakpoint: bool = False  # end of a prefix shared with other threads


class ContextPreview(BaseModel):
    thread_id: int
    model: str
    messages: list[ContextMessage]
    token_count: int
    token_count_estimated: bool
    token_budget: int
    over_budget: bool


class SearchHit(BaseModel):
    """A conversation title (message_id None) or message containing the query."""

    conversation_id: int
    conversation_title: str
    thread_id: int  # the main thread for a title hit
    thread_kind: str
    message_id: int | None
    role: str | None
    snippet: str  # one line around the first match, "…" where trimmed
    match_start: int  # the match's span within snippet
    match_end: int


class ModelSettingsOut(BaseModel):
    id: str
    label: str
    available: bool
    missing_env: str | None
    context_window: int | None  # in effect, override included
    config_context_window: int | None  # from config.yaml


class SettingsOut(BaseModel):
    default_model: str
    summarizer_model: str | None  # None: each thread's own model
    token_budget: int
    send_with_enter: bool
    models: list[ModelSettingsOut]
    overridden: list[str]  # settings saved over config.yaml, sorted


class SettingsUpdate(BaseModel):
    """Only the fields sent are changed. A context window of None drops that override."""

    default_model: str | None = None
    summarizer_model: str | None = None  # null is a value: use the thread's model
    token_budget: int | None = Field(default=None, gt=0)
    send_with_enter: bool | None = None
    context_windows: dict[str, Annotated[int, Field(gt=0)] | None] | None = None

    @model_validator(mode="after")
    def _no_null_values(self) -> "SettingsUpdate":
        for key in self.model_fields_set - {"summarizer_model"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self
