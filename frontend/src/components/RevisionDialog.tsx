import { useEffect, useRef, useState } from 'react'
import { locateAnchor, type SourceAnchor } from '../lib/anchors'
import { reviseMessage, type Message, type RevisionProposal } from '../lib/api'
import { DiffView } from './DiffView'

/** Something anchored to the message being revised: a side thread or a sent comment. */
export interface AnchorRef extends SourceAnchor {
  label: string
}

interface Props {
  message: Message
  anchors: AnchorRef[]
  onAccept(content: string, reason: string | null): Promise<void>
  onClose(): void
}

/**
 * Revise a reply with its merged side threads' notes. The model proposes a
 * rewrite; the user reviews the diff (and may edit) before it becomes the new
 * version. Earlier versions are kept.
 */
export function RevisionDialog({ message, anchors, onAccept, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [streamed, setStreamed] = useState('')
  const [proposal, setProposal] = useState<RevisionProposal | null>(null)
  const [text, setText] = useState('')
  const [tab, setTab] = useState<'changes' | 'edit'>('changes')
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    dialogRef.current?.showModal()
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    reviseMessage(message.id, (delta) => setStreamed((s) => s + delta), abort.signal)
      .then((p) => {
        setProposal(p)
        setText(p.content)
      })
      .catch((e) => !abort.signal.aborted && setError(`Could not revise: ${e}`))
    return () => abort.abort()
  }, [message.id, attempt])

  const detaching = proposal
    ? anchors.filter((a) => locateAnchor(message.content, a) && !locateAnchor(text, a))
    : []

  function retry() {
    setStreamed('')
    setProposal(null)
    setError(null)
    setAttempt((n) => n + 1)
  }

  async function accept() {
    setSaving(true)
    setError(null)
    try {
      await onAccept(text, proposal?.reason ?? null)
      onClose()
    } catch (e) {
      setError(String(e))
      setSaving(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="merge-dialog revision-dialog"
      onCancel={(e) => {
        e.preventDefault()
        if (!saving) onClose()
      }}
    >
      <h2>Update response with resolved threads</h2>
      {!proposal ? (
        <>
          <p className="muted">{error ? '' : 'Rewriting…'}</p>
          {streamed && <pre className="revision-stream">{streamed}</pre>}
        </>
      ) : (
        <>
          <p className="muted">
            {proposal.reason} · proposed by {proposal.model}. Review before accepting; v{message.version} is kept.
          </p>
          <div className="revision-tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'changes'} onClick={() => setTab('changes')}>
              Changes
            </button>
            <button role="tab" aria-selected={tab === 'edit'} onClick={() => setTab('edit')}>
              Edit
            </button>
          </div>
          {tab === 'changes' ? (
            <DiffView before={message.content} after={text} />
          ) : (
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={18} />
          )}
          {detaching.length > 0 && (
            <div className="revision-detaching">
              {detaching.length === 1 ? 'This will detach 1 anchor' : `This will detach ${detaching.length} anchors`}{' '}
              (its text is gone): {detaching.map((a) => a.label).join(', ')}
            </div>
          )}
        </>
      )}
      {error && <div className="message-error">{error}</div>}
      <footer>
        <button onClick={retry} disabled={saving || (!proposal && !error)}>
          Try again
        </button>
        <span className="spacer" />
        <button onClick={onClose} disabled={saving}>
          Discard
        </button>
        <button className="primary" onClick={accept} disabled={saving || !proposal || !text.trim()}>
          Accept as v{message.version + 1}
        </button>
      </footer>
    </dialog>
  )
}
