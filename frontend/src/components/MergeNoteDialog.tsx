import { useEffect, useRef, useState } from 'react'
import { summarizeThread } from '../lib/api'

interface Props {
  threadId: number
  quote: string
  onMerge(noteText: string): Promise<void>
  onResolve(): Promise<void> // resolve without merging
  onClose(): void
}

/**
 * Resolve a side thread. The summarizer proposes a note, which the user must
 * approve or edit before it reaches the parent thread's context: summaries can
 * drop the one detail that mattered.
 */
export function MergeNoteDialog({ threadId, quote, onMerge, onResolve, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [note, setNote] = useState('')
  const [summarizer, setSummarizer] = useState<string | null>(null)
  const [summarizing, setSummarizing] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    dialogRef.current?.showModal()
    let current = true
    summarizeThread(threadId)
      .then((proposal) => {
        if (!current) return
        setNote((typed) => typed || proposal.note_text) // never overwrite what the user typed
        setSummarizer(proposal.model)
      })
      .catch((e) => current && setError(`Could not summarize (${e}). You can write the note yourself.`))
      .finally(() => current && setSummarizing(false))
    return () => {
      current = false
    }
  }, [threadId])

  async function run(action: () => Promise<void>) {
    setSaving(true)
    setError(null)
    try {
      await action()
      onClose()
    } catch (e) {
      setError(String(e))
      setSaving(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="merge-dialog"
      onCancel={(e) => {
        e.preventDefault() // Esc: close through React state, not just the element
        if (!saving) onClose()
      }}
    >
      <h2>Resolve side thread</h2>
      <blockquote className="focus-quote">{quote}</blockquote>
      <label htmlFor="merge-note">
        Note for the parent thread{' '}
        <span className="muted">
          {summarizing
            ? '(summarizing…)'
            : summarizer && `(proposed by ${summarizer}; edit before merging)`}
        </span>
      </label>
      <textarea
        id="merge-note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={summarizing ? 'Summarizing the thread…' : 'Decisions, constraints, corrections (1-3 lines)'}
        rows={4}
        autoFocus
      />
      <p className="muted">
        The note is added to your next message in the parent thread. The side thread itself is
        never sent there.
      </p>
      {error && <div className="message-error">{error}</div>}
      <footer>
        <button onClick={() => run(onResolve)} disabled={saving}>
          Resolve without merging
        </button>
        <span className="spacer" />
        <button onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button
          className="primary"
          onClick={() => run(() => onMerge(note))}
          disabled={saving || !note.trim()}
        >
          Merge note
        </button>
      </footer>
    </dialog>
  )
}
