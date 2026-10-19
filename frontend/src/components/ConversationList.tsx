import { useEffect, useState } from 'react'
import { searchConversations, type ConversationSummary, type SearchHit } from '../lib/api'

const SEARCH_DELAY_MS = 250

interface Props {
  conversations: ConversationSummary[]
  activeId: number | null
  onSelect(id: number): void
  onSelectHit(hit: SearchHit): void
  onNew(): void
}

export function ConversationList({ conversations, activeId, onSelect, onSelectHit, onNew }: Props) {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null) // null while a search is pending
  const searching = query.trim() !== ''

  useEffect(() => {
    if (!query.trim()) return
    let current = true
    const timer = setTimeout(() => {
      searchConversations(query)
        .then((h) => current && setHits(h))
        .catch(() => current && setHits([]))
    }, SEARCH_DELAY_MS)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [query])

  function changeQuery(value: string) {
    setQuery(value)
    setHits(null)
  }

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <span className="brand">Postil</span>
        <button onClick={onNew}>New chat</button>
      </div>
      <input
        className="sidebar-search"
        type="search"
        placeholder="Search conversations"
        value={query}
        onChange={(e) => changeQuery(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && changeQuery('')}
      />
      {searching ? (
        <ul className="search-results">
          {hits === null && <li className="muted">Searching…</li>}
          {hits?.length === 0 && <li className="muted">No matches</li>}
          {hits?.map((h) => (
            <li key={`${h.conversation_id}-${h.message_id}`}>
              <button onClick={() => onSelectHit(h)}>
                <span className="search-hit-title">
                  {h.conversation_title}
                  {h.message_id !== null && (
                    <span className="muted">
                      {' · '}
                      {h.thread_kind === 'side' ? 'side thread, ' : ''}
                      {h.role === 'user' ? 'you' : 'assistant'}
                    </span>
                  )}
                </span>
                <span className="search-hit-snippet">
                  {h.snippet.slice(0, h.match_start)}
                  <mark>{h.snippet.slice(h.match_start, h.match_end)}</mark>
                  {h.snippet.slice(h.match_end)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
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
      )}
    </nav>
  )
}
