import { useRef } from 'react'
import type { ContextPreview } from '../lib/api'

const fmt = new Intl.NumberFormat()
const NEAR_BUDGET = 0.8 // warn before the model's context window is full

/** Token count for a thread's assembled context; click to see exactly what is sent. */
export function ContextMeter({ preview }: { preview: ContextPreview | null }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  if (!preview) return null

  const approx = preview.token_count_estimated ? '~' : ''
  const level = preview.over_budget
    ? ' over'
    : preview.token_count >= NEAR_BUDGET * preview.token_budget
      ? ' near'
      : ''
  return (
    <>
      <button
        className={`context-meter${level}`}
        onClick={() => dialogRef.current?.showModal()}
        title={
          preview.over_budget
            ? 'Over the token budget: the provider may truncate or reject this context'
            : 'Show assembled context'
        }
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
