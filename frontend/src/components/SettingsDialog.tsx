import { useEffect, useRef, useState } from 'react'
import type { Settings, SettingsUpdate } from '../lib/api'

interface Props {
  settings: Settings
  onSave(update: SettingsUpdate): Promise<void>
  onReset(): Promise<void>
  onClose(): void
}

const positiveInt = (text: string) => (/^\d+$/.test(text.trim()) && Number(text) > 0 ? Number(text) : null)

/**
 * Settings saved over config.yaml. Models and API keys stay in config.yaml and
 * .env; here they are listed with their context windows, which can be overridden.
 */
export function SettingsDialog({ settings, onSave, onReset, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [defaultModel, setDefaultModel] = useState(settings.default_model)
  const [summarizer, setSummarizer] = useState(settings.summarizer_model ?? '')
  const [sendWithEnter, setSendWithEnter] = useState(settings.send_with_enter)
  const [budget, setBudget] = useState(String(settings.token_budget))
  const [windows, setWindows] = useState<Record<string, string>>(() =>
    Object.fromEntries(settings.models.map((m) => [m.id, m.context_window?.toString() ?? ''])),
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    dialogRef.current?.showModal()
  }, [])

  const invalid =
    positiveInt(budget) === null || Object.values(windows).some((w) => w.trim() !== '' && positiveInt(w) === null)

  /** Only what changed, so unchanged settings keep following config.yaml. */
  function changes(): SettingsUpdate {
    const update: SettingsUpdate = {}
    if (defaultModel !== settings.default_model) update.default_model = defaultModel
    if ((summarizer || null) !== settings.summarizer_model) update.summarizer_model = summarizer || null
    if (sendWithEnter !== settings.send_with_enter) update.send_with_enter = sendWithEnter
    if (positiveInt(budget) !== settings.token_budget) update.token_budget = positiveInt(budget)!
    const contextWindows: Record<string, number | null> = {}
    for (const m of settings.models) {
      const value = positiveInt(windows[m.id]) // blank: back to config.yaml
      if (value === m.context_window) continue
      contextWindows[m.id] = value === m.config_context_window ? null : value
    }
    if (Object.keys(contextWindows).length) update.context_windows = contextWindows
    return update
  }

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
      className="merge-dialog settings-dialog"
      onCancel={(e) => {
        e.preventDefault()
        if (!saving) onClose()
      }}
    >
      <h2>Settings</h2>
      <div className="settings-grid">
        <label htmlFor="settings-default-model">Default model</label>
        <div>
          <select id="settings-default-model" value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}>
            {settings.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          <span className="muted">For new chats</span>
        </div>

        <label htmlFor="settings-summarizer">Summarizer</label>
        <div>
          <select id="settings-summarizer" value={summarizer} onChange={(e) => setSummarizer(e.target.value)}>
            <option value="">Same as the thread</option>
            {settings.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          <span className="muted">Writes merge notes</span>
        </div>

        <label htmlFor="settings-enter">Send with Enter</label>
        <div>
          <input
            id="settings-enter"
            type="checkbox"
            checked={sendWithEnter}
            onChange={(e) => setSendWithEnter(e.target.checked)}
          />
          <span className="muted">
            {sendWithEnter ? 'Enter sends; Shift+Enter starts a new line' : 'Ctrl+Enter sends; Enter starts a new line'}
          </span>
        </div>

        <label htmlFor="settings-budget">Token budget</label>
        <div>
          <input
            id="settings-budget"
            inputMode="numeric"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            aria-invalid={positiveInt(budget) === null}
          />
          <span className="muted">For models without a context window</span>
        </div>
      </div>

      <h3>Models</h3>
      <p className="muted">
        Models and API keys are set in config.yaml and .env. The context window should match what the server
        really allocates (for Ollama, OLLAMA_CONTEXT_LENGTH).
      </p>
      <table className="settings-models">
        <thead>
          <tr>
            <th>Model</th>
            <th>Context window</th>
          </tr>
        </thead>
        <tbody>
          {settings.models.map((m) => (
            <tr key={m.id}>
              <td>
                {m.label}
                {!m.available && <span className="muted"> · set {m.missing_env}</span>}
              </td>
              <td>
                <input
                  inputMode="numeric"
                  aria-label={`${m.label} context window`}
                  value={windows[m.id]}
                  placeholder={m.config_context_window?.toString() ?? `budget (${settings.token_budget})`}
                  onChange={(e) => setWindows((prev) => ({ ...prev, [m.id]: e.target.value }))}
                  aria-invalid={windows[m.id].trim() !== '' && positiveInt(windows[m.id]) === null}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {error && <div className="message-error">{error}</div>}
      <footer>
        <button
          onClick={() => run(onReset)}
          disabled={saving || settings.overridden.length === 0}
          title="Drop every saved setting and use config.yaml's values"
        >
          Reset to config.yaml
        </button>
        <span className="spacer" />
        <button onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="primary" onClick={() => run(() => onSave(changes()))} disabled={saving || invalid}>
          Save
        </button>
      </footer>
    </dialog>
  )
}
