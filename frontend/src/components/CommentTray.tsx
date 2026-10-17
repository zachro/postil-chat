import type { SourceAnchor } from '../lib/anchors'
import { shorten } from '../lib/format'

const QUOTE_CHARS = 60

/** A comment on a span of an assistant message, waiting to be sent with the batch. */
export interface PendingComment {
  key: number
  messageId: number
  anchor: SourceAnchor
  text: string
}

interface Props {
  comments: PendingComment[]
  onChange(key: number, text: string): void
  onRemove(key: number): void
}

/** Pending multi-anchor comments above the composer, sent together as one message. */
export function CommentTray({ comments, onChange, onRemove }: Props) {
  if (!comments.length) return null
  return (
    <ul className="comment-tray">
      {comments.map((c) => (
        <li key={c.key}>
          <div className="comment-tray-quote">
            {c.anchor.section && <span className="comment-tray-section">{c.anchor.section}</span>}
            <span title={c.anchor.quote}>“{shorten(c.anchor.quote, QUOTE_CHARS)}”</span>
            <button className="comment-tray-remove" aria-label="Remove comment" onClick={() => onRemove(c.key)}>
              ×
            </button>
          </div>
          <textarea
            value={c.text}
            onChange={(e) => onChange(c.key, e.target.value)}
            placeholder="Comment on this part"
            rows={1}
            autoFocus
          />
        </li>
      ))}
    </ul>
  )
}
