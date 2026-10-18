# CLAUDE.md: Postil (Inline-Threaded LLM Chat)

**Name:** Postil (an old word for a marginal note). Domain `postil.chat`. Use `postil-chat` for package names (bare `postil` is taken on npm).

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

- **Backend:** Python 3.12 (managed by `uv`), FastAPI, Uvicorn, SQLite via SQLAlchemy 2.x, Pydantic v2. Schema via `create_all`; no Alembic until the schema settles.
- **LLM layer:** a thin `LLMProvider` protocol in `app/llm/` with two adapters: `openai_compat.py` (OpenAI SDK; covers Ollama, OpenAI, OpenRouter, vLLM, LM Studio) and `anthropic.py` (Anthropic SDK; direct control of `cache_control`). No LiteLLM. Do not call vendor SDKs outside `app/llm/`.
- **Streaming:** Server-Sent Events from backend to frontend. Since the stream starts from a POST, `lib/sse.ts` parses a `fetch` response body (not `EventSource`). If a stream fails midway, persist the partial assistant message with an error flag.
- **Frontend:** React + TypeScript + Vite, npm. Plain CSS. Markdown via `react-markdown` (remark), because mdast nodes carry source positions, which `lib/anchors.ts` needs to map DOM selections back to raw-markdown offsets. Selection handling via the DOM `Selection`/`Range` APIs. Vite proxies `/api` to the backend (no CORS).
- **Dev model:** native Ollama at `http://localhost:11434` so development costs nothing; model name lives in config (a small current model; the dev machine is CPU-only). Anthropic/OpenAI keys come from env vars.
- **Dev commands:** top-level `Makefile` (`make dev`, `make test`, `make lint`).
- **Networking:** no auth in the MVP, so the server binds to `127.0.0.1` by default, including in Docker Compose.
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
  app/llm/openai_compat.py, app/llm/anthropic.py
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

Threads form a **tree**; each thread is a **linear** list of messages ordered by `position`. The "main thread" is the root thread; side threads hang off an anchor in a message. There is no branching inside a thread in the MVP (no regenerate/edit-and-resend forks); the only branching is via side threads.

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
  id, thread_id, position,       -- position: 0-based order within the thread
  role            -- 'user' | 'assistant'
  content, model, token_count,
  error           NULL            -- set if generation failed; content holds the partial reply
  created_at
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
3. Approved merge notes whose `target_thread_id` is this thread, injected as a short block prepended to the next user turn after `inject_after_message_id`. On merge, `inject_after_message_id` is set to the target thread's **last message at merge time**, so a note only ever attaches to a *new* user turn and never rewrites past turns (which would break the cached prefix):
   ```
   <resolved_side_threads>
   - [re §2] User runs Postgres 16; prefer approach B.
   </resolved_side_threads>
   ```
4. Side-thread messages are **never** included unless merged as a note.

**Side thread**
1. System prompt, byte-identical to the main thread's (for caching). The side-thread instruction ("answer narrowly about the focus span; the user may merge your conclusion back later") goes in the focus turn instead, as a `<side_thread>` block before `<focus>`.
2. The parent thread's context up to and including `anchor_message_id`. This is recursive: for a nested side thread, it is the parent side thread's own assembled context (grandparent prefix, focus marker, turns) truncated at the anchor, not just the parent's raw messages. Concretely: parent-thread messages from the start up to and including `anchor_message_id` (the version in `anchor_version` if `context_mode='as_of_anchor'`, latest otherwise). If `context_mode='as_of_now'`, also include later parent messages.
3. A focus marker as the first side-thread user turn:
   ```
   <focus message="msg_12" section="§2">
   exact quoted span here
   </focus>
   <user question text>
   ```
4. Side-thread turns.
5. Excluded: sibling side threads, parent messages after the anchor (unless `as_of_now`).

**Multi-anchor reply** (comments on several parts of one response, sent at once). This is a **main-thread** user turn, not a side thread. Like starting a review on GitHub: the user highlights step 2 and types a comment, highlights step 4 and types another, and the UI collects them in a pending tray; "Send" submits the whole batch as one user message encoded as:
```xml
<inline_replies to="msg_12">
  <reply anchor="§2">Why not use a migration here?</reply>
  <reply anchor="§4">Does this work with Ollama's API too?</reply>
</inline_replies>
```
Use section labels when present; otherwise fall back to `<reply quote="short exact span">`. The original message is already in context, so anchors are pointers, not copies. Optional free text the user adds to the batch goes after the block. Store the structured anchors alongside the message so the UI can render each comment against its span.

**Budgeting:** compute tokens per assembled context (each provider adapter exposes `count_tokens`; an estimate is fine for OpenAI-compatible/Ollama), expose it in the UI per thread, and warn when over a configurable budget. Do not silently truncate in the MVP; surface it.

**Prompt-cache friendliness:** keep the shared prefix (system + parent history through the anchor) byte-identical across sibling side threads and across turns. Put volatile content (focus marker, merge notes, new turns) at the end. For Anthropic, set `cache_control` breakpoints at the end of the stable prefix. Ollama/llama.cpp reuse the KV cache for identical prefixes automatically, so just keep prefixes stable.

---

## 6. System prompt requirements

- Ask the model to **label long, multi-part responses with section markers** (`§1`, `§2`, ...) at the start of each logical section/step, so anchors can be a couple of tokens. Short answers need no labels. Labels are an optimization only: small local models will emit them inconsistently, so nothing may depend on them. Quote-based anchors must always work alone.
- Keep it short, provider-neutral, and stored in one file (`app/prompts.py`) so it is easy to tune.
- The frontend should render `§n` markers as small clickable gutter labels (hide the raw text or style it subtly).

---

## 7. Merge-back flow (`app/merge.py`)

1. User clicks "Resolve" on a side thread.
2. Backend calls the **summarizer model** (config; defaults to the thread's own model) with the side thread and the focus span, asking for a 1-3 line note capturing decisions, constraints, and corrections, nothing else.
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
POST   /messages/{id}/threads                 -> create side thread {quote, start, end, section?, context_mode?}; the first question is then sent via POST /threads/{id}/messages
PATCH  /threads/{id}                          -> title, context_mode, status ('open' <-> 'resolved'; 'merged' only via /merge)
POST   /threads/{id}/summarize                -> returns proposed merge note {note_text, model}; stores nothing
POST   /threads/{id}/merge                    -> {note_text} approves + injects; returns the thread with merge_note
POST   /messages/{id}/revise                  -> proposes revision (later milestone)
GET    /threads/{id}/context-preview          -> assembled messages + token count (debug + UX)
GET    /models                                -> configured providers/models
```

`/context-preview` is important: it makes assembly inspectable and testable. Build it early.

---

## 10. UX spec

- **Layout:** main chat in the center; side threads open in a right-hand panel; anchored spans in the main message are highlighted with a small badge showing the reply count. Clicking a highlight opens its thread. Multiple open threads are tabs in the panel.
- **Creating a thread:** select text in an assistant message, a small "Reply inline" button appears next to the selection, and clicking opens the side panel with the focus span quoted at the top.
- **Multi-anchor reply (M6):** next to "Reply inline", an "Add comment" action adds the span + typed comment to a pending tray above the main composer; the tray lists pending comments (editable/removable) and sends them as one main-thread message (section 5).
- **Original response stays put:** adding side-thread messages never scrolls or reflows the main chat.
- **Per-thread controls:** model picker, context mode toggle (as-of-anchor / as-of-now), token count, Resolve / Merge.
- **Detached anchors:** shown in the panel with the stored quote and a "detached" badge.
- Keyboard: Esc closes the panel; Cmd/Ctrl+Enter sends.
- Desktop-first; do not spend time on mobile in the MVP.

---

## 11. Milestones

1. **M1, Skeleton:** FastAPI + SQLite + OpenAI-compatible adapter; linear chat against Ollama with SSE streaming; React UI renders the main thread. Create the `conversations`/`threads`/`messages` tables now (M1 chat is just the main thread) to avoid a migration.
2. **M2, Tree + context assembly:** remaining schema; `assemble_context` with unit tests for main/side/as_of modes; `/context-preview`.
3. **M3, Inline threads:** selection -> anchor -> side panel; highlights with badges; side-thread chat working end to end.
4. **M4, Merge-back:** summarize, edit/approve dialog, injection into main context; verify via context-preview that token count drops vs. pasting the full thread.
5. **M5, Providers + caching:** Anthropic adapter; OpenAI via the OpenAI-compatible adapter; Anthropic cache breakpoints; confirm cache hits across sibling threads.
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
- Type hints everywhere (Python and TS); `ruff` + `mypy` (backend), `oxlint` + `tsc --noEmit` (frontend). Run all with `make lint`.
- **Never commit to `main`** (branch protection requires PRs and linear history; no force pushes). Work on a branch per milestone step and open a PR.
- Small commits per milestone step; commit messages in imperative mood.
- Prefer boring, readable code over cleverness. No new dependency without a one-line justification in the PR/commit.
- Do not call vendor SDKs directly outside `app/llm/`.
- Secrets only via env vars; provide `.env.example`.
- When a design question is genuinely ambiguous, make the simplest choice, note it under "Decisions log" below, and continue.

## 14. Decisions log

_(Claude Code: append dated one-line entries here as you make non-obvious choices.)_

- 2026-10-10: Two provider adapters (OpenAI-compatible + Anthropic) instead of LiteLLM: smaller dependency surface, direct `cache_control`.
- 2026-10-10: Threads are linear (`position`, no `parent_id`); branching only via side threads.
- 2026-10-10: Nested side threads inherit the parent's *assembled* context up to the anchor.
- 2026-10-10: Merge notes attach after the target thread's last message at merge time, never into past turns.
- 2026-10-10: Multi-anchor replies are a main-thread message built from a GitHub-review-style pending tray.
- 2026-10-10: `react-markdown` for rendering (source positions for anchor mapping); fetch-based SSE parser.
- 2026-10-10: Summarizer defaults to the thread's model; side threads inherit the parent's model; Docker Compose after M5, bound to 127.0.0.1.
- 2026-10-10: `threads.model` column (config model id) added for the per-thread model picker; the model sent with a message is remembered on the thread.
- 2026-10-10: `oxlint` instead of ESLint (current Vite template default, much faster).
- 2026-10-10: Conversations are created on first send, not on "New chat", so empty conversations do not pile up.
- 2026-10-10: Thinking models: only `content` deltas are shown; `reasoning` is dropped. Dev config disables thinking (`reasoning_effort: none`) for CPU speed.
- 2026-10-10: Side-thread instruction lives in the focus turn, not the system prompt, so main and side threads share one cacheable prefix.
- 2026-10-10: Stored text wrapped in our tags (quotes, notes) has closing tags for those tags neutralized (`</focus` -> `<\/focus`); no other escaping, so quotes stay byte-exact.
- 2026-10-10: Merge-note label is `§n` if the side thread has a section, else the first 40 chars of its quote.
- 2026-10-10: Every assistant reply gets a `message_versions` v1 row on save; a missing version row falls back to `messages.content`.
- 2026-10-10: `threads.model` NULL means inherit from the parent chain (resolved at request time).
- 2026-10-10: Token counts for OpenAI-compatible providers are a ~4 chars/token estimate (no standard tokenize endpoint); the UI marks them with `~`.
- 2026-10-10: Side threads are saved on their first send (like conversations); the create endpoint takes no `first_question`, so every turn streams through one endpoint.
- 2026-10-10: Depth cap (3) is enforced by the API as well as the UI.
- 2026-10-10: Anchor quotes are raw-markdown slices, so a selection across formatting includes its syntax (e.g. `bold** text`). Rendered text not found verbatim in the source (HTML entities) is unmapped: not selectable as an anchor, not highlightable.
- 2026-10-10: Badge count = messages in the side thread. Esc closes the panel and discards unsent drafts.
- 2026-10-10: Token budget is per model (`context_window` in config.yaml, matching the server's real allocation, e.g. OLLAMA_CONTEXT_LENGTH); global `token_budget` is the fallback. Meter warns at 80%, red when over.
- 2026-10-11: The summarizer sees only the focus quote and the side thread's own transcript (with notes merged into it), not the parent history: smaller prompt, and the summary is about the side discussion. Built in `context.py` like every other prompt.
- 2026-10-11: Resolved and merged threads are closed to new messages (409). Resolved can be reopened; merged is final, since its note may already be part of the target thread's history.
- 2026-10-11: Multi-line notes are joined into one bullet (continuation lines indented) in `<resolved_side_threads>`.
- 2026-10-11: Summarizer prompt carries one off-topic example note: qwen3 1.7B otherwise copied the transcript's `User:`/`Assistant:` lines into the note.
- 2026-10-11: A merged note shows as a marker after its `inject_after` message in the target thread ("goes with your next message" until a user turn follows). Resolved/merged highlights stay, muted; they still open the thread.
- 2026-10-11: Cache breakpoints are a provider-neutral `ChatMessage.cache_breakpoint` flag set by `assemble_context` on the last turn of each inherited prefix. The Anthropic adapter puts `cache_control` on the deepest three, plus top-level automatic caching for the tail; other adapters ignore the flag (their caching is automatic).
- 2026-10-11: Providers fill a `Usage` record per reply (cache reads/writes included); it is returned in the `done` SSE event, shown in the context meter, and output tokens go in `messages.token_count`. No new columns.
- 2026-10-11: A model whose `api_key_env` is unset is listed as unavailable (disabled in the picker) instead of failing on send.
- 2026-10-11: Claude refusals and `max_tokens` stops are saved like failed generations (partial text plus `error`). The config enables server-side refusal fallbacks (`fallbacks: default`).
- 2026-10-17: A line-start `§n` (after heading/list/quote markers and an opening `**`) is a section label; mid-line `§` is not. A span's section is the last label at or before it, or none if it crosses a label. Clicking a gutter label selects its whole section.
- 2026-10-17: Multi-anchor comments live in a new `inline_replies` table (a new table needs no migration under `create_all`). `messages.content` keeps only the free text; `context.py` renders one `<inline_replies>` block per target message.
- 2026-10-17: Inline replies may only target assistant messages in the same thread. The `quote` fallback is the first 40 chars (whitespace collapsed, `…` if cut, `"` as `&quot;`).
- 2026-10-17: The comment tray is per chat pane, so it works in open side threads too, not only the main thread.
- 2026-10-18: Revision proposals are never stored. `POST /messages/{id}/revise` streams a rewrite; `POST /messages/{id}/versions` stores the accepted (possibly edited) text, with `base_version` to reject a stale proposal (409). Restoring an old version adds a new version.
- 2026-10-18: The revise prompt is the thread's context through the reply (the same cached prefix its side threads use), then one user turn with the notes and a `<revise>` instruction. It is built in `context.py`.
- 2026-10-18: A reply's pending notes are the approved notes of side threads anchored to it, merged after its current version was created. Only those are sent, so a revision never re-applies a note.
- 2026-10-18: Re-anchoring is computed at render time, not stored: the exact quote nearest the stored offsets, else a fuzzy match (at most 20% edits, quotes of 12-1000 chars), else detached. Stored anchors never change, and `as_of_anchor` side threads keep seeing the version they were anchored to.
- 2026-10-18: Only replies in open threads can be revised (409 otherwise), like sending.
- 2026-10-18: The revision diff is a hand-written line LCS (`lib/diff.ts`), not a new dependency.

## 15. Open questions for the owner

_(none currently)_
