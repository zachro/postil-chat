import { useMemo } from 'react'
import { diffLines, withContext } from '../lib/diff'

const CONTEXT_LINES = 3

/** Line diff between two versions of a reply, unchanged stretches collapsed. */
export function DiffView({ before, after }: { before: string; after: string }) {
  const items = useMemo(() => withContext(diffLines(before, after), CONTEXT_LINES), [before, after])
  if (before === after) return <p className="muted">No changes.</p>
  return (
    <pre className="diff">
      {items.map((item, i) =>
        item.type === 'skip' ? (
          <div key={i} className="diff-skip">
            ⋯ {item.count} unchanged lines
          </div>
        ) : (
          <div key={i} className={`diff-${item.type}`}>
            <span className="diff-sign">{item.type === 'add' ? '+' : item.type === 'del' ? '−' : ' '}</span>
            {item.text || ' '}
          </div>
        ),
      )}
    </pre>
  )
}
