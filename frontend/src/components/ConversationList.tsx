import type { ConversationSummary } from '../lib/api'

interface Props {
  conversations: ConversationSummary[]
  activeId: number | null
  onSelect(id: number): void
  onNew(): void
}

export function ConversationList({ conversations, activeId, onSelect, onNew }: Props) {
  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <span className="brand">Postil</span>
        <button onClick={onNew}>New chat</button>
      </div>
      <ul>
        {conversations.map((c) => (
          <li key={c.id}>
            <button
              className={c.id === activeId ? 'active' : undefined}
              onClick={() => onSelect(c.id)}
              title={c.title}
            >
              {c.title}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
