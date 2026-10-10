import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { rangeToAnchor, type SourceAnchor } from '../lib/anchors'
import {
  getContextPreview,
  sendMessage,
  type ContextPreview,
  type Message,
  type ModelInfo,
  type Thread,
} from '../lib/api'
import { Composer } from './Composer'
import { ContextMeter } from './ContextMeter'
import { MessageView, type MessageAnchor } from './MessageView'

interface Props {
  thread: Thread | null // null for a thread that has not been saved yet
  createThread(): Promise<number> // saves the thread on first send; returns its id
  models: ModelInfo[]
  model: string
  onModelChange(model: string): void
  onAppend(message: Message): void
  onReplyFinished(): void
  anchorsFor?(messageId: number): MessageAnchor[]
  onOpenThread?(threadId: number): void
  onReplyInline?(messageId: number, anchor: SourceAnchor): void // unset: selection does nothing
  header?: ReactNode
  emptyText?: string
  refreshKey?: string // changes when the thread's context changes without new messages
  className?: string
}

interface PendingReply {
  messageId: number
  anchor: SourceAnchor
  x: number
  y: number
}

export function ChatPane(props: Props) {
  const { thread, createThread, models, model, onModelChange, onAppend, onReplyFinished } = props
  const { anchorsFor, onOpenThread, onReplyInline, header, emptyText, refreshKey } = props
  const messages = thread?.messages ?? []
  const [draft, setDraft] = useState<string | null>(null) // streaming reply, null when idle
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const [preview, setPreview] = useState<ContextPreview | null>(null)
  const [pendingReply, setPendingReply] = useState<PendingReply | null>(null)
  const threadId = thread?.id
  const idle = draft === null

  // Refresh the token count whenever the thread settles on new content.
  useEffect(() => {
    if (threadId === undefined || !idle) return
    let current = true
    getContextPreview(threadId)
      .then((p) => current && setPreview(p))
      .catch(() => current && setPreview(null))
    return () => {
      current = false
    }
  }, [threadId, idle, messages.length, refreshKey])

  useEffect(() => {
    const clearIfCollapsed = () => {
      if (window.getSelection()?.isCollapsed) setPendingReply(null)
    }
    document.addEventListener('selectionchange', clearIfCollapsed)
    return () => document.removeEventListener('selectionchange', clearIfCollapsed)
  }, [])

  /** After a selection inside an assistant message, offer "Reply inline" next to it. */
  function onMouseUp() {
    if (!onReplyInline) return
    requestAnimationFrame(() => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !selection.rangeCount) return setPendingReply(null)
      const range = selection.getRangeAt(0)
      const start = range.startContainer
      const root = (start instanceof Element ? start : start.parentElement)?.closest<HTMLElement>(
        '[data-message-id]',
      )
      const message = root && messages.find((m) => m.id === Number(root.dataset.messageId))
      const anchor =
        root && message && scrollRef.current?.contains(root) && root.contains(range.endContainer)
          ? rangeToAnchor(root, range, message.content)
          : null
      if (!message || !anchor) return setPendingReply(null)
      const rect = range.getBoundingClientRect()
      setPendingReply({ messageId: message.id, anchor, x: rect.right, y: rect.bottom })
    })
  }

  function replyInline() {
    if (!pendingReply || !onReplyInline) return
    onReplyInline(pendingReply.messageId, pendingReply.anchor)
    window.getSelection()?.removeAllRanges()
    setPendingReply(null)
  }

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [messages.length, draft])

  function onScroll() {
    setPendingReply(null)
    const el = scrollRef.current
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  async function send(content: string) {
    const abort = new AbortController()
    abortRef.current = abort
    stickToBottom.current = true
    setError(null)
    setDraft('')
    let text = ''
    let lastPosition = messages.length - 1
    let threadId = thread?.id
    try {
      threadId ??= await createThread()
      await sendMessage(
        threadId,
        content,
        model,
        {
          onUserMessage(message) {
            lastPosition = message.position
            onAppend(message)
          },
          onDelta(delta) {
            text += delta
            setDraft(text)
          },
          onFinish(message) {
            onAppend(message)
          },
        },
        abort.signal,
      )
    } catch (e) {
      if (abort.signal.aborted && threadId !== undefined) {
        // The server saves the partial reply as cancelled; mirror it locally.
        onAppend({
          id: -Date.now(),
          thread_id: threadId,
          position: lastPosition + 1,
          role: 'assistant',
          content: text,
          model,
          error: 'cancelled',
          created_at: new Date().toISOString(),
        })
      } else {
        setError(String(e))
      }
    } finally {
      abortRef.current = null
      setDraft(null)
      onReplyFinished()
    }
  }

  return (
    <main className={`chat ${props.className ?? ''}`}>
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll} onMouseUp={onMouseUp}>
        <div className="chat-column">
          {header}
          {messages.length === 0 && draft === null && (
            <p className="empty">{emptyText ?? 'Start the conversation below.'}</p>
          )}
          {messages.map((m) => (
            <MessageView
              key={m.id}
              role={m.role}
              content={m.content}
              error={m.error}
              messageId={m.id > 0 ? m.id : undefined} // negative ids are local placeholders
              anchors={anchorsFor?.(m.id)}
              onOpenThread={onOpenThread}
            />
          ))}
          {draft !== null && <MessageView role="assistant" content={draft} streaming />}
          {error && <div className="message-error">{error}</div>}
        </div>
      </div>
      <div className="chat-column">
        <Composer
          models={models}
          model={model}
          onModelChange={onModelChange}
          streaming={draft !== null}
          onSend={send}
          onStop={() => abortRef.current?.abort()}
          status={<ContextMeter preview={preview} />}
        />
      </div>
      {pendingReply && (
        <button
          className="reply-inline primary"
          style={{
            left: Math.min(pendingReply.x, window.innerWidth - 130),
            top: Math.min(pendingReply.y + 6, window.innerHeight - 40),
          }}
          onMouseDown={(e) => e.preventDefault()} // keep the selection while clicking
          onClick={replyInline}
        >
          Reply inline
        </button>
      )}
    </main>
  )
}
