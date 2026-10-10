import { useState, type KeyboardEvent, type ReactNode } from 'react'
import type { ModelInfo } from '../lib/api'

interface Props {
  models: ModelInfo[]
  model: string
  onModelChange(model: string): void
  streaming: boolean
  onSend(content: string): void
  onStop(): void
  status?: ReactNode // shown between the model picker and the send button
}

export function Composer({ models, model, onModelChange, streaming, onSend, onStop, status }: Props) {
  const [text, setText] = useState('')

  function send() {
    const content = text.trim()
    if (!content || streaming) return
    onSend(content)
    setText('')
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="composer">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Message (Ctrl+Enter to send)"
        rows={3}
        autoFocus
      />
      <div className="composer-bar">
        <select value={model} onChange={(e) => onModelChange(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="composer-status">{status}</span>
        {streaming ? (
          <button onClick={onStop}>Stop</button>
        ) : (
          <button className="primary" onClick={send} disabled={!text.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  )
}
