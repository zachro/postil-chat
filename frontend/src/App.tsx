import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatPane, type MergeMarker } from './components/ChatPane'
import { ConversationList } from './components/ConversationList'
import { MergeNoteDialog } from './components/MergeNoteDialog'
import { MessageFooter } from './components/MessageFooter'
import { RevisionDialog, type AnchorRef } from './components/RevisionDialog'
import { VersionHistoryDialog } from './components/VersionHistoryDialog'
import type { MessageAnchor } from './components/MessageView'
import { SettingsDialog } from './components/SettingsDialog'
import { SidePanel, SideThreadHeader } from './components/SidePanel'
import { locateAnchor, type SourceAnchor } from './lib/anchors'
import { shorten } from './lib/format'
import {
  createConversation,
  createSideThread,
  createVersion,
  exportUrl,
  getConversation,
  getModels,
  getSettings,
  listConversations,
  mergeThread,
  resetSettings,
  updateSettings,
  updateThread,
  type ContextMode,
  type Conversation,
  type ConversationSummary,
  type Message,
  type ModelsResponse,
  type SearchHit,
  type Settings,
  type SettingsUpdate,
  type Thread,
} from './lib/api'

const MAX_DEPTH = 3 // side threads nest at most this deep (main thread is 0)
const TAB_LABEL_CHARS = 28
const NOTE_LABEL_CHARS = 40

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

export default function App() {
  const [models, setModels] = useState<ModelsResponse | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSaves, setSettingsSaves] = useState(0) // budgets may have changed: refresh meters
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  // null = a new chat that is saved on its first send (no empty conversations pile up)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draftModel, setDraftModel] = useState<string | null>(null) // model for an unsaved chat
  const [paneKey, setPaneKey] = useState(0) // bumped to reset the chat pane on navigation
  const [tabs, setTabs] = useState<Tab[]>([])
  const [activeTab, setActiveTab] = useState<string | null>(null)
  const draftCounter = useRef(0)
  const [resolving, setResolving] = useState<number | null>(null) // thread id in the merge dialog
  const [revising, setRevising] = useState<number | null>(null) // message id in the revision dialog
  const [history, setHistory] = useState<number | null>(null) // message id in the history dialog
  const focusMessage = useRef<number | null>(null) // a search hit to scroll to once it renders
  const [focusRequest, setFocusRequest] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const refreshList = useCallback(async () => {
    setConversations(await listConversations())
  }, [])

  const open = useCallback(async (id: number) => {
    setConversation(await getConversation(id))
    setPaneKey((k) => k + 1)
    setTabs([])
    setResolving(null)
    setRevising(null)
    setHistory(null)
  }, [])

  function startNew() {
    setConversation(null)
    setPaneKey((k) => k + 1)
    setTabs([])
    setResolving(null)
    setRevising(null)
    setHistory(null)
  }

  useEffect(() => {
    async function load() {
      const [m, s] = await Promise.all([getModels(), getSettings()])
      setModels(m)
      setSettings(s)
      const list = await listConversations()
      setConversations(list)
      if (list.length) await open(list[0].id)
    }
    load().catch((e) => setError(String(e)))
  }, [open])

  /** Apply saved settings; the models list carries the default model, so refetch it too. */
  async function applySettings(save: Promise<Settings>) {
    setSettings(await save)
    setModels(await getModels())
    setSettingsSaves((n) => n + 1)
  }

  // Bring a search hit into view once its conversation (and side thread tab) has rendered.
  useEffect(() => {
    const article = focusMessage.current !== null && document.getElementById(`message-${focusMessage.current}`)
    if (!article) return
    focusMessage.current = null
    article.scrollIntoView({ block: 'center' })
    article.classList.remove('flash')
    void article.offsetWidth // restart the animation
    article.classList.add('flash')
  }, [focusRequest, conversation, tabs])

  async function openHit(hit: SearchHit) {
    if (hit.conversation_id !== conversation?.id) await open(hit.conversation_id)
    if (hit.thread_kind === 'side') openThread(hit.thread_id)
    focusMessage.current = hit.message_id
    setFocusRequest((n) => n + 1)
  }

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

  function replaceMessage(message: Message) {
    updateThreadState(message.thread_id, (t) => ({
      ...t,
      messages: t.messages.map((m) => (m.id === message.id ? message : m)),
    }))
  }

  const messageById = (id: number | null) => threads.flatMap((t) => t.messages).find((m) => m.id === id)

  /** Merged side threads anchored to a reply whose notes are newer than its current version. */
  function pendingNotes(message: Message): number {
    const since = Date.parse(message.version_created_at)
    return threads.filter(
      (t) => t.anchor_message_id === message.id && t.merge_note && Date.parse(t.merge_note.created_at) > since,
    ).length
  }

  /** Side threads and sent comments anchored to a reply, for the revision dialog's detach warning. */
  function anchorRefs(message: Message): AnchorRef[] {
    const label = (section: string | null, quote: string) => section ?? `"${shorten(quote, NOTE_LABEL_CHARS)}"`
    const fromThreads = threads
      .filter((t) => t.anchor_message_id === message.id && t.anchor_quote !== null)
      .map((t) => ({
        quote: t.anchor_quote!,
        start: t.anchor_start ?? 0,
        end: t.anchor_end ?? 0,
        label: `thread ${label(t.anchor_section, t.anchor_quote!)}`,
      }))
    const fromComments = threads
      .flatMap((t) => t.messages)
      .flatMap((m) => m.inline_replies)
      .filter((r) => r.target_message_id === message.id)
      .map((r) => ({ ...r, label: `comment ${label(r.section, r.quote)}` }))
    return [...fromThreads, ...fromComments]
  }

  function messageFooter(message: Message) {
    return (
      <MessageFooter
        version={message.version}
        pendingNotes={pendingNotes(message)}
        canRevise={threadById(message.thread_id)?.status === 'open'}
        onRevise={() => setRevising(message.id)}
        onHistory={() => setHistory(message.id)}
      />
    )
  }

  // Changes when any reply is revised or settings are saved, so token counts refresh
  // without new messages.
  const versionKey = `${threads.flatMap((t) => t.messages.map((m) => m.version)).join(',')}:${settingsSaves}`

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
        closed: t.status !== 'open',
      }))
    const drafts = tabs.flatMap((tab) =>
      tab.draft?.messageId === messageId ? [{ ...tab.draft.anchor, threadId: null, count: 0 }] : [],
    )
    return [...saved, ...drafts]
  }

  /** Notes merged into a thread right after `messageId`, for markers in that thread. */
  function notesAfter(messageId: number): MergeMarker[] {
    return threads.flatMap((t) => {
      const note = t.merge_note
      if (!note || note.inject_after_message_id !== messageId) return []
      const target = threadById(note.target_thread_id)
      const after = target?.messages.find((m) => m.id === messageId)
      const pending = !target?.messages.some((m) => m.role === 'user' && m.position > (after?.position ?? -1))
      const label = t.anchor_section ?? `"${shorten(t.anchor_quote ?? '', NOTE_LABEL_CHARS)}"`
      return [{ threadId: t.id, label, text: note.note_text, pending }]
    })
  }

  function replaceThread(updated: Thread) {
    updateThreadState(updated.id, () => updated)
  }

  async function setStatus(threadId: number, status: 'open' | 'resolved') {
    replaceThread(await updateThread(threadId, { status }))
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
    const thread = threadById(tab.threadId)
    const quote = tab.draft?.anchor.quote ?? thread?.anchor_quote ?? 'Side thread'
    const done = thread && thread.status !== 'open' ? '✓ ' : ''
    return done + shorten(quote, TAB_LABEL_CHARS)
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
        notesAfter={notesAfter}
        messageFooter={messageFooter}
        sendWithEnter={settings?.send_with_enter}
        onOpenThread={openThread}
        onReplyInline={
          thread && thread.status === 'open' && depth(thread) < MAX_DEPTH
            ? (id, anchor) => replyInline(thread, id, anchor)
            : undefined
        }
        closedNotice={
          thread?.status === 'merged'
            ? 'Merged: the note above is in the parent thread\'s context.'
            : thread?.status === 'resolved'
              ? 'Resolved without merging. Reopen to continue.'
              : undefined
        }
        header={
          <SideThreadHeader
            quote={draft?.anchor.quote ?? thread!.anchor_quote ?? ''}
            detached={thread ? isDetached(thread) : false}
            contextMode={draft?.contextMode ?? thread!.context_mode}
            onContextModeChange={(mode) => changeContextMode(tab, mode).catch((e) => setError(String(e)))}
            thread={thread}
            onResolve={() => thread && setResolving(thread.id)}
            onReopen={() => thread && setStatus(thread.id, 'open').catch((e) => setError(String(e)))}
          />
        }
        emptyText="Ask about the highlighted text."
        refreshKey={`${thread?.context_mode}:${versionKey}`}
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
  // The list has the title set on the first send; the open conversation keeps its original.
  const title = conversations.find((c) => c.id === conversation?.id)?.title ?? conversation?.title
  return (
    <div className={`layout${tabs.length ? ' with-panel' : ''}`}>
      <ConversationList
        conversations={conversations}
        activeId={conversation?.id ?? null}
        onSelect={(id) => open(id).catch((e) => setError(String(e)))}
        onSelectHit={(hit) => openHit(hit).catch((e) => setError(String(e)))}
        onNew={startNew}
        onSettings={() => setSettingsOpen(true)}
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
        notesAfter={notesAfter}
        messageFooter={messageFooter}
        sendWithEnter={settings?.send_with_enter}
        refreshKey={versionKey}
        onOpenThread={openThread}
        onReplyInline={mainThread ? (id, anchor) => replyInline(mainThread, id, anchor) : undefined}
        header={
          conversation && (
            <header className="chat-header">
              <h1>{title}</h1>
              <span className="muted">Export</span>
              <a href={exportUrl(conversation.id, 'md')} download>
                Markdown
              </a>
              <a href={exportUrl(conversation.id, 'json')} download title="Every thread and message version">
                JSON
              </a>
            </header>
          )
        }
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
      {resolving !== null && (
        <MergeNoteDialog
          threadId={resolving}
          quote={threadById(resolving)?.anchor_quote ?? ''}
          onMerge={async (note) => replaceThread(await mergeThread(resolving, note))}
          onResolve={() => setStatus(resolving, 'resolved')}
          onClose={() => setResolving(null)}
        />
      )}
      {revising !== null && messageById(revising) && (
        <RevisionDialog
          message={messageById(revising)!}
          anchors={anchorRefs(messageById(revising)!)}
          onAccept={async (content, reason) => {
            const base = messageById(revising)!.version
            replaceMessage(await createVersion(revising, { content, reason, base_version: base }))
          }}
          onClose={() => setRevising(null)}
        />
      )}
      {settingsOpen && settings && (
        <SettingsDialog
          settings={settings}
          onSave={(update: SettingsUpdate) => applySettings(updateSettings(update))}
          onReset={() => applySettings(resetSettings())}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {history !== null && (
        <VersionHistoryDialog
          messageId={history}
          onRestore={async (v) => {
            const base = messageById(history)?.version
            replaceMessage(
              await createVersion(history, { content: v.content, reason: `Restored v${v.version}`, base_version: base }),
            )
          }}
          onClose={() => setHistory(null)}
        />
      )}
    </div>
  )
}
