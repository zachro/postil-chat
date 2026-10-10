import { useRef } from 'react'
import type { ContextPreview } from '../lib/api'

const fmt = new Intl.NumberFormat()

/** Token count for a thread's assembled context; click to see exactly what is sent. */
export function ContextMeter({ preview }: { preview: ContextPreview | null }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  if (!preview) return null

  const approx = preview.token_count_estimated ? '~' : ''
  return (
    <>
      <button
        className={`context-meter${preview.over_budget ? ' over' : ''}`}
        onClick={() => dialogRef.current?.showModal()}
        title={preview.over_budget ? 'Context is over the token budget' : 'Show assembled context'}
      >
        {approx}
        {fmt.format(preview.token_count)} / {fmt.format(preview.token_budget)} tokens
      </button>
      <dialog
        ref={dialogRef}
        className="context-dialog"
        onClick={(e) => e.target === dialogRef.current && dialogRef.current?.close()}
      >
        <header>
          <strong>Context sent to {preview.model}</strong>
          <span>
            {approx}
            {fmt.format(preview.token_count)} tokens
            {preview.token_count_estimated && ' (estimated)'}
          </span>
          <button onClick={() => dialogRef.current?.close()}>Close</button>
        </header>
        <ol>
          {preview.messages.map((m, i) => (
            <li key={i} className={`context-${m.role}`}>
              <div className="context-role">{m.role}</div>
              <pre>{m.content}</pre>
            </li>
          ))}
        </ol>
      </dialog>
    </>
  )
}
