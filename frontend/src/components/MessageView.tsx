import Markdown from 'react-markdown'
import type { Message } from '../lib/api'

interface Props {
  role: Message['role']
  content: string
  error?: string | null
  streaming?: boolean
}

export function MessageView({ role, content, error, streaming }: Props) {
  return (
    <article className={`message message-${role}`}>
      {role === 'assistant' ? (
        <div className="markdown">
          <Markdown>{content}</Markdown>
          {streaming && !content && <span className="typing">Thinking…</span>}
        </div>
      ) : (
        <div className="plain">{content}</div>
      )}
      {error && (
        <div className="message-error">
          {error === 'cancelled' ? 'Stopped.' : `Generation failed: ${error}`}
        </div>
      )}
    </article>
  )
}
