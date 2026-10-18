interface Props {
  version: number
  pendingNotes: number // merged side threads not yet applied to this reply
  canRevise: boolean
  onRevise(): void
  onHistory(): void
}

/** Version and revise-in-place actions under an assistant reply. */
export function MessageFooter({ version, pendingNotes, canRevise, onRevise, onHistory }: Props) {
  const revise = canRevise && pendingNotes > 0
  if (version === 1 && !revise) return null
  return (
    <div className="message-footer">
      {version > 1 && (
        <button className="link" onClick={onHistory} title="Show earlier versions of this reply">
          v{version} · history
        </button>
      )}
      {revise && (
        <button onClick={onRevise} title="Rewrite this reply with the conclusions of its merged side threads">
          Update with {pendingNotes} resolved {pendingNotes === 1 ? 'thread' : 'threads'}
        </button>
      )}
    </div>
  )
}
