# CLAUDE.md: Postil (Inline-Threaded LLM Chat)

**Name:** Postil (an old word for a marginal note). Canonical domain: `postil.chat`; `trypostil.com` redirects to it. Use `postil-chat` for the repo and for package names where the bare `postil` is taken. The bare `postil` name is **taken on npm and GitHub** (free on PyPI), so do not assume it is available: use `@postil/web` or `postil-chat` on npm, and `postil-chat` for the GitHub repo or org.

A model-agnostic, self-hostable chat UI where you can reply **inline** to a specific part of an AI response (like margin comments in Google Docs) without pushing the original response out of view. Side threads can be **merged back** into the main conversation as a tiny note, so the main thread's context stays small.

Works with hosted models (Claude, OpenAI) and local models (Ollama) through one interface.

---

## 1. Problem and goals

**Problem:** After a long or multi-step AI response, follow-up questions about early parts push the original response far up the scroll. Users scroll back or re-ask for the response. Existing "branch in new chat" features copy the whole history into a separate chat, and the conclusions never come back.

**Goals**
1. Anchor a side thread to a highlighted span of any assistant message and show it in a side panel/margin, so the original response stays visible.
2. Side threads see only the context they need (the main path up to the anchored message plus the focus span), not the rest of the main conversation.
3. Resolved side threads can be compressed into a short note that is injected into main-thread context. This is the anti-bloat mechanism.
4. Optionally, the model can revise the original response in place using the side-thread conclusions (versioned, never destructive).
5. Provider-agnostic: Claude, OpenAI, Ollama, and anything LiteLLM supports.

**Non-goals (MVP):** multi-user auth, sharing/collab, mobile-optimized UI, RAG/file uploads, tool use/MCP. Possible later; do not design them out.

---

## 2. Key insight (read this first)

LLM APIs are **stateless**. The context window is rebuilt from scratch on every call. So the **UI structure (a tree of threads)** and **what the model sees (a per-call assembled prompt)** are fully decoupled. All the interesting logic lives in one function, `assemble_context(thread)` (section 5). Keep that function pure, well-tested, and the single place where prompts are built.

---

## 3. Stack (defaults; deviate only with reason)

- **Backend:** Python 3.12, FastAPI, Uvicorn, SQLite via SQLAlchemy 2.x (or plain `sqlite3` if simpler), Pydantic v2.
- **LLM layer:** LiteLLM behind a thin `LLMProvider` interface in `backend/llm/`. Do not call vendor SDKs from route handlers.
- **Streaming:** Server-Sent Events from backend to frontend.
- **Frontend:** React + TypeScript + Vite. Plain CSS or Tailwind. Selection handling via the DOM `Selection`/`Range` APIs.
- **Dev model:** Ollama (e.g. `llama3.1` or `qwen2.5`) at `http://localhost:11434` so development costs nothing. Anthropic/OpenAI keys come from env vars.
- **Tests:** pytest for backend (heavy coverage on context assembly), Vitest for frontend utilities (anchor/offset logic).
- **Config:** `.env` plus `config.yaml` (default model, summarizer model, provider keys, token budgets). Never commit secrets.

Suggested layout:

```
backend/
  app/main.py            # FastAPI app, routes
  app/db.py              # engine, session, migrations
  app/models.py          # ORM models
  app/schemas.py         # Pydantic request/response models
  app/context.py         # assemble_context(), the heart of the project
  app/merge.py           # summarize_thread(), merge-note flow
  app/revise.py          # revise-in-place flow
  app/llm/base.py        # LLMProvider protocol
  app/llm/litellm_provider.py
  tests/
frontend/
  src/
    components/ (ChatPane, MessageView, MarginThread, ThreadPanel, MergeNoteDialog)
    lib/anchors.ts       # selection -> anchor, anchor -> highlight
    lib/api.ts, lib/sse.ts
CLAUDE.md
```

---

## 4. Data model

Messages form a **tree**. A **thread** is a path of messages. The "main thread" is the root thread; side threads hang off an anchor in a message.

```
conversations(id, title, created_at)

threads(
  id, conversation_id,
  kind            -- 'main' | 'side'
  parent_thread_id NULL for main
  anchor_message_id NULL for main   -- the assistant message being discussed
  anchor_quote    -- exact highlighted text (source of truth)
  anchor_start, anchor_end          -- char offsets in the message content (version-specific, see below)
  anchor_section  NULL              -- e.g. '§2' if the span falls in a labelled section
  anchor_version  -- message version the anchor was made against
  context_mode    -- 'as_of_anchor' | 'as_of_now'  (default 'as_of_anchor')
  status          -- 'open' | 'resolved' | 'merged'
  title, created_at
)

messages(
  id, thread_id, parent_id,
  role            -- 'user' | 'assistant'
  content, model, token_count, created_at
)

message_versions(          -- for revise-in-place; v1 mirrors original content
  id, message_id, version, content, reason, created_at
)

merge_notes(
  id, thread_id,           -- the side thread being merged
  target_thread_id,        -- usually the parent thread
  note_text,               -- 1-3 lines, user-editable
  approved BOOLEAN,
  inject_after_message_id, -- where it takes effect in the target thread's context
  created_at
)
```

Rules:
- **Anchor by quote first, offsets second.** Offsets break when content is revised; the quote is used to re-locate the span (fuzzy match fallback). If re-location fails, show the anchor as "detached" with the stored quote, never silently drop it.
- Never mutate message content in place. Revisions go in `message_versions`; `messages.content` always equals the latest approved version.
- Side threads may have their own side threads (`parent_thread_id` chains). Cap depth at 3 in the UI for the MVP.

---

## 5. Context assembly (`app/context.py`)

`assemble_context(thread_id) -> list[Message]` builds exactly what is sent to the model. Pure function of DB state, no side effects.

**Main thread**
1. System prompt (section 6).
2. Main-path messages in order.
3. Approved merge notes whose `target_thread_id` is this thread, injected as a short block prepended to the next user turn after `inject_after_message_id`:
   ```
   <resolved_side_threads>
   - [re §2] User runs Postgres 16; prefer approach B.
   </resolved_side_threads>
   ```
4. Side-thread messages are **never** included unless merged as a note.

**Side thread**
1. System prompt (same base plus a short side-thread addendum: "answer narrowly about the focus span; the user may merge your conclusion back later").
2. Parent-thread messages from the start up to and including `anchor_message_id` (the version in `anchor_version` if `context_mode='as_of_anchor'`, latest otherwise). If `context_mode='as_of_now'`, also include later parent messages.
3. A focus marker as the first side-thread user turn:
   ```
   <focus message="msg_12" section="§2">
   exact quoted span here
   </focus>
   <user question text>
   ```
4. Side-thread turns.
5. Excluded: sibling side threads, parent messages after the anchor (unless `as_of_now`).

**Inline replies to several parts at once** (multi-anchor reply in one send):
```xml
<inline_replies to="msg_12">
  <reply anchor="§2">Why not use a migration here?</reply>
  <reply anchor="§4">Does this work with Ollama's API too?</reply>
</inline_replies>
```
Use section labels when present; otherwise fall back to `<reply quote="short exact span">`. The original message is already in context, so anchors are pointers, not copies.

**Budgeting:** compute tokens per assembled context (LiteLLM `token_counter`), expose it in the UI per thread, and warn when over a configurable budget. Do not silently truncate in the MVP; surface it.

**Prompt-cache friendliness:** keep the shared prefix (system + parent history through the anchor) byte-identical across sibling side threads and across turns. Put volatile content (focus marker, merge notes, new turns) at the end. For Anthropic, set `cache_control` breakpoints at the end of the stable prefix. Ollama/llama.cpp reuse the KV cache for identical prefixes automatically, so just keep prefixes stable.

---

## 6. System prompt requirements

- Ask the model to **label long, multi-part responses with section markers** (`§1`, `§2`, ...) at the start of each logical section/step, so anchors can be a couple of tokens. Short answers need no labels.
- Keep it short, provider-neutral, and stored in one file (`app/prompts.py`) so it is easy to tune.
- The frontend should render `§n` markers as small clickable gutter labels (hide the raw text or style it subtly).

---

## 7. Merge-back flow (`app/merge.py`)

1. User clicks "Resolve" on a side thread.
2. Backend calls the **summarizer model** (config; default a cheap/small model) with the side thread and the focus span, asking for a 1-3 line note capturing decisions, constraints, and corrections, nothing else.
3. UI shows the note in an editable dialog. **The user must approve or edit before it is injected.** Summaries can drop the one detail that mattered.
4. On approval, store `merge_notes` row, set thread `status='merged'`, and the note appears in main-thread context per section 5.
5. "Resolve without merging" is allowed (`status='resolved'`, nothing injected).

## 8. Revise-in-place (`app/revise.py`), after MVP core works

- Action on an assistant message: "Update this response with resolved threads."
- Call the main model with the original message plus approved notes; ask for a full rewrite preserving structure and `§` labels.
- Store as a new `message_versions` row, show a diff, and require user acceptance. Offer version history.
- Re-anchor existing threads against the new version via quote matching; mark failures as detached.

---

## 9. API sketch

```
POST   /conversations                         -> create
GET    /conversations/{id}                    -> tree: threads + messages + anchors
POST   /threads/{id}/messages                 -> send user message, streams assistant reply (SSE)
POST   /messages/{id}/threads                 -> create side thread {quote, start, end, section?, first_question}
PATCH  /threads/{id}                          -> title, context_mode, status
POST   /threads/{id}/summarize                -> returns proposed merge note
POST   /threads/{id}/merge                    -> {note_text} approves + injects
POST   /messages/{id}/revise                  -> proposes revision (later milestone)
GET    /threads/{id}/context-preview          -> assembled messages + token count (debug + UX)
GET    /models                                -> configured providers/models
```

`/context-preview` is important: it makes assembly inspectable and testable. Build it early.

---

## 10. UX spec

- **Layout:** main chat in the center; side threads open in a right-hand panel; anchored spans in the main message are highlighted with a small badge showing the reply count. Clicking a highlight opens its thread. Multiple open threads are tabs in the panel.
- **Creating a thread:** select text in an assistant message, a small "Reply inline" button appears next to the selection, and clicking opens the side panel with the focus span quoted at the top.
- **Original response stays put:** adding side-thread messages never scrolls or reflows the main chat.
- **Per-thread controls:** model picker, context mode toggle (as-of-anchor / as-of-now), token count, Resolve / Merge.
- **Detached anchors:** shown in the panel with the stored quote and a "detached" badge.
- Keyboard: Esc closes the panel; Cmd/Ctrl+Enter sends.
- Desktop-first; do not spend time on mobile in the MVP.

---

## 11. Milestones

1. **M1, Skeleton:** FastAPI + SQLite + LiteLLM; linear chat against Ollama with SSE streaming; React UI renders the main thread.
2. **M2, Tree + context assembly:** schema above; `assemble_context` with unit tests for main/side/as_of modes; `/context-preview`.
3. **M3, Inline threads:** selection -> anchor -> side panel; highlights with badges; side-thread chat working end to end.
4. **M4, Merge-back:** summarize, edit/approve dialog, injection into main context; verify via context-preview that token count drops vs. pasting the full thread.
5. **M5, Providers + caching:** Claude and OpenAI via LiteLLM; Anthropic cache breakpoints; confirm cache hits across sibling threads.
6. **M6, Section labels + multi-anchor replies:** system prompt, gutter labels, `<inline_replies>` encoding.
7. **M7, Revise-in-place + versions + re-anchoring.**
8. **M8, Polish:** export conversation tree to Markdown/JSON, search, settings UI.

Work one milestone at a time; each should leave the app runnable.

---

## 12. Known pitfalls (design for these)

- **Staleness:** a side thread forked at message 3 does not know what happened at message 10. Hence `context_mode`.
- **Summary loss:** always human-approve merge notes.
- **Anchor drift:** quote-first anchoring with fuzzy fallback; detached state is first-class.
- **Selection edge cases:** selections spanning markdown elements/code blocks. Anchor against the raw markdown source text, and map from the rendered DOM selection back to source offsets in `lib/anchors.ts`. Unit-test this.
- **Prompt-injection through anchors/notes:** treat all stored text as data. Wrap it in XML tags and do not let it appear in the system prompt.
- **Provider quirks:** some local models handle XML-tag structure worse than Claude. Keep tags simple and test the main flows on Ollama.

---

## 13. Conventions for Claude Code

- Keep `app/context.py` pure and small; every behavior change there needs a test first.
- Type hints everywhere (Python and TS); `ruff` + `mypy` (backend), `eslint` + `tsc --noEmit` (frontend).
- Small commits per milestone step; commit messages in imperative mood.
- Prefer boring, readable code over cleverness. No new dependency without a one-line justification in the PR/commit.
- Do not call vendor SDKs directly outside `app/llm/`.
- Secrets only via env vars; provide `.env.example`.
- When a design question is genuinely ambiguous, make the simplest choice, note it under "Decisions log" below, and continue.

## 14. Decisions log

_(Claude Code: append dated one-line entries here as you make non-obvious choices.)_

## 15. Open questions for the owner

- Default summarizer model (cheap hosted vs. local small model).
- Whether side threads should inherit the parent thread's model by default (assumed: yes).
- Whether to ship as a Docker Compose app (assumed: yes, after M5).
