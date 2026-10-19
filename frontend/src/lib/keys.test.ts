import { describe, expect, it } from 'vitest'
import { isSendKey, type SendKeyEvent } from './keys'

const key = (extra: Partial<SendKeyEvent> = {}): SendKeyEvent => ({
  key: 'Enter',
  shiftKey: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  isComposing: false,
  ...extra,
})

describe('isSendKey', () => {
  it('sends on Ctrl/Cmd+Enter whatever the setting', () => {
    for (const sendWithEnter of [false, true]) {
      expect(isSendKey(key({ ctrlKey: true }), sendWithEnter)).toBe(true)
      expect(isSendKey(key({ metaKey: true }), sendWithEnter)).toBe(true)
    }
  })

  it('sends on plain Enter only when enabled', () => {
    expect(isSendKey(key(), false)).toBe(false)
    expect(isSendKey(key(), true)).toBe(true)
  })

  it('keeps Shift+Enter and Alt+Enter as new lines', () => {
    expect(isSendKey(key({ shiftKey: true }), true)).toBe(false)
    expect(isSendKey(key({ altKey: true }), true)).toBe(false)
  })

  it('ignores Enter while an IME is composing, and other keys', () => {
    expect(isSendKey(key({ isComposing: true }), true)).toBe(false)
    expect(isSendKey(key({ isComposing: true, ctrlKey: true }), true)).toBe(false)
    expect(isSendKey(key({ key: 'a', ctrlKey: true }), true)).toBe(false)
  })
})
