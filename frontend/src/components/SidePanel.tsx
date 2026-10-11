import type { ReactNode } from 'react'
import type { ContextMode, Thread } from '../lib/api'

export interface PanelTab {
  key: string
  label: string
}

interface Props {
  tabs: PanelTab[]
  activeKey: string | null
  onActivate(key: string): void
  onClose(key: string): void
  renderTab(key: string): ReactNode
}

/** Right-hand panel of side threads, one tab each. Inactive tabs stay mounted so
 * streams, drafts and scroll positions survive switching. */
export function SidePanel({ tabs, activeKey, onActivate, onClose, renderTab }: Props) {
  return (
    <aside className="side-panel">
      <div className="side-tabs" role="tablist">
        {tabs.map((tab) => (
          <div key={tab.key} className={`side-tab${tab.key === activeKey ? ' active' : ''}`}>
            <button role="tab" aria-selected={tab.key === activeKey} onClick={() => onActivate(tab.key)} title={tab.label}>
              {tab.label}
            </button>
            <button className="side-tab-close" aria-label="Close thread" onClick={() => onClose(tab.key)}>
              ×
            </button>
          </div>
        ))}
      </div>
      {tabs.map((tab) => (
        <div key={tab.key} className="side-tab-body" hidden={tab.key !== activeKey}>
          {renderTab(tab.key)}
        </div>
      ))}
    </aside>
  )
}

interface HeaderProps {
  quote: string
  detached: boolean
  contextMode: ContextMode
  onContextModeChange(mode: ContextMode): void
  thread: Thread | null // null for a draft
  onResolve(): void
  onReopen(): void
}

/** The focus span a side thread is about, plus its per-thread controls. */
export function SideThreadHeader(props: HeaderProps) {
  const { quote, detached, contextMode, onContextModeChange, thread, onResolve, onReopen } = props
  const status = thread?.status ?? 'open'
  return (
    <div className="side-header">
      <blockquote className="focus-quote">{quote}</blockquote>
      <div className="side-controls">
        {detached && (
          <span className="detached" title="The quoted text is no longer in the message">
            detached
          </span>
        )}
        {status !== 'open' && <span className={`status status-${status}`}>{status}</span>}
        {status === 'open' && (
          <label title="What the model sees from the parent thread">
            Context{' '}
            <select value={contextMode} onChange={(e) => onContextModeChange(e.target.value as ContextMode)}>
              <option value="as_of_anchor">up to this reply</option>
              <option value="as_of_now">whole conversation</option>
            </select>
          </label>
        )}
        {status === 'open' && thread && thread.messages.length > 0 && (
          <button onClick={onResolve} title="Summarize into a note for the parent thread, or just close">
            Resolve…
          </button>
        )}
        {status === 'resolved' && <button onClick={onReopen}>Reopen</button>}
      </div>
      {thread?.merge_note && (
        <div className="merged-note">
          <span className="muted">Merged note</span>
          <p>{thread.merge_note.note_text}</p>
        </div>
      )}
    </div>
  )
}
