import { useCallback, useEffect, useState } from 'react'
import { ChatPane } from './components/ChatPane'
import { ConversationList } from './components/ConversationList'
import {
  createConversation,
  getConversation,
  getModels,
  listConversations,
  type Conversation,
  type ConversationSummary,
  type Message,
  type ModelsResponse,
} from './lib/api'

const mainThreadOf = (c: Conversation | null) => c?.threads.find((t) => t.kind === 'main')

export default function App() {
  const [models, setModels] = useState<ModelsResponse | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  // null = a new chat that is saved on its first send (no empty conversations pile up)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draftModel, setDraftModel] = useState<string | null>(null) // model for an unsaved chat
  const [paneKey, setPaneKey] = useState(0) // bumped to reset the chat pane on navigation
  const [error, setError] = useState<string | null>(null)

  const refreshList = useCallback(async () => {
    setConversations(await listConversations())
  }, [])

  const open = useCallback(async (id: number) => {
    setConversation(await getConversation(id))
    setPaneKey((k) => k + 1)
  }, [])

  function startNew() {
    setConversation(null)
    setPaneKey((k) => k + 1)
  }

  useEffect(() => {
    async function load() {
      setModels(await getModels())
      const list = await listConversations()
      setConversations(list)
      if (list.length) await open(list[0].id)
    }
    load().catch((e) => setError(String(e)))
  }, [open])

  async function createThread(): Promise<number> {
    const created = await createConversation()
    setConversation(created)
    await refreshList()
    return mainThreadOf(created)!.id
  }

  function updateMainThread(update: (messages: Message[]) => Message[], threadId?: number) {
    setConversation((prev) => {
      const main = mainThreadOf(prev)
      if (!prev || !main || (threadId !== undefined && main.id !== threadId)) return prev
      return {
        ...prev,
        threads: prev.threads.map((t) => (t.id === main.id ? { ...t, messages: update(t.messages) } : t)),
      }
    })
  }

  function appendMessage(message: Message) {
    // Dropped if the user switched conversations mid-stream. Replaces any local
    // placeholder at the same position (e.g. a stopped reply).
    updateMainThread(
      (messages) => [...messages.filter((m) => m.position !== message.position), message],
      message.thread_id,
    )
  }

  function changeModel(model: string) {
    if (!conversation) return setDraftModel(model)
    setConversation((prev) =>
      prev && {
        ...prev,
        threads: prev.threads.map((t) => (t.kind === 'main' ? { ...t, model } : t)),
      },
    )
  }

  if (error) return <div className="fatal">Could not reach the backend: {error}</div>
  if (!models) return null

  const mainThread = mainThreadOf(conversation) ?? null
  return (
    <div className="layout">
      <ConversationList
        conversations={conversations}
        activeId={conversation?.id ?? null}
        onSelect={(id) => open(id).catch((e) => setError(String(e)))}
        onNew={startNew}
      />
      <ChatPane
        key={paneKey}
        thread={mainThread}
        createThread={createThread}
        models={models.models}
        model={mainThread?.model ?? draftModel ?? models.default_model}
        onModelChange={changeModel}
        onAppend={appendMessage}
        onReplyFinished={() => refreshList().catch(() => {})}
      />
    </div>
  )
}
