"""FastAPI app and routes. Run with: uvicorn --factory app.main:create_app"""

import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ModelConfig, Settings, get_settings
from app.context import assemble_context, assemble_revision_context, pending_revision_notes
from app.db import init_db, make_engine
from app.export import export_filename, export_json, export_markdown
from app.llm import LLMProvider, Usage, estimate_tokens, get_provider
from app.merge import merge_thread, summarize_thread
from app.models import Conversation, InlineReply, Message, MessageVersion, Thread
from app.preferences import clear_settings, effective_settings, save_settings
from app.revise import accept_revision, clean_revision, revision_reason
from app.schemas import (
    ContextMessage,
    ContextPreview,
    ConversationCreate,
    ConversationOut,
    ConversationSummary,
    MergeRequest,
    MessageOut,
    ModelInfo,
    ModelSettingsOut,
    ModelsResponse,
    NoteProposal,
    RevisionProposal,
    SearchHit,
    SendMessage,
    SettingsOut,
    SettingsUpdate,
    SideThreadCreate,
    ThreadOut,
    ThreadUpdate,
    VersionCreate,
    VersionOut,
)
from app.search import search

log = logging.getLogger("postil")

DEFAULT_TITLE = "New conversation"
MAX_SIDE_THREAD_DEPTH = 3  # main thread is depth 0


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    settings: Settings | None = None,
    provider_for: Callable[[ModelConfig], LLMProvider] = get_provider,
) -> FastAPI:
    config = settings or get_settings()  # config.yaml; see SettingsDep for what applies
    session_factory = init_db(make_engine(config.database_url))

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def get_current_settings(session: SessionDep) -> Settings:
        return effective_settings(session, config)[0]

    # config.yaml plus what was saved in the settings dialog, read per request.
    SettingsDep = Annotated[Settings, Depends(get_current_settings)]

    def missing_env(model: ModelConfig) -> str | None:
        env = model.api_key_env
        return env if env and not os.environ.get(env) else None

    def settings_out(session: Session) -> SettingsOut:
        settings, overridden = effective_settings(session, config)
        return SettingsOut(
            default_model=settings.default_model,
            summarizer_model=settings.summarizer_model,
            token_budget=settings.token_budget,
            send_with_enter=settings.send_with_enter,
            models=[
                ModelSettingsOut(
                    id=m.id,
                    label=m.label,
                    available=missing_env(m) is None,
                    missing_env=missing_env(m),
                    context_window=m.context_window,
                    config_context_window=config.models[m.id].context_window,
                )
                for m in settings.models.values()
            ],
            overridden=overridden,
        )

    def thread_model(settings: Settings, thread: Thread) -> str:
        """The thread's model, inherited from its parent chain if unset."""
        current: Thread | None = thread
        while current is not None:
            if current.model in settings.models:
                return current.model
            current = current.parent_thread
        return settings.default_model

    def get_thread(session: Session, thread_id: int) -> Thread:
        thread = session.get(Thread, thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        return thread

    def get_revisable(session: Session, message_id: int) -> Message:
        message = session.get(Message, message_id)
        if message is None:
            raise HTTPException(404, "message not found")
        if message.role != "assistant":
            raise HTTPException(422, "only assistant messages have versions")
        if message.thread.status != "open":
            raise HTTPException(409, f"thread is {message.thread.status}; reopen it to revise")
        return message

    def require_open_side_thread(thread: Thread) -> None:
        if thread.kind != "side":
            raise HTTPException(422, "only side threads can be resolved or merged")
        if thread.status != "open":
            raise HTTPException(409, f"thread is already {thread.status}")

    def check_anchor(message: Message | None, quote: str, start: int, end: int) -> Message:
        """The assistant message an anchor points into, if the anchor matches it."""
        if message is None:
            raise HTTPException(404, "message not found")
        if message.role != "assistant":
            raise HTTPException(422, "anchors point into assistant messages")
        if not (start < end <= len(message.content)):
            raise HTTPException(422, "anchor offsets are out of range")
        if message.content[start:end] != quote:
            raise HTTPException(422, "quote does not match the message at those offsets")
        return message

    def latest_version(session: Session, message_id: int) -> int:
        version = session.scalar(
            select(func.max(MessageVersion.version)).where(MessageVersion.message_id == message_id)
        )
        return version or 1

    def thread_depth(thread: Thread) -> int:
        depth = 0
        while thread.parent_thread is not None:
            thread = thread.parent_thread
            depth += 1
        return depth

    router = APIRouter(prefix="/api")

    @router.get("/models")
    def list_models(settings: SettingsDep) -> ModelsResponse:
        models = [
            ModelInfo(
                id=m.id, label=m.label, available=missing_env(m) is None, missing_env=missing_env(m)
            )
            for m in settings.models.values()
        ]
        return ModelsResponse(models=models, default_model=settings.default_model)

    @router.get("/settings")
    def get_settings_route(session: SessionDep) -> SettingsOut:
        return settings_out(session)

    @router.patch("/settings")
    def update_settings(body: SettingsUpdate, session: SessionDep) -> SettingsOut:
        """Save settings over config.yaml; only the fields sent change."""
        try:
            save_settings(session, config, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        session.commit()
        return settings_out(session)

    @router.delete("/settings")
    def reset_settings(session: SessionDep) -> SettingsOut:
        """Drop every saved setting, back to config.yaml."""
        clear_settings(session)
        session.commit()
        return settings_out(session)

    @router.get("/conversations")
    def list_conversations(session: SessionDep) -> list[ConversationSummary]:
        rows = session.scalars(select(Conversation).order_by(Conversation.id.desc()))
        return [ConversationSummary.model_validate(c) for c in rows]

    @router.post("/conversations", status_code=201)
    def create_conversation(
        body: ConversationCreate, session: SessionDep, settings: SettingsDep
    ) -> ConversationOut:
        conversation = Conversation(title=body.title or DEFAULT_TITLE)
        conversation.threads.append(Thread(kind="main", model=settings.default_model))
        session.add(conversation)
        session.commit()
        return ConversationOut.model_validate(conversation)

    @router.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: int, session: SessionDep) -> ConversationOut:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(404, "conversation not found")
        return ConversationOut.model_validate(conversation)

    @router.get("/search")
    def search_messages(q: str, session: SessionDep) -> list[SearchHit]:
        """Conversations whose title or messages contain `q` (case-insensitive substring)."""
        return search(session, q)

    @router.get("/conversations/{conversation_id}/export")
    def export_conversation(
        conversation_id: int,
        session: SessionDep,
        fmt: Annotated[Literal["md", "json"], Query(alias="format")] = "md",
    ) -> Response:
        """Download the conversation tree: Markdown to read, JSON with every version to keep."""
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(404, "conversation not found")
        disposition = {
            "Content-Disposition": f'attachment; filename="{export_filename(conversation, fmt)}"'
        }
        if fmt == "json":
            return JSONResponse(export_json(conversation), headers=disposition)
        return PlainTextResponse(
            export_markdown(conversation), media_type="text/markdown", headers=disposition
        )

    @router.post("/messages/{message_id}/threads", status_code=201)
    def create_side_thread(
        message_id: int, body: SideThreadCreate, session: SessionDep
    ) -> ThreadOut:
        """Anchor a new side thread to a span of an assistant message.

        The first question is sent afterwards via POST /threads/{id}/messages,
        so it streams like any other turn.
        """
        message = check_anchor(session.get(Message, message_id), body.quote, body.start, body.end)
        parent = message.thread
        if thread_depth(parent) >= MAX_SIDE_THREAD_DEPTH:
            raise HTTPException(422, f"side threads nest at most {MAX_SIDE_THREAD_DEPTH} deep")

        thread = Thread(
            conversation_id=parent.conversation_id,
            kind="side",
            parent_thread_id=parent.id,
            anchor_message_id=message_id,
            anchor_quote=body.quote,
            anchor_start=body.start,
            anchor_end=body.end,
            anchor_section=body.section,
            anchor_version=latest_version(session, message_id),
            context_mode=body.context_mode,
        )
        session.add(thread)
        session.commit()
        return ThreadOut.model_validate(thread)

    @router.patch("/threads/{thread_id}")
    def update_thread(thread_id: int, body: ThreadUpdate, session: SessionDep) -> ThreadOut:
        thread = get_thread(session, thread_id)
        if body.context_mode is not None:
            if thread.kind == "main":
                raise HTTPException(422, "the main thread has no context mode")
            thread.context_mode = body.context_mode
        if body.title is not None:
            thread.title = body.title
        if body.status is not None and body.status != thread.status:
            if thread.kind == "main":
                raise HTTPException(422, "the main thread cannot be resolved")
            if thread.status == "merged":
                raise HTTPException(409, "a merged thread cannot be reopened")
            thread.status = body.status  # open <-> resolved (resolve without merging)
        session.commit()
        return ThreadOut.model_validate(thread)

    @router.post("/threads/{thread_id}/summarize")
    async def summarize(thread_id: int, session: SessionDep, settings: SettingsDep) -> NoteProposal:
        """Propose a merge note for a side thread. Nothing is stored until /merge."""
        thread = get_thread(session, thread_id)
        require_open_side_thread(thread)
        model_id = settings.summarizer_model or thread_model(settings, thread)
        try:
            note = await summarize_thread(
                session, thread_id, provider_for(settings.models[model_id])
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"summarizer failed: {exc or type(exc).__name__}") from exc
        return NoteProposal(note_text=note, model=model_id)

    @router.post("/threads/{thread_id}/merge")
    def merge(thread_id: int, body: MergeRequest, session: SessionDep) -> ThreadOut:
        """Approve a (possibly edited) note: inject it into the parent thread's context."""
        thread = get_thread(session, thread_id)
        require_open_side_thread(thread)
        if not body.note_text.strip():
            raise HTTPException(422, "the note is empty")
        merge_thread(session, thread, body.note_text)
        session.commit()
        return ThreadOut.model_validate(thread)

    @router.post("/threads/{thread_id}/messages")
    def send_message(
        thread_id: int, body: SendMessage, session: SessionDep, settings: SettingsDep
    ) -> StreamingResponse:
        """Save the user message, then stream the assistant reply as SSE.

        Events: `user_message` (saved message), `delta` ({text}), then either
        `done` ({message}) or `error` ({error, message}). The assistant message
        is saved even if generation fails, with `error` set and partial content.
        """
        thread = get_thread(session, thread_id)
        if thread.status != "open":
            raise HTTPException(409, f"thread is {thread.status}; reopen it to continue")
        model_id = body.model or thread_model(settings, thread)
        if model_id not in settings.models:
            raise HTTPException(422, f"unknown model {model_id!r}")
        replies = []
        for i, r in enumerate(body.inline_replies):
            target = session.get(Message, r.target_message_id)
            if target is None or target.thread_id != thread_id:
                raise HTTPException(422, "inline replies point into this thread's own messages")
            check_anchor(target, r.quote, r.start, r.end)
            replies.append(
                InlineReply(
                    position=i,
                    target_message_id=target.id,
                    quote=r.quote,
                    start=r.start,
                    end=r.end,
                    section=r.section,
                    target_version=latest_version(session, target.id),
                    comment=r.comment,
                )
            )
        thread.model = model_id

        position = session.scalar(
            select(func.count()).select_from(Message).where(Message.thread_id == thread_id)
        )
        assert position is not None
        user_message = Message(
            thread_id=thread_id,
            position=position,
            role="user",
            content=body.content,
            inline_replies=replies,
        )
        session.add(user_message)
        if position == 0 and thread.kind == "main" and body.content.strip():
            conversation = thread.conversation
            if conversation.title == DEFAULT_TITLE:
                conversation.title = body.content.strip().splitlines()[0][:80]
        session.commit()

        user_out = MessageOut.model_validate(user_message).model_dump(mode="json")
        context = assemble_context(session, thread_id)

        async def events() -> AsyncIterator[str]:
            yield sse("user_message", user_out)
            parts: list[str] = []
            usage = Usage()
            error: str | None = "cancelled"  # overwritten unless the client disconnects
            try:
                provider = provider_for(settings.models[model_id])
                async for delta in provider.stream_chat(context, usage):
                    parts.append(delta)
                    yield sse("delta", {"text": delta})
                error = None
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            finally:
                with session_factory() as s:
                    reply = Message(
                        thread_id=thread_id,
                        position=position + 1,
                        role="assistant",
                        content="".join(parts),
                        model=model_id,
                        token_count=usage.output_tokens or None,
                        error=error,
                    )
                    reply.versions.append(MessageVersion(version=1, content=reply.content))
                    s.add(reply)
                    s.commit()
                    reply_out = MessageOut.model_validate(reply).model_dump(mode="json")
            usage_out = asdict(usage)
            log.info("thread %s reply via %s: %s", thread_id, model_id, usage_out)
            if error is None:
                yield sse("done", {"message": reply_out, "usage": usage_out})
            else:
                yield sse("error", {"error": error, "message": reply_out, "usage": usage_out})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/messages/{message_id}/revise")
    def revise(message_id: int, session: SessionDep, settings: SettingsDep) -> StreamingResponse:
        """Stream a rewrite of the reply that applies its merged side threads' notes.

        Events: `delta` ({text}), then `done` (RevisionProposal) or `error` ({error}).
        Nothing is stored; accept with POST /messages/{id}/versions.
        """
        message = get_revisable(session, message_id)
        try:
            context = assemble_revision_context(session, message_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        reason = revision_reason(pending_revision_notes(session, message))
        model_id = thread_model(settings, message.thread)

        async def events() -> AsyncIterator[str]:
            parts: list[str] = []
            try:
                provider = provider_for(settings.models[model_id])
                async for delta in provider.stream_chat(context):
                    parts.append(delta)
                    yield sse("delta", {"text": delta})
            except Exception as exc:
                yield sse("error", {"error": str(exc) or type(exc).__name__})
                return
            proposal = RevisionProposal(
                content=clean_revision("".join(parts)), model=model_id, reason=reason
            )
            yield sse("done", proposal.model_dump())

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/messages/{message_id}/versions")
    def create_version(message_id: int, body: VersionCreate, session: SessionDep) -> MessageOut:
        """Accept a revision (or restore an old version) as the message's new content."""
        message = get_revisable(session, message_id)
        if body.base_version is not None and body.base_version != message.version:
            raise HTTPException(409, f"the message is at version {message.version} now")
        accept_revision(session, message, body.content, body.reason)
        session.commit()
        return MessageOut.model_validate(message)

    @router.get("/messages/{message_id}/versions")
    def list_versions(message_id: int, session: SessionDep) -> list[VersionOut]:
        message = session.get(Message, message_id)
        if message is None:
            raise HTTPException(404, "message not found")
        return [VersionOut.model_validate(v) for v in message.versions]

    @router.get("/threads/{thread_id}/context-preview")
    async def context_preview(
        thread_id: int, session: SessionDep, settings: SettingsDep
    ) -> ContextPreview:
        """Exactly what the next model call for this thread would send, plus its size."""
        thread = get_thread(session, thread_id)
        try:
            context = assemble_context(session, thread_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        model_id = thread_model(settings, thread)
        try:
            tokens = await provider_for(settings.models[model_id]).count_tokens(context)
        except Exception:
            tokens = estimate_tokens(context)  # e.g. missing API key; still show a size
        return ContextPreview(
            thread_id=thread_id,
            model=model_id,
            messages=[
                ContextMessage(role=m.role, content=m.content, cache_breakpoint=m.cache_breakpoint)
                for m in context
            ],
            token_count=tokens.count,
            token_count_estimated=tokens.estimated,
            token_budget=settings.budget_for(model_id),
            over_budget=tokens.count > settings.budget_for(model_id),
        )

    app = FastAPI(title="Postil")
    app.include_router(router)
    return app
