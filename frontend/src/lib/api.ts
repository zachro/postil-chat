// Typed client for the backend. Mirrors backend/app/schemas.py.

import { readSSE } from './sse'

export interface ModelInfo {
  id: string
  label: string
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
  error: string | null
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
  context_mode: 'as_of_anchor' | 'as_of_now'
  status: 'open' | 'resolved' | 'merged'
  title: string | null
  model: string | null
  messages: Message[]
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

export interface SendHandlers {
  onUserMessage(message: Message): void
  onDelta(text: string): void
  /** Called once with the saved assistant message; `error` is set if generation failed. */
  onFinish(message: Message): void
}

/** Send a user message and stream the reply. Abort via `signal` to stop generation. */
export async function sendMessage(
  threadId: number,
  content: string,
  model: string,
  handlers: SendHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/threads/${threadId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, model }),
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
      handlers.onFinish((payload as { message: Message }).message)
    }
  }
}

export interface ContextPreview {
  thread_id: number
  model: string
  messages: { role: 'system' | 'user' | 'assistant'; content: string }[]
  token_count: number
  token_count_estimated: boolean
  token_budget: number
  over_budget: boolean
}

export const getContextPreview = (threadId: number) =>
  request<ContextPreview>(`/threads/${threadId}/context-preview`)
