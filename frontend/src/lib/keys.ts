export interface SendKeyEvent {
  key: string
  shiftKey: boolean
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  isComposing: boolean // an IME is open: its Enter confirms a candidate
}

/** Whether a keypress in the composer sends. Ctrl/Cmd+Enter always does. */
export function isSendKey(e: SendKeyEvent, sendWithEnter: boolean): boolean {
  if (e.key !== 'Enter' || e.isComposing) return false
  return e.ctrlKey || e.metaKey || (sendWithEnter && !e.shiftKey && !e.altKey)
}
