import { useMemo, type MouseEvent, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import {
  commentKey,
  findSections,
  locateAnchor,
  rehypeSourceMap,
  sectionSpan,
  sourceToRange,
  type Highlight,
  type SourceAnchor,
} from '../lib/anchors'
import type { InlineReply, Message } from '../lib/api'
import { shorten } from '../lib/format'

const REPLY_QUOTE_CHARS = 40

/**
 * A span to highlight in this message: a side thread (threadId null for an
 * unsaved draft), a pending comment (threadId null), or a sent comment.
 */
export interface MessageAnchor extends SourceAnchor {
  threadId: number | null
  count: number
  closed?: boolean // resolved or merged
  comment?: Highlight['comment']
}

interface Props {
  role: Message['role']
  content: string
  messageId?: number
  error?: string | null
  streaming?: boolean
  anchors?: MessageAnchor[]
  inlineReplies?: InlineReply[] // a user message's multi-anchor comments
  onOpenThread?(threadId: number): void
  footer?: ReactNode
}

export function MessageView(props: Props) {
  const { role, content, messageId, error, streaming, anchors, inlineReplies, onOpenThread, footer } = props

  const plugins = useMemo(() => {
    const highlights: Highlight[] = []
    for (const a of anchors ?? []) {
      const span = locateAnchor(content, a) // detached anchors are shown in the panel only
      if (span) highlights.push({ ...a, start: span[0], end: span[1] })
    }
    return [[rehypeSourceMap, { source: content, highlights }] as [typeof rehypeSourceMap, object]]
  }, [anchors, content])
  const hasSections = useMemo(() => findSections(content).length > 0, [content])

  function onClick(e: MouseEvent) {
    if (!onOpenThread || !window.getSelection()?.isCollapsed) return // a drag-select, not a click
    const target = e.target as HTMLElement
    const badge = target.closest<HTMLElement>('[data-thread]')
    const ids = badge?.dataset.thread ?? target.closest<HTMLElement>('[data-threads]')?.dataset.threads
    if (ids) onOpenThread(Number(ids.split(' ')[0]))
  }

  /** A gutter label selects its whole section, which offers the usual reply actions. */
  function onMouseDown(e: MouseEvent<HTMLElement>) {
    const label = (e.target as HTMLElement).closest<HTMLElement>('[data-section-start]')
    if (!label || e.button !== 0) return
    e.preventDefault() // no drag-selection from the label itself
    const range = sourceToRange(e.currentTarget, ...sectionSpan(content, Number(label.dataset.sectionStart)))
    const selection = window.getSelection()
    if (!range || !selection) return
    selection.removeAllRanges()
    selection.addRange(range)
  }

  return (
    <article className={`message message-${role}`} id={messageId ? `message-${messageId}` : undefined}>
      {role === 'assistant' ? (
        <div
          className={`markdown${hasSections ? ' has-sections' : ''}`}
          data-message-id={messageId}
          onClick={onClick}
          onMouseDown={onMouseDown}
        >
          <Markdown rehypePlugins={plugins}>{content}</Markdown>
          {streaming && !content && <span className="typing">Thinking…</span>}
        </div>
      ) : (
        <div className="plain">
          {inlineReplies?.length ? (
            <ul className="inline-replies">
              {inlineReplies.map((r, i) => (
                <li key={i}>
                  <button
                    className="inline-reply-anchor"
                    title="Show in the response"
                    onClick={() => messageId && showComment(commentKey(messageId, i))}
                  >
                    re {r.section ?? `"${shorten(r.quote, REPLY_QUOTE_CHARS)}"`}
                  </button>
                  <span>{r.comment}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {content}
        </div>
      )}
      {error && (
        <div className="message-error">
          {error === 'cancelled' ? 'Stopped.' : `Generation failed: ${error}`}
        </div>
      )}
      {footer}
    </article>
  )
}

/** Scroll a sent comment's highlighted span into view and flash it. */
function showComment(key: string) {
  const mark = document.querySelector<HTMLElement>(`[data-comment="${key}"]`)
  if (!mark) return
  mark.scrollIntoView({ block: 'center', behavior: 'smooth' })
  mark.classList.remove('flash')
  void mark.offsetWidth // restart the animation
  mark.classList.add('flash')
}
