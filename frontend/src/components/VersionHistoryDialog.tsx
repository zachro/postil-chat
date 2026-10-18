import { useEffect, useRef, useState } from 'react'
import { getVersions, type MessageVersion } from '../lib/api'
import { DiffView } from './DiffView'

interface Props {
  messageId: number
  onRestore(version: MessageVersion): Promise<void>
  onClose(): void
}

/** A reply's versions, each shown as a diff against the one before. Restoring adds a new version. */
export function VersionHistoryDialog({ messageId, onRestore, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [versions, setVersions] = useState<MessageVersion[] | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    dialogRef.current?.showModal()
    let current = true
    getVersions(messageId)
      .then((vs) => {
        if (!current) return
        setVersions(vs)
        setSelected(vs.at(-1)?.version ?? null)
      })
      .catch((e) => current && setError(String(e)))
    return () => {
      current = false
    }
  }, [messageId])

  const index = versions?.findIndex((v) => v.version === selected) ?? -1
  const shown = versions?.[index]
  const previous = index > 0 ? versions![index - 1] : null
  const latest = versions?.at(-1)

  async function restore(version: MessageVersion) {
    setSaving(true)
    setError(null)
    try {
      await onRestore(version)
      onClose()
    } catch (e) {
      setError(String(e))
      setSaving(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="merge-dialog history-dialog"
      onCancel={(e) => {
        e.preventDefault()
        if (!saving) onClose()
      }}
    >
      <h2>Version history</h2>
      <div className="history-body">
        <ol className="history-list">
          {versions?.map((v) => (
            <li key={v.version}>
              <button aria-current={v.version === selected} onClick={() => setSelected(v.version)}>
                <strong>
                  v{v.version}
                  {v.version === latest?.version && ' (current)'}
                </strong>
                <span className="muted">{v.reason ?? (v.version === 1 ? 'Original reply' : '')}</span>
                <span className="muted">{new Date(v.created_at).toLocaleString()}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="history-view">
          {shown && (previous ? <DiffView before={previous.content} after={shown.content} /> : <pre className="diff">{shown.content}</pre>)}
        </div>
      </div>
      {error && <div className="message-error">{error}</div>}
      <footer>
        <span className="spacer" />
        <button onClick={onClose} disabled={saving}>
          Close
        </button>
        {shown && latest && shown.version !== latest.version && (
          <button className="primary" onClick={() => restore(shown)} disabled={saving}>
            Restore as v{latest.version + 1}
          </button>
        )}
      </footer>
    </dialog>
  )
}
