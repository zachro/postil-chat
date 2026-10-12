"""FastAPI app and routes. Run with: uvicorn --factory app.main:create_app"""

import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ModelConfig, Settings, get_settings
from app.context import assemble_context
from app.db import init_db, make_engine
from app.llm import LLMProvider, Usage, estimate_tokens, get_provider
from app.merge import merge_thread, summarize_thread
from app.models import Conversation, Message, MessageVersion, Thread
from app.schemas import (
    ContextMessage,
    ContextPreview,
    ConversationCreate,
    ConversationOut,
    ConversationSummary,
    MergeRequest,
    MessageOut,
    ModelInfo,
    ModelsResponse,
    NoteProposal,
    SendMessage,
    SideThreadCreate,
    ThreadOut,
    ThreadUpdate,
)

log = logging.getLogger("postil")

DEFAULT_TITLE = "New conversation"
MAX_SIDE_THREAD_DEPTH = 3  # main thread is depth 0


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    settings: Settings | None = None,
    provider_for: Callable[[ModelConfig], LLMProvider] = get_provider,
) -> FastAPI:
    settings = settings or get_settings()
    session_factory = init_db(make_engine(settings.database_url))

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def thread_model(thread: Thread) -> str:
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

    def require_open_side_thread(thread: Thread) -> None:
        if thread.kind != "side":
            raise HTTPException(422, "only side threads can be resolved or merged")
        if thread.status != "open":
            raise HTTPException(409, f"thread is already {thread.status}")

    def thread_depth(thread: Thread) -> int:
        depth = 0
        while thread.parent_thread is not None:
            thread = thread.parent_thread
            depth += 1
        return depth

    router = APIRouter(prefix="/api")

    @router.get("/models")
    def list_models() -> ModelsResponse:
        models = []
        for m in settings.models.values():
            missing = m.api_key_env if m.api_key_env and not os.environ.get(m.api_key_env) else None
            models.append(
                ModelInfo(id=m.id, label=m.label, available=missing is None, missing_env=missing)
            )
        return ModelsResponse(models=models, default_model=settings.default_model)

    @router.get("/conversations")
    def list_conversations(session: SessionDep) -> list[ConversationSummary]:
        rows = session.scalars(select(Conversation).order_by(Conversation.id.desc()))
        return [ConversationSummary.model_validate(c) for c in rows]

    @router.post("/conversations", status_code=201)
    def create_conversation(body: ConversationCreate, session: SessionDep) -> ConversationOut:
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

    @router.post("/messages/{message_id}/threads", status_code=201)
    def create_side_thread(
        message_id: int, body: SideThreadCreate, session: SessionDep
    ) -> ThreadOut:
        """Anchor a new side thread to a span of an assistant message.

        The first question is sent afterwards via POST /threads/{id}/messages,
        so it streams like any other turn.
        """
        message = session.get(Message, message_id)
        if message is None:
            raise HTTPException(404, "message not found")
        if message.role != "assistant":
            raise HTTPException(422, "side threads anchor to assistant messages")
        if not (body.start < body.end <= len(message.content)):
            raise HTTPException(422, "anchor offsets are out of range")
        if message.content[body.start : body.end] != body.quote:
            raise HTTPException(422, "quote does not match the message at those offsets")
        parent = message.thread
        if thread_depth(parent) >= MAX_SIDE_THREAD_DEPTH:
            raise HTTPException(422, f"side threads nest at most {MAX_SIDE_THREAD_DEPTH} deep")

        version = session.scalar(
            select(func.max(MessageVersion.version)).where(MessageVersion.message_id == message_id)
        )
        thread = Thread(
            conversation_id=parent.conversation_id,
            kind="side",
            parent_thread_id=parent.id,
            anchor_message_id=message_id,
            anchor_quote=body.quote,
            anchor_start=body.start,
            anchor_end=body.end,
            anchor_section=body.section,
            anchor_version=version or 1,
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
    async def summarize(thread_id: int, session: SessionDep) -> NoteProposal:
        """Propose a merge note for a side thread. Nothing is stored until /merge."""
        thread = get_thread(session, thread_id)
        require_open_side_thread(thread)
        model_id = settings.summarizer_model or thread_model(thread)
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
    def send_message(thread_id: int, body: SendMessage, session: SessionDep) -> StreamingResponse:
        """Save the user message, then stream the assistant reply as SSE.

        Events: `user_message` (saved message), `delta` ({text}), then either
        `done` ({message}) or `error` ({error, message}). The assistant message
        is saved even if generation fails, with `error` set and partial content.
        """
        thread = get_thread(session, thread_id)
        if thread.status != "open":
            raise HTTPException(409, f"thread is {thread.status}; reopen it to continue")
        model_id = body.model or thread_model(thread)
        if model_id not in settings.models:
            raise HTTPException(422, f"unknown model {model_id!r}")
        thread.model = model_id

        position = session.scalar(
            select(func.count()).select_from(Message).where(Message.thread_id == thread_id)
        )
        assert position is not None
        user_message = Message(
            thread_id=thread_id, position=position, role="user", content=body.content
        )
        session.add(user_message)
        if position == 0 and thread.kind == "main":
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
                    s.add(reply)
                    s.flush()
                    s.add(MessageVersion(message_id=reply.id, version=1, content=reply.content))
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

    @router.get("/threads/{thread_id}/context-preview")
    async def context_preview(thread_id: int, session: SessionDep) -> ContextPreview:
        """Exactly what the next model call for this thread would send, plus its size."""
        thread = get_thread(session, thread_id)
        try:
            context = assemble_context(session, thread_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        model_id = thread_model(thread)
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
