import { useLayoutEffect, useRef, useState } from 'react'
import { sendMessage, type Message, type ModelInfo, type Thread } from '../lib/api'
import { Composer } from './Composer'
import { MessageView } from './MessageView'

interface Props {
  thread: Thread | null // null for a new chat that has not been saved yet
  createThread(): Promise<number> // saves the new chat; returns its main thread id
  models: ModelInfo[]
  model: string
  onModelChange(model: string): void
  onAppend(message: Message): void
  onReplyFinished(): void
}

export function ChatPane(props: Props) {
  const { thread, createThread, models, model, onModelChange, onAppend, onReplyFinished } = props
  const messages = thread?.messages ?? []
  const [draft, setDraft] = useState<string | null>(null) // streaming reply, null when idle
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [messages.length, draft])

  function onScroll() {
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
    <main className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="chat-column">
          {messages.length === 0 && draft === null && (
            <p className="empty">Start the conversation below.</p>
          )}
          {messages.map((m) => (
            <MessageView key={m.id} role={m.role} content={m.content} error={m.error} />
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
        />
      </div>
    </main>
  )
}
