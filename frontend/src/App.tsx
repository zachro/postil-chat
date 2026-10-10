import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatPane } from './components/ChatPane'
import { ConversationList } from './components/ConversationList'
import type { MessageAnchor } from './components/MessageView'
import { SidePanel, SideThreadHeader } from './components/SidePanel'
import { locateAnchor, type SourceAnchor } from './lib/anchors'
import {
  createConversation,
  createSideThread,
  getConversation,
  getModels,
  listConversations,
  updateThread,
  type ContextMode,
  type Conversation,
  type ConversationSummary,
  type Message,
  type ModelsResponse,
  type Thread,
} from './lib/api'

const MAX_DEPTH = 3 // side threads nest at most this deep (main thread is 0)
const TAB_LABEL_CHARS = 28

/** A side thread the user started but has not sent a first message in yet. */
interface DraftThread {
  parentThreadId: number
  messageId: number
  anchor: SourceAnchor
  contextMode: ContextMode
  model: string | null
}

interface Tab {
  key: string
  threadId: number | null // null while still a draft
  draft: DraftThread | null
}

const mainThreadOf = (c: Conversation | null) => c?.threads.find((t) => t.kind === 'main')

function shorten(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

export default function App() {
  const [models, setModels] = useState<ModelsResponse | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  // null = a new chat that is saved on its first send (no empty conversations pile up)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draftModel, setDraftModel] = useState<string | null>(null) // model for an unsaved chat
  const [paneKey, setPaneKey] = useState(0) // bumped to reset the chat pane on navigation
  const [tabs, setTabs] = useState<Tab[]>([])
  const [activeTab, setActiveTab] = useState<string | null>(null)
  const draftCounter = useRef(0)
  const [error, setError] = useState<string | null>(null)

  const refreshList = useCallback(async () => {
    setConversations(await listConversations())
  }, [])

  const open = useCallback(async (id: number) => {
    setConversation(await getConversation(id))
    setPaneKey((k) => k + 1)
    setTabs([])
  }, [])

  function startNew() {
    setConversation(null)
    setPaneKey((k) => k + 1)
    setTabs([])
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

  // Esc closes the side panel (unless a dialog is open; it handles Esc itself).
  useEffect(() => {
    if (!tabs.length) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.querySelector('dialog[open]')) setTabs([])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tabs.length])

  // --- Conversation state ------------------------------------------------------

  const threads = conversation?.threads ?? []
  const threadById = (id: number | null) => threads.find((t) => t.id === id)

  function updateThreadState(threadId: number, update: (thread: Thread) => Thread) {
    setConversation((prev) =>
      prev && { ...prev, threads: prev.threads.map((t) => (t.id === threadId ? update(t) : t)) },
    )
  }

  function appendMessage(message: Message) {
    // Ignored if the thread is not in the open conversation (switched mid-stream).
    // Replaces any local placeholder at the same position (e.g. a stopped reply).
    updateThreadState(message.thread_id, (t) => ({
      ...t,
      messages: [...t.messages.filter((m) => m.position !== message.position), message],
    }))
  }

  function resolveModel(thread: Thread | undefined): string {
    for (let t = thread; t; t = threadById(t.parent_thread_id)) {
      if (t.model) return t.model
    }
    return models!.default_model
  }

  function depth(thread: Thread | undefined): number {
    let d = 0
    for (let t = thread; t?.parent_thread_id != null; t = threadById(t.parent_thread_id)) d++
    return d
  }

  function anchorsFor(messageId: number): MessageAnchor[] {
    const saved = threads
      .filter((t) => t.anchor_message_id === messageId && t.anchor_quote !== null)
      .map((t) => ({
        quote: t.anchor_quote!,
        start: t.anchor_start ?? 0,
        end: t.anchor_end ?? 0,
        threadId: t.id,
        count: t.messages.length,
      }))
    const drafts = tabs.flatMap((tab) =>
      tab.draft?.messageId === messageId ? [{ ...tab.draft.anchor, threadId: null, count: 0 }] : [],
    )
    return [...saved, ...drafts]
  }

  function isDetached(thread: Thread): boolean {
    if (thread.anchor_quote === null) return false
    const message = threads.flatMap((t) => t.messages).find((m) => m.id === thread.anchor_message_id)
    if (!message) return false
    const anchor = { quote: thread.anchor_quote, start: thread.anchor_start ?? 0, end: thread.anchor_end ?? 0 }
    return locateAnchor(message.content, anchor) === null
  }

  // --- Side panel tabs ----------------------------------------------------------

  function openThread(threadId: number) {
    const key = `thread-${threadId}`
    setTabs((prev) =>
      prev.some((t) => t.threadId === threadId) ? prev : [...prev, { key, threadId, draft: null }],
    )
    setActiveTab(tabs.find((t) => t.threadId === threadId)?.key ?? key)
  }

  function replyInline(parent: Thread, messageId: number, anchor: SourceAnchor) {
    const same = tabs.find(
      (t) => t.draft?.messageId === messageId && t.draft.anchor.start === anchor.start && t.draft.anchor.end === anchor.end,
    )
    if (same) return setActiveTab(same.key)
    const key = `draft-${++draftCounter.current}`
    const draft: DraftThread = { parentThreadId: parent.id, messageId, anchor, contextMode: 'as_of_anchor', model: null }
    setTabs((prev) => [...prev, { key, threadId: null, draft }])
    setActiveTab(key)
  }

  function closeTab(key: string) {
    const remaining = tabs.filter((t) => t.key !== key)
    setTabs(remaining)
    if (activeTab === key) setActiveTab(remaining.at(-1)?.key ?? null)
  }

  function updateDraft(key: string, update: Partial<DraftThread>) {
    setTabs((prev) => prev.map((t) => (t.key === key && t.draft ? { ...t, draft: { ...t.draft, ...update } } : t)))
  }

  async function saveDraft(key: string, draft: DraftThread): Promise<number> {
    const thread = await createSideThread(draft.messageId, { ...draft.anchor, context_mode: draft.contextMode })
    setConversation((prev) => prev && { ...prev, threads: [...prev.threads, thread] })
    setTabs((prev) => prev.map((t) => (t.key === key ? { ...t, threadId: thread.id, draft: null } : t)))
    return thread.id
  }

  async function changeContextMode(tab: Tab, mode: ContextMode) {
    if (tab.draft) return updateDraft(tab.key, { contextMode: mode })
    const updated = await updateThread(tab.threadId!, { context_mode: mode })
    updateThreadState(updated.id, (t) => ({ ...t, context_mode: updated.context_mode }))
  }

  function tabLabel(tab: Tab): string {
    const quote = tab.draft?.anchor.quote ?? threadById(tab.threadId)?.anchor_quote ?? 'Side thread'
    return shorten(quote, TAB_LABEL_CHARS)
  }

  function renderTab(key: string) {
    const tab = tabs.find((t) => t.key === key)!
    const thread = threadById(tab.threadId) ?? null
    const draft = tab.draft
    if (!thread && !draft) return null
    const parent = threadById(thread?.parent_thread_id ?? draft!.parentThreadId)
    return (
      <ChatPane
        className="chat-side"
        thread={thread}
        createThread={() => saveDraft(key, draft!)}
        models={models!.models}
        model={thread ? resolveModel(thread) : (draft!.model ?? resolveModel(parent))}
        onModelChange={(model) =>
          thread ? updateThreadState(thread.id, (t) => ({ ...t, model })) : updateDraft(key, { model })
        }
        onAppend={appendMessage}
        onReplyFinished={() => {}}
        anchorsFor={anchorsFor}
        onOpenThread={openThread}
        onReplyInline={thread && depth(thread) < MAX_DEPTH ? (id, anchor) => replyInline(thread, id, anchor) : undefined}
        header={
          <SideThreadHeader
            quote={draft?.anchor.quote ?? thread!.anchor_quote ?? ''}
            detached={thread ? isDetached(thread) : false}
            contextMode={draft?.contextMode ?? thread!.context_mode}
            onContextModeChange={(mode) => changeContextMode(tab, mode).catch((e) => setError(String(e)))}
          />
        }
        emptyText="Ask about the highlighted text."
        refreshKey={thread?.context_mode}
      />
    )
  }

  // --- Layout ---------------------------------------------------------------------

  async function createMainThread(): Promise<number> {
    const created = await createConversation()
    setConversation(created)
    await refreshList()
    return mainThreadOf(created)!.id
  }

  if (error) return <div className="fatal">Something went wrong: {error}</div>
  if (!models) return null

  const mainThread = mainThreadOf(conversation) ?? null
  return (
    <div className={`layout${tabs.length ? ' with-panel' : ''}`}>
      <ConversationList
        conversations={conversations}
        activeId={conversation?.id ?? null}
        onSelect={(id) => open(id).catch((e) => setError(String(e)))}
        onNew={startNew}
      />
      <ChatPane
        key={paneKey}
        thread={mainThread}
        createThread={createMainThread}
        models={models.models}
        model={mainThread ? resolveModel(mainThread) : (draftModel ?? models.default_model)}
        onModelChange={(model) =>
          mainThread ? updateThreadState(mainThread.id, (t) => ({ ...t, model })) : setDraftModel(model)
        }
        onAppend={appendMessage}
        onReplyFinished={() => refreshList().catch(() => {})}
        anchorsFor={anchorsFor}
        onOpenThread={openThread}
        onReplyInline={mainThread ? (id, anchor) => replyInline(mainThread, id, anchor) : undefined}
      />
      {tabs.length > 0 && (
        <SidePanel
          tabs={tabs.map((t) => ({ key: t.key, label: tabLabel(t) }))}
          activeKey={activeTab}
          onActivate={setActiveTab}
          onClose={closeTab}
          renderTab={renderTab}
        />
      )}
    </div>
  )
}
