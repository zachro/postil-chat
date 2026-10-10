import type { ReactNode } from 'react'
import type { ContextMode } from '../lib/api'

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
}

/** The focus span a side thread is about, plus its per-thread controls. */
export function SideThreadHeader({ quote, detached, contextMode, onContextModeChange }: HeaderProps) {
  return (
    <div className="side-header">
      <blockquote className="focus-quote">{quote}</blockquote>
      <div className="side-controls">
        {detached && (
          <span className="detached" title="The quoted text is no longer in the message">
            detached
          </span>
        )}
        <label title="What the model sees from the parent thread">
          Context{' '}
          <select value={contextMode} onChange={(e) => onContextModeChange(e.target.value as ContextMode)}>
            <option value="as_of_anchor">up to this reply</option>
            <option value="as_of_now">whole conversation</option>
          </select>
        </label>
      </div>
    </div>
  )
}
