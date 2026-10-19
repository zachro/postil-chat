// Typed client for the backend. Mirrors backend/app/schemas.py.

import { readSSE } from './sse'

export interface ModelInfo {
  id: string
  label: string
  available: boolean // false until its API key env var is set
  missing_env: string | null
}

export interface ModelsResponse {
  models: ModelInfo[]
  default_model: string
}

export interface ConversationSummary {
  id: number
  title: string
  created_at: string
}

export interface Message {
  id: number
  thread_id: number
  position: number
  role: 'user' | 'assistant'
  content: string
  model: string | null
  token_count: number | null // output tokens, if the provider reported them
  error: string | null
  created_at: string
  inline_replies: InlineReply[] // a multi-anchor reply's comments, in order
  version: number // the version `content` holds
  version_created_at: string
}

export interface MessageVersion {
  version: number
  content: string
  reason: string | null
  created_at: string
}

/** One comment of a multi-anchor reply, on a span of an assistant message in the same thread. */
export interface InlineReply {
  target_message_id: number
  quote: string
  start: number
  end: number
  section: string | null
  target_version: number
  comment: string
}

export type NewInlineReply = Omit<InlineReply, 'target_version'>

export interface MergeNote {
  id: number
  thread_id: number // the merged side thread
  target_thread_id: number
  note_text: string
  inject_after_message_id: number | null // joins the first user turn after this message
  created_at: string
}

export interface Thread {
  id: number
  kind: 'main' | 'side'
  parent_thread_id: number | null
  anchor_message_id: number | null
  anchor_quote: string | null
  anchor_start: number | null
  anchor_end: number | null
  anchor_section: string | null
  anchor_version: number | null
  context_mode: 'as_of_anchor' | 'as_of_now'
  status: 'open' | 'resolved' | 'merged'
  title: string | null
  model: string | null
  messages: Message[]
  merge_note: MergeNote | null
}

export interface Conversation extends ConversationSummary {
  threads: Thread[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
  return (await response.json()) as T
}

export const getModels = () => request<ModelsResponse>('/models')
export const listConversations = () => request<ConversationSummary[]>('/conversations')
export const getConversation = (id: number) => request<Conversation>(`/conversations/${id}`)
export const createConversation = () =>
  request<Conversation>('/conversations', { method: 'POST', body: '{}' })

/** Download link for a conversation tree: Markdown to read, JSON (with every version) to keep. */
export const exportUrl = (id: number, format: 'md' | 'json') => `/api/conversations/${id}/export?format=${format}`

export type ContextMode = Thread['context_mode']

export interface NewSideThread {
  quote: string
  start: number
  end: number
  section?: string | null
  context_mode?: ContextMode
}

export const createSideThread = (messageId: number, body: NewSideThread) =>
  request<Thread>(`/messages/${messageId}/threads`, { method: 'POST', body: JSON.stringify(body) })

export const updateThread = (
  threadId: number,
  body: { title?: string; context_mode?: ContextMode; status?: 'open' | 'resolved' },
) => request<Thread>(`/threads/${threadId}`, { method: 'PATCH', body: JSON.stringify(body) })

/** Ask the summarizer model for a proposed merge note. Nothing is stored. */
export const summarizeThread = (threadId: number) =>
  request<{ note_text: string; model: string }>(`/threads/${threadId}/summarize`, { method: 'POST' })

/** Approve a note: injects it into the parent thread's context and marks the thread merged. */
export const mergeThread = (threadId: number, noteText: string) =>
  request<Thread>(`/threads/${threadId}/merge`, {
    method: 'POST',
    body: JSON.stringify({ note_text: noteText }),
  })

/** Token usage of one reply. input_tokens excludes cached tokens. */
export interface Usage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface SendHandlers {
  onUserMessage(message: Message): void
  onDelta(text: string): void
  /** Called once with the saved assistant message; `error` is set if generation failed. */
  onFinish(message: Message, usage: Usage): void
}

export interface NewMessage {
  content: string // may be empty when inline_replies carries the turn
  model: string
  inline_replies?: NewInlineReply[]
}

/** Send a user message and stream the reply. Abort via `signal` to stop generation. */
export async function sendMessage(
  threadId: number,
  body: NewMessage,
  handlers: SendHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/threads/${threadId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`${response.status} ${await response.text()}`)
  }
  for await (const { event, data } of readSSE(response.body)) {
    const payload = JSON.parse(data)
    if (event === 'user_message') handlers.onUserMessage(payload as Message)
    else if (event === 'delta') handlers.onDelta((payload as { text: string }).text)
    else if (event === 'done' || event === 'error') {
      const { message, usage } = payload as { message: Message; usage: Usage }
      handlers.onFinish(message, usage)
    }
  }
}

export interface RevisionProposal {
  content: string
  model: string
  reason: string
}

/**
 * Stream a rewrite of an assistant reply that applies its merged side threads'
 * notes. Nothing is stored; accept it with createVersion.
 */
export async function reviseMessage(
  messageId: number,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<RevisionProposal> {
  const response = await fetch(`/api/messages/${messageId}/revise`, { method: 'POST', signal })
  if (!response.ok || !response.body) {
    throw new Error(`${response.status} ${await response.text()}`)
  }
  for await (const { event, data } of readSSE(response.body)) {
    const payload = JSON.parse(data)
    if (event === 'delta') onDelta((payload as { text: string }).text)
    else if (event === 'done') return payload as RevisionProposal
    else if (event === 'error') throw new Error((payload as { error: string }).error)
  }
  throw new Error('the revision stream ended early')
}

/** Store content as the message's new latest version (an accepted revision or a restore). */
export const createVersion = (
  messageId: number,
  body: { content: string; reason?: string | null; base_version?: number },
) => request<Message>(`/messages/${messageId}/versions`, { method: 'POST', body: JSON.stringify(body) })

export const getVersions = (messageId: number) => request<MessageVersion[]>(`/messages/${messageId}/versions`)

export interface ContextPreview {
  thread_id: number
  model: string
  messages: { role: 'system' | 'user' | 'assistant'; content: string; cache_breakpoint: boolean }[]
  token_count: number
  token_count_estimated: boolean
  token_budget: number
  over_budget: boolean
}

export const getContextPreview = (threadId: number) =>
  request<ContextPreview>(`/threads/${threadId}/context-preview`)
