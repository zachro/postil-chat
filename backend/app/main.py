"""FastAPI app and routes. Run with: uvicorn --factory app.main:create_app"""

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import ModelConfig, Settings, get_settings
from app.context import assemble_context
from app.db import init_db, make_engine
from app.llm import LLMProvider, estimate_tokens, get_provider
from app.models import Conversation, Message, MessageVersion, Thread
from app.schemas import (
    ContextMessage,
    ContextPreview,
    ConversationCreate,
    ConversationOut,
    ConversationSummary,
    MessageOut,
    ModelInfo,
    ModelsResponse,
    SendMessage,
)

DEFAULT_TITLE = "New conversation"


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

    router = APIRouter(prefix="/api")

    @router.get("/models")
    def list_models() -> ModelsResponse:
        return ModelsResponse(
            models=[ModelInfo(id=m.id, label=m.label) for m in settings.models.values()],
            default_model=settings.default_model,
        )

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

    @router.post("/threads/{thread_id}/messages")
    def send_message(thread_id: int, body: SendMessage, session: SessionDep) -> StreamingResponse:
        """Save the user message, then stream the assistant reply as SSE.

        Events: `user_message` (saved message), `delta` ({text}), then either
        `done` ({message}) or `error` ({error, message}). The assistant message
        is saved even if generation fails, with `error` set and partial content.
        """
        thread = get_thread(session, thread_id)
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
            error: str | None = "cancelled"  # overwritten unless the client disconnects
            try:
                provider = provider_for(settings.models[model_id])
                async for delta in provider.stream_chat(context):
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
                        error=error,
                    )
                    s.add(reply)
                    s.flush()
                    s.add(MessageVersion(message_id=reply.id, version=1, content=reply.content))
                    s.commit()
                    reply_out = MessageOut.model_validate(reply).model_dump(mode="json")
            if error is None:
                yield sse("done", {"message": reply_out})
            else:
                yield sse("error", {"error": error, "message": reply_out})

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
            messages=[ContextMessage(role=m.role, content=m.content) for m in context],
            token_count=tokens.count,
            token_count_estimated=tokens.estimated,
            token_budget=settings.token_budget,
            over_budget=tokens.count > settings.token_budget,
        )

    app = FastAPI(title="Postil")
    app.include_router(router)
    return app
