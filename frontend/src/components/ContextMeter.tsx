import { useRef } from 'react'
import type { ContextPreview, Usage } from '../lib/api'

const fmt = new Intl.NumberFormat()
const NEAR_BUDGET = 0.8 // warn before the model's context window is full

function describeUsage(u: Usage): string {
  const prompt = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
  const cached = u.cache_read_tokens ? `, ${fmt.format(u.cache_read_tokens)} from cache` : ''
  const written = u.cache_write_tokens ? `, ${fmt.format(u.cache_write_tokens)} written to cache` : ''
  return `Last reply: ${fmt.format(prompt)} prompt tokens${cached}${written}; ${fmt.format(u.output_tokens)} output`
}

interface Props {
  preview: ContextPreview | null
  lastUsage?: Usage | null // as reported by the provider for this pane's last reply
}

/** Token count for a thread's assembled context; click to see exactly what is sent. */
export function ContextMeter({ preview, lastUsage }: Props) {
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
        title={[
          preview.over_budget
            ? 'Over the token budget: the provider may truncate or reject this context'
            : 'Show assembled context',
          lastUsage && describeUsage(lastUsage),
        ]
          .filter(Boolean)
          .join('\n')}
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
        {lastUsage && <p className="context-usage">{describeUsage(lastUsage)}</p>}
        <ol>
          {preview.messages.map((m, i) => (
            <li key={i} className={`context-${m.role}`}>
              <div className="context-role">{m.role}</div>
              <pre>{m.content}</pre>
              {m.cache_breakpoint && (
                <div className="context-breakpoint" title="Shared with sibling threads; cached by providers that support it">
                  cached prefix ends here
                </div>
              )}
            </li>
          ))}
        </ol>
      </dialog>
    </>
  )
}
