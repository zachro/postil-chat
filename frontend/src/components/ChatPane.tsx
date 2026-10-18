import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { commentKey, rangeToAnchor, sectionOf, type SourceAnchor } from '../lib/anchors'
import {
  getContextPreview,
  sendMessage,
  type ContextPreview,
  type Message,
  type Usage,
  type ModelInfo,
  type Thread,
} from '../lib/api'
import { CommentTray, type PendingComment } from './CommentTray'
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
  closedNotice?: ReactNode // set when the thread takes no new messages; replaces the composer
  notesAfter?(messageId: number): MergeMarker[] // merged notes that take effect after a message
  className?: string
}

/** A side thread's note as shown in the thread it was merged into. */
export interface MergeMarker {
  threadId: number // the merged side thread
  label: string
  text: string
  pending: boolean // no user turn after it yet: it rides along with the next message
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
  const { closedNotice, notesAfter } = props
  const messages = thread?.messages ?? []
  const [draft, setDraft] = useState<string | null>(null) // streaming reply, null when idle
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const [preview, setPreview] = useState<ContextPreview | null>(null)
  const [pendingReply, setPendingReply] = useState<PendingReply | null>(null)
  const [lastUsage, setLastUsage] = useState<Usage | null>(null)
  const [comments, setComments] = useState<PendingComment[]>([]) // multi-anchor reply tray
  const commentCounter = useRef(0)
  const canComment = thread !== null && !closedNotice
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

  /** After a selection inside an assistant message, offer "Reply inline" / "Add comment" next to it. */
  function onMouseUp() {
    if (!onReplyInline && !canComment) return
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
      anchor.section = sectionOf(message.content, anchor.start, anchor.end)
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

  function addComment() {
    if (!pendingReply) return
    const { messageId, anchor } = pendingReply
    setComments((prev) => [...prev, { key: ++commentCounter.current, messageId, anchor, text: '' }])
    window.getSelection()?.removeAllRanges()
    setPendingReply(null)
  }

  /** Side threads, pending comments, and comments already sent in this thread. */
  function anchorsOf(messageId: number): MessageAnchor[] {
    const pending = comments
      .filter((c) => c.messageId === messageId)
      .map((c) => ({ ...c.anchor, threadId: null, count: 0 }))
    const sent = messages.flatMap((m) =>
      m.inline_replies.flatMap((r, i) =>
        r.target_message_id === messageId
          ? [{ ...r, threadId: null, count: 0, comment: { key: commentKey(m.id, i), text: r.comment } }]
          : [],
      ),
    )
    return [...(anchorsFor?.(messageId) ?? []), ...pending, ...sent]
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
      const inline_replies = comments.map((c) => ({
        target_message_id: c.messageId,
        quote: c.anchor.quote,
        start: c.anchor.start,
        end: c.anchor.end,
        section: c.anchor.section ?? null,
        comment: c.text.trim(),
      }))
      await sendMessage(
        threadId,
        { content, model, inline_replies },
        {
          onUserMessage(message) {
            lastPosition = message.position
            onAppend(message)
            setComments([]) // saved with the message; only now is it safe to drop them
          },
          onDelta(delta) {
            text += delta
            setDraft(text)
          },
          onFinish(message, usage) {
            onAppend(message)
            setLastUsage(usage)
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
          token_count: null,
          error: 'cancelled',
          created_at: new Date().toISOString(),
          inline_replies: [],
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
            <Fragment key={m.id}>
              <MessageView
                role={m.role}
                content={m.content}
                error={m.error}
                messageId={m.id > 0 ? m.id : undefined} // negative ids are local placeholders
                anchors={anchorsOf(m.id)}
                inlineReplies={m.inline_replies}
                onOpenThread={onOpenThread}
              />
              {notesAfter?.(m.id).map((note) => (
                <button
                  key={note.threadId}
                  className="merge-marker"
                  onClick={() => onOpenThread?.(note.threadId)}
                  title={
                    note.pending
                      ? 'Merged from a side thread; sent with your next message'
                      : 'Merged from a side thread; in this thread\'s context'
                  }
                >
                  <span className="merge-marker-label">
                    Merged · re {note.label}
                    {note.pending && ' · goes with your next message'}
                  </span>
                  <span>{note.text}</span>
                </button>
              ))}
            </Fragment>
          ))}
          {draft !== null && <MessageView role="assistant" content={draft} streaming />}
          {error && <div className="message-error">{error}</div>}
        </div>
      </div>
      <div className="chat-column">
        {closedNotice ? (
          <div className="composer-closed">{closedNotice}</div>
        ) : (
          <Composer
            models={models}
            model={model}
            onModelChange={onModelChange}
            streaming={draft !== null}
            onSend={send}
            onStop={() => abortRef.current?.abort()}
            status={<ContextMeter preview={preview} lastUsage={lastUsage} />}
            tray={
              <CommentTray
                comments={comments}
                onChange={(key, text) => setComments((prev) => prev.map((c) => (c.key === key ? { ...c, text } : c)))}
                onRemove={(key) => setComments((prev) => prev.filter((c) => c.key !== key))}
              />
            }
            canSendEmpty={comments.length > 0}
            blocked={
              comments.some((c) => !c.text.trim()) ? 'Write each comment or remove it before sending' : undefined
            }
          />
        )}
      </div>
      {pendingReply && (
        <div
          className="reply-inline"
          style={{
            left: Math.min(pendingReply.x, window.innerWidth - 240),
            top: Math.min(pendingReply.y + 6, window.innerHeight - 40),
          }}
          onMouseDown={(e) => e.preventDefault()} // keep the selection while clicking
        >
          {onReplyInline && (
            <button className="primary" onClick={replyInline}>
              Reply inline
            </button>
          )}
          {canComment && (
            <button onClick={addComment} title="Collect comments on several parts and send them as one message">
              Add comment
            </button>
          )}
        </div>
      )}
    </main>
  )
}
