// @vitest-environment happy-dom
import { renderToStaticMarkup } from 'react-dom/server'
import Markdown from 'react-markdown'
import { describe, expect, it } from 'vitest'
import {
  findSections,
  locateAnchor,
  rangeToAnchor,
  rehypeSourceMap,
  sectionOf,
  sectionSpan,
  sourceToRange,
  type Highlight,
} from './anchors'

function render(source: string, highlights: Highlight[] = []): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = renderToStaticMarkup(
    <Markdown rehypePlugins={[[rehypeSourceMap, { source, highlights }]]}>{source}</Markdown>,
  )
  document.body.replaceChildren(root)
  return root
}

/** A Range over the first occurrence of `needle` in the rendered text of `root`. */
function selectRendered(root: HTMLElement, needle: string): Range {
  const nodes: Text[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text)
  const full = nodes.map((n) => n.data).join('')
  const at = full.indexOf(needle)
  if (at === -1) throw new Error(`"${needle}" not rendered in: ${JSON.stringify(full)}`)
  const locate = (index: number, preferNext: boolean): [Text, number] => {
    let seen = 0
    for (const n of nodes) {
      const len = n.data.length
      if (index < seen + len || (!preferNext && index === seen + len)) return [n, index - seen]
      seen += len
    }
    const last = nodes[nodes.length - 1]
    return [last, last.data.length]
  }
  const range = document.createRange()
  range.setStart(...locate(at, true))
  range.setEnd(...locate(at + needle.length, false))
  return range
}

function anchorFor(source: string, needle: string) {
  const root = render(source)
  return rangeToAnchor(root, selectRendered(root, needle), source)
}

describe('rangeToAnchor', () => {
  it('maps a plain selection to source offsets', () => {
    const source = 'Hello brave new world'
    expect(anchorFor(source, 'brave new')).toEqual({ start: 6, end: 15, quote: 'brave new' })
  })

  it('includes markdown syntax between the endpoints', () => {
    const source = 'Use **bold** text here'
    expect(anchorFor(source, 'bold text')).toEqual({ start: 6, end: 17, quote: 'bold** text' })
  })

  it('maps text inside inline code', () => {
    const source = 'Run `npm install` now'
    expect(anchorFor(source, 'install')).toEqual({ start: 9, end: 16, quote: 'install' })
  })

  it('maps text inside a fenced code block', () => {
    const source = 'Text\n\n```py\nx = 1\ny = 2\n```\n'
    const anchor = anchorFor(source, '1\ny')
    expect(anchor?.quote).toBe('1\ny')
    expect(source.slice(anchor!.start, anchor!.end)).toBe('1\ny')
  })

  it('maps list items whose continuation lines lose indentation when rendered', () => {
    const source = '- first item\n  continues here\n- second'
    expect(anchorFor(source, 'item\ncontinues')?.quote).toBe('item\n  continues')
  })

  it('maps selections in later blocks to the right occurrence', () => {
    const source = 'same words\n\nsame words'
    const root = render(source)
    const second = root.querySelectorAll('p')[1].firstChild!.firstChild as Text
    const range = document.createRange()
    range.setStart(second, 0)
    range.setEnd(second, 4)
    expect(rangeToAnchor(root, range, source)).toEqual({ start: 12, end: 16, quote: 'same' })
  })

  it('trims surrounding whitespace', () => {
    expect(anchorFor('Hello brave new world', ' brave ')?.quote).toBe('brave')
  })

  it('returns null outside the root or for unmappable text', () => {
    const root = render('Hello')
    const outside = document.createElement('p')
    outside.textContent = 'elsewhere'
    document.body.append(outside)
    const range = document.createRange()
    range.selectNodeContents(outside)
    expect(rangeToAnchor(root, range, 'Hello')).toBeNull()

    // Entities render differently from their source, so that text is unmapped.
    expect(anchorFor('a &amp; b', '& b')).toBeNull()
  })

  it('ignores badge text inside the selection', () => {
    const source = 'Hello brave new world'
    const root = render(source, [{ start: 6, end: 11, threadId: 1, count: 3 }])
    expect(root.textContent).toBe('Hello brave3 new world')
    expect(rangeToAnchor(root, selectRendered(root, 'brave3 new'), source)?.quote).toBe(
      'brave new',
    )
  })
})

describe('rehypeSourceMap highlights', () => {
  it('wraps the anchored text and adds a badge after it', () => {
    const root = render('Hello brave new world', [{ start: 6, end: 15, threadId: 7, count: 2 }])
    const marks = [...root.querySelectorAll('.anchor')]
    expect(marks.map((m) => m.textContent).join('')).toBe('brave new')
    expect(marks.every((m) => m.getAttribute('data-threads') === '7')).toBe(true)
    const badge = root.querySelector('.anchor-badge')!
    expect(badge.textContent).toBe('2')
    expect(badge.getAttribute('data-thread')).toBe('7')
    expect(badge.previousSibling).toBe(marks[marks.length - 1])
  })

  it('highlights across inline markup', () => {
    const root = render('Use **bold** text here', [{ start: 6, end: 17, threadId: 1, count: 1 }])
    expect([...root.querySelectorAll('.anchor')].map((m) => m.textContent)).toEqual([
      'bold',
      ' text',
    ])
  })

  it('splits overlapping highlights and lists every thread', () => {
    const root = render('abcdefghij', [
      { start: 0, end: 6, threadId: 1, count: 1 },
      { start: 4, end: 10, threadId: 2, count: 1 },
    ])
    expect(
      [...root.querySelectorAll('.anchor')].map((m) => [m.textContent, m.getAttribute('data-threads')]),
    ).toEqual([
      ['abcd', '1'],
      ['ef', '1 2'],
      ['ghij', '2'],
    ])
  })

  it('mutes highlights whose threads are all resolved or merged', () => {
    const root = render('abcdefghij', [
      { start: 0, end: 6, threadId: 1, count: 2, closed: true },
      { start: 4, end: 10, threadId: 2, count: 1 },
    ])
    expect(
      [...root.querySelectorAll('.anchor')].map((m) => [m.textContent, m.classList.contains('anchor-closed')]),
    ).toEqual([
      ['abcd', true],
      ['ef', false],
      ['ghij', false],
    ])
    const badges = [...root.querySelectorAll('.anchor-badge')]
    expect(badges.map((b) => b.classList.contains('anchor-closed'))).toEqual([true, false])
  })

  it('draws drafts without a badge', () => {
    const root = render('Hello world', [{ start: 6, end: 11, threadId: null, count: 0 }])
    expect(root.querySelector('.anchor-draft')?.textContent).toBe('world')
    expect(root.querySelector('.anchor-badge')).toBeNull()
  })

  it('draws sent comments with their key and text, without a badge', () => {
    const comment = { key: '5-0', text: 'Why?' }
    const root = render('Hello world', [{ start: 6, end: 11, threadId: null, count: 0, comment }])
    const mark = root.querySelector('.anchor-comment')!
    expect(mark.textContent).toBe('world')
    expect(mark.classList.contains('anchor-draft')).toBe(false)
    expect(mark.getAttribute('data-comment')).toBe('5-0')
    expect(mark.getAttribute('title')).toBe('Why?')
    expect(mark.hasAttribute('data-threads')).toBe(false)
    expect(root.querySelector('.anchor-badge')).toBeNull()
  })
})

const LABELLED = '## §1 Install\n\nRun it.\n\n### §2. Configure\n\n- **§3** Check\n- plain item'

describe('section labels', () => {
  it('finds labels at the start of lines, headings and list items', () => {
    expect(findSections(LABELLED)).toEqual([
      { label: '§1', start: 3, end: 5 },
      { label: '§2', start: 28, end: 31 },
      { label: '§3', start: 47, end: 49 },
    ])
  })

  it('ignores § in the middle of a line', () => {
    expect(findSections('See §2 of the spec.\nAs §3 says')).toEqual([])
  })

  it('names the section a span falls in, or none if it crosses a label', () => {
    const at = (needle: string) => LABELLED.indexOf(needle)
    expect(sectionOf(LABELLED, at('Run'), at('Run') + 3)).toBe('§1')
    expect(sectionOf(LABELLED, at('§1'), at('Install') + 7)).toBe('§1')
    expect(sectionOf(LABELLED, at('Configure'), at('Configure') + 9)).toBe('§2')
    expect(sectionOf(LABELLED, at('Run'), at('Configure'))).toBeNull()
    expect(sectionOf('Intro.\n\n§1 Body', 0, 5)).toBeNull()
  })

  it('spans a section from its label to the next one', () => {
    expect(LABELLED.slice(...sectionSpan(LABELLED, 3))).toBe('§1 Install\n\nRun it.')
    expect(LABELLED.slice(...sectionSpan(LABELLED, 47))).toBe('§3** Check\n- plain item')
  })

  it('renders labels as gutter spans that stay part of the source map', () => {
    const root = render(LABELLED)
    const labels = [...root.querySelectorAll('.section-label')]
    expect(labels.map((l) => [l.textContent, l.getAttribute('data-section-start')])).toEqual([
      ['§1', '3'],
      ['§2.', '28'],
      ['§3', '47'],
    ])
    expect(rangeToAnchor(root, selectRendered(root, '§1 Install'), LABELLED)?.quote).toBe(
      '§1 Install',
    )
  })
})

describe('sourceToRange', () => {
  it('selects the rendered text of a source span', () => {
    const source = 'Use **bold** text\n\nNext para'
    const root = render(source)
    const range = sourceToRange(root, 6, source.indexOf('Next') + 4)!
    expect(range.toString()).toBe('bold text\nNext')
    expect(rangeToAnchor(root, range, source)?.quote).toBe('bold** text\n\nNext')
  })

  it('returns null when nothing in the span is rendered', () => {
    expect(sourceToRange(render('Hello'), 10, 12)).toBeNull()
  })
})

describe('locateAnchor', () => {
  it('uses stored offsets when the quote is still there', () => {
    expect(locateAnchor('one two one', { quote: 'one', start: 8, end: 11 })).toEqual([8, 11])
  })

  it('falls back to the occurrence nearest the stored offsets', () => {
    const revised = 'Intro added. one two one'
    expect(locateAnchor(revised, { quote: 'one', start: 8, end: 11 })).toEqual([13, 16])
  })

  it('returns null when the quote is gone (detached)', () => {
    expect(locateAnchor('rewritten', { quote: 'one', start: 0, end: 3 })).toBeNull()
  })

  it('re-locates a lightly edited quote by fuzzy match', () => {
    const quote = 'Run python -m venv env to create it'
    const revised = 'Intro.\n\nRun python3 -m venv .env to create it, then activate.'
    const span = locateAnchor(revised, { quote, start: 0, end: quote.length })!
    expect(revised.slice(...span)).toBe('Run python3 -m venv .env to create it')
  })

  it('prefers the fuzzy match nearest the stored offsets', () => {
    const quote = 'install the package now'
    const revised = 'install the packages now. Later: install the packages now.'
    const span = locateAnchor(revised, { quote, start: 34, end: 34 + quote.length })!
    expect(span[0]).toBe(33)
  })

  it('detaches short or heavily rewritten quotes instead of guessing', () => {
    expect(locateAnchor('Hello Earth', { quote: 'world', start: 6, end: 11 })).toBeNull()
    const quote = 'python -m venv <folder-name>'
    expect(locateAnchor('uv venv <dir>', { quote, start: 0, end: quote.length })).toBeNull()
  })
})
