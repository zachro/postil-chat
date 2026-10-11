"""Check that sibling side threads share a cached prefix, against a real model.

    uv run python -m app.cache_check [model-id]   # defaults to config's default_model

Builds a throwaway conversation (in memory) whose main reply is long enough to
be cacheable, then sends two sibling side threads anchored to it. The first
should write the shared prefix to cache and the second should read it.
Providers that cache automatically (Ollama, OpenAI) report reads only.
Costs three short requests on hosted models.
"""

import asyncio
import sys

from sqlalchemy.orm import Session

from app.config import get_settings
from app.context import assemble_context
from app.db import init_db, make_engine
from app.llm import Usage, get_provider
from app.models import Conversation, Message, Thread

# ~1,500 tokens: above every current Claude model's minimum cacheable prefix.
STEPS = 60
REPLY = "\n".join(
    f"{n}. Step {n}: check the configuration for service {n}, restart it, and confirm "
    f"that its health endpoint reports ready before moving on to the next service."
    for n in range(1, STEPS + 1)
)


def build(session: Session) -> tuple[int, int]:
    """A main exchange plus two side threads on different steps; returns their ids."""
    conversation = Conversation(title="cache check")
    main = Thread(kind="main")
    conversation.threads.append(main)
    session.add(conversation)
    session.flush()
    session.add(Message(thread_id=main.id, position=0, role="user", content="How do I roll out?"))
    reply = Message(thread_id=main.id, position=1, role="assistant", content=REPLY)
    session.add(reply)
    session.flush()

    ids = []
    for step, question in [(3, "Why restart service 3 first?"), (7, "What if 7 fails?")]:
        quote = f"Step {step}: check the configuration for service {step}"
        start = REPLY.index(quote)
        side = Thread(
            conversation_id=conversation.id,
            kind="side",
            parent_thread_id=main.id,
            anchor_message_id=reply.id,
            anchor_quote=quote,
            anchor_start=start,
            anchor_end=start + len(quote),
            anchor_version=1,
        )
        session.add(side)
        session.flush()
        session.add(Message(thread_id=side.id, position=0, role="user", content=question))
        ids.append(side.id)
    session.flush()
    return ids[0], ids[1]


async def main(model_id: str | None) -> None:
    settings = get_settings()
    model_id = model_id or settings.default_model
    provider = get_provider(settings.models[model_id])
    with init_db(make_engine("sqlite:///:memory:"))() as session:
        first, second = build(session)
        for label, thread_id in [("side thread A", first), ("side thread B", second)]:
            context = assemble_context(session, thread_id)
            usage = Usage()
            reply = "".join([d async for d in provider.stream_chat(context, usage)])
            prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
            print(
                f"{label}: {prompt} prompt tokens, {usage.cache_write_tokens} written to cache, "
                f"{usage.cache_read_tokens} read from cache ({len(reply)} chars of reply)"
            )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
