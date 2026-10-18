import { describe, expect, it } from 'vitest'
import { diffLines, withContext } from './diff'

describe('diffLines', () => {
  it('marks removed and added lines and keeps the rest', () => {
    expect(diffLines('a\nb\nc', 'a\nB\nc\nd')).toEqual([
      { type: 'same', text: 'a' },
      { type: 'del', text: 'b' },
      { type: 'add', text: 'B' },
      { type: 'same', text: 'c' },
      { type: 'add', text: 'd' },
    ])
  })

  it('handles identical and empty texts', () => {
    expect(diffLines('x\ny', 'x\ny').every((op) => op.type === 'same')).toBe(true)
    expect(diffLines('', 'new')).toEqual([
      { type: 'del', text: '' },
      { type: 'add', text: 'new' },
    ])
  })

  it('finds the longest common run, not just a shared prefix', () => {
    const ops = diffLines('1\n2\n3\n4', '0\n2\n3\n5')
    expect(ops.filter((op) => op.type === 'same').map((op) => op.text)).toEqual(['2', '3'])
  })
})

describe('withContext', () => {
  it('collapses long unchanged runs, keeping lines around changes', () => {
    const before = ['1', '2', '3', '4', '5', '6', '7', '8'].join('\n')
    const after = before.replace('5', 'five')
    expect(withContext(diffLines(before, after), 1)).toEqual([
      { type: 'skip', count: 3 },
      { type: 'same', text: '4' },
      { type: 'del', text: '5' },
      { type: 'add', text: 'five' },
      { type: 'same', text: '6' },
      { type: 'skip', count: 2 },
    ])
  })

  it('does not collapse a run shorter than the marker it would need', () => {
    const ops = diffLines('1\n2\n3', '1\nx\n3')
    expect(withContext(ops, 1)).toEqual(ops)
  })
})
