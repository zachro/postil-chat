import { useMemo, type MouseEvent } from 'react'
import Markdown from 'react-markdown'
import { locateAnchor, rehypeSourceMap, type Highlight, type SourceAnchor } from '../lib/anchors'
import type { Message } from '../lib/api'

/** A side thread anchored in this message; threadId null for an unsaved draft. */
export interface MessageAnchor extends SourceAnchor {
  threadId: number | null
  count: number
  closed?: boolean // resolved or merged
}

interface Props {
  role: Message['role']
  content: string
  messageId?: number
  error?: string | null
  streaming?: boolean
  anchors?: MessageAnchor[]
  onOpenThread?(threadId: number): void
}

export function MessageView(props: Props) {
  const { role, content, messageId, error, streaming, anchors, onOpenThread } = props

  const plugins = useMemo(() => {
    const highlights: Highlight[] = []
    for (const a of anchors ?? []) {
      const span = locateAnchor(content, a) // detached anchors are shown in the panel only
      if (span) highlights.push({ ...a, start: span[0], end: span[1] })
    }
    return [[rehypeSourceMap, { source: content, highlights }] as [typeof rehypeSourceMap, object]]
  }, [anchors, content])

  function onClick(e: MouseEvent) {
    if (!onOpenThread || !window.getSelection()?.isCollapsed) return // a drag-select, not a click
    const target = e.target as HTMLElement
    const badge = target.closest<HTMLElement>('[data-thread]')
    const ids = badge?.dataset.thread ?? target.closest<HTMLElement>('[data-threads]')?.dataset.threads
    const id = ids?.split(' ').find((t) => t !== 'draft')
    if (id) onOpenThread(Number(id))
  }

  return (
    <article className={`message message-${role}`}>
      {role === 'assistant' ? (
        <div className="markdown" data-message-id={messageId} onClick={onClick}>
          <Markdown rehypePlugins={plugins}>{content}</Markdown>
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
