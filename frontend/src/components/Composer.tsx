import { useState, type KeyboardEvent, type ReactNode } from 'react'
import type { ModelInfo } from '../lib/api'
import { isSendKey } from '../lib/keys'

interface Props {
  models: ModelInfo[]
  model: string
  onModelChange(model: string): void
  streaming: boolean
  onSend(content: string): void
  onStop(): void
  status?: ReactNode // shown between the model picker and the send button
  tray?: ReactNode // pending inline comments, shown above the text box
  canSendEmpty?: boolean // the tray alone makes a message
  blocked?: string // why sending is disabled right now, if it is
  sendWithEnter?: boolean // Enter sends and Shift+Enter adds a line; otherwise Ctrl+Enter sends
}

export function Composer(props: Props) {
  const { models, model, onModelChange, streaming, onSend, onStop, status, tray, canSendEmpty, blocked } = props
  const { sendWithEnter } = props
  const [text, setText] = useState('')
  const canSend = !streaming && !blocked && (canSendEmpty || !!text.trim())

  function send() {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  // On the wrapper, so the send key also works in the tray's comment boxes.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const { key, shiftKey, altKey, ctrlKey, metaKey } = e
    const isComposing = e.nativeEvent.isComposing
    if (isSendKey({ key, shiftKey, altKey, ctrlKey, metaKey, isComposing }, !!sendWithEnter)) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="composer" onKeyDown={onKeyDown}>
      {tray}
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={
          canSendEmpty
            ? 'Add a message to your comments (optional)'
            : `Message (${sendWithEnter ? 'Enter to send, Shift+Enter for a new line' : 'Ctrl+Enter to send'})`
        }
        rows={3}
        autoFocus
      />
      <div className="composer-bar">
        <select value={model} onChange={(e) => onModelChange(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>
              {m.available ? m.label : `${m.label} (set ${m.missing_env})`}
            </option>
          ))}
        </select>
        <span className="composer-status">{status}</span>
        {streaming ? (
          <button onClick={onStop}>Stop</button>
        ) : (
          <button className="primary" onClick={send} disabled={!canSend} title={blocked}>
            Send
          </button>
        )}
      </div>
    </div>
  )
}
