// Anchors are offsets into a message's raw markdown source, never into the
// rendered DOM. Two pieces connect the two:
//
// - rehypeSourceMap: a rehype plugin that wraps every rendered text run whose
//   text appears verbatim in the source in <span data-s={sourceOffset}>, and
//   splits those runs to draw highlights (and reply-count badges) for anchors.
// - rangeToAnchor: maps a DOM selection back to source offsets via those spans.
//
// Text that cannot be found verbatim in the source (e.g. HTML entities) is left
// unmapped: it is not highlightable, and selections simply skip over it.

import type { Element, ElementContent, Root, RootContent, Text } from 'hast'

export interface SourceAnchor {
  start: number
  end: number
  quote: string
}

/** A source span to highlight. threadId null marks a draft thread (no badge yet). */
export interface Highlight {
  start: number
  end: number
  threadId: number | null
  count: number
}

interface Segment {
  text: string
  start: number | null // source offset, or null if not found verbatim
}

type Span = [start: number, end: number]

/**
 * Map a rendered text value to source offsets. `exact` is set when the node's
 * own source range holds exactly this text; otherwise each line is searched for
 * within `range` (lines may lose indentation or markup when rendered).
 */
export function mapText(
  value: string,
  source: string,
  range: Span | null,
  exact: number | null,
  cursor: number,
): { segments: Segment[]; cursor: number } {
  if (exact !== null) return { segments: [{ text: value, start: exact }], cursor: exact + value.length }
  if (!range) return { segments: [{ text: value, start: null }], cursor }

  const segments: Segment[] = []
  let from = Math.max(range[0], cursor)
  value.split('\n').forEach((line, i) => {
    if (i > 0) segments.push({ text: '\n', start: null })
    if (!line) return
    const at = source.indexOf(line, from)
    if (at !== -1 && at + line.length <= range[1]) {
      segments.push({ text: line, start: at })
      from = at + line.length
    } else {
      segments.push({ text: line, start: null })
    }
  })
  return { segments, cursor: from }
}

export function rehypeSourceMap(options: { source: string; highlights?: Highlight[] }) {
  return (tree: Root) => annotate(tree, options.source, options.highlights ?? [])
}

type Parent = Root | Element

interface Piece {
  node: Element
  parent: Parent
  start: number
  end: number
}

function annotate(tree: Root, source: string, highlights: Highlight[]): void {
  const pieces: Piece[] = []
  let cursor = 0

  function visit(parent: Parent, range: Span | null): void {
    const out: (RootContent | ElementContent)[] = []
    for (const child of parent.children) {
      if (child.type !== 'text') {
        if (child.type === 'element') visit(child, offsets(child) ?? range)
        out.push(child)
        continue
      }
      const own = offsets(child)
      const exact = own && source.slice(own[0], own[1]) === child.value ? own[0] : null
      const mapped = mapText(child.value, source, own ?? range, exact, cursor)
      cursor = mapped.cursor
      for (const segment of mapped.segments) {
        if (segment.start === null) {
          out.push(text(segment.text))
          continue
        }
        for (const piece of splitByHighlights(segment.text, segment.start, highlights)) {
          const node = sourceSpan(piece.text, piece.start, piece.threads)
          out.push(node)
          pieces.push({ node, parent, start: piece.start, end: piece.start + piece.text.length })
        }
      }
    }
    parent.children = out as typeof parent.children
  }

  visit(tree, null)

  for (const h of highlights) {
    if (h.threadId === null) continue
    const last = pieces.findLast((p) => p.start < h.end && p.end > h.start)
    if (!last) continue
    const siblings = last.parent.children as (RootContent | ElementContent)[]
    siblings.splice(siblings.indexOf(last.node) + 1, 0, badge(h.threadId, h.count))
  }
}

function splitByHighlights(value: string, start: number, highlights: Highlight[]) {
  const end = start + value.length
  const cuts = new Set([start, end])
  for (const h of highlights) {
    if (h.start > start && h.start < end) cuts.add(h.start)
    if (h.end > start && h.end < end) cuts.add(h.end)
  }
  const sorted = [...cuts].sort((a, b) => a - b)
  return sorted.slice(0, -1).map((from, i) => {
    const to = sorted[i + 1]
    const threads = highlights.filter((h) => h.start < to && h.end > from).map((h) => h.threadId)
    return { text: value.slice(from - start, to - start), start: from, threads }
  })
}

function sourceSpan(value: string, start: number, threads: (number | null)[]): Element {
  const properties: Element['properties'] = { dataS: start }
  if (threads.length) {
    properties.className = threads.every((t) => t === null) ? ['anchor', 'anchor-draft'] : ['anchor']
    properties.dataThreads = threads.map((t) => t ?? 'draft').join(' ')
  }
  return { type: 'element', tagName: 'span', properties, children: [text(value)] }
}

function badge(threadId: number, count: number): Element {
  return {
    type: 'element',
    tagName: 'sup',
    properties: { className: ['anchor-badge'], dataThread: threadId, title: 'Open side thread' },
    children: [text(String(count))],
  }
}

const text = (value: string): Text => ({ type: 'text', value })

function offsets(node: { position?: { start: { offset?: number }; end: { offset?: number } } }) {
  const start = node.position?.start.offset
  const end = node.position?.end.offset
  return start === undefined || end === undefined ? null : ([start, end] as Span)
}

/** Map a DOM range inside `root` (rendered by rehypeSourceMap) to source offsets. */
export function rangeToAnchor(root: Node, range: Range, source: string): SourceAnchor | null {
  if (range.collapsed || !root.contains(range.commonAncestorContainer)) return null
  const doc = root.ownerDocument ?? document
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let start: number | null = null
  let end: number | null = null
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const offset = (node.parentNode as HTMLElement | null)?.dataset?.s
    if (offset === undefined || !range.intersectsNode(node)) continue
    let from = 0
    let to = node.textContent?.length ?? 0
    if (node === range.startContainer) from = range.startOffset
    if (node === range.endContainer) to = range.endOffset
    if (from >= to) continue
    start ??= Number(offset) + from
    end = Number(offset) + to
  }
  if (start === null || end === null) return null
  while (start < end && /\s/.test(source[start])) start++
  while (end > start && /\s/.test(source[end - 1])) end--
  return start < end ? { start, end, quote: source.slice(start, end) } : null
}

/**
 * Find an anchor's span in (possibly revised) content: its stored offsets if the
 * quote is still there, else the occurrence of the quote closest to them, else
 * null (the anchor is detached).
 */
export function locateAnchor(source: string, anchor: SourceAnchor): Span | null {
  const { quote, start } = anchor
  if (source.slice(start, start + quote.length) === quote) return [start, start + quote.length]
  let best: number | null = null
  for (let at = source.indexOf(quote); at !== -1; at = source.indexOf(quote, at + 1)) {
    if (best === null || Math.abs(at - start) < Math.abs(best - start)) best = at
  }
  return best === null ? null : [best, best + quote.length]
}
