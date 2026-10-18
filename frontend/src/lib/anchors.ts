// Anchors are offsets into a message's raw markdown source, never into the
// rendered DOM. Two pieces connect the two:
//
// - rehypeSourceMap: a rehype plugin that wraps every rendered text run whose
//   text appears verbatim in the source in <span data-s={sourceOffset}>, and
//   splits those runs to draw highlights (and reply-count badges) for anchors.
// - rangeToAnchor: maps a DOM selection back to source offsets via those spans
//   (sourceToRange goes the other way).
//
// Section labels (§1, §2, ...) at the start of a line are found in the source
// and rendered as gutter spans; they are an optimization only, so an anchor
// without one still works by its quote.
//
// Text that cannot be found verbatim in the source (e.g. HTML entities) is left
// unmapped: it is not highlightable, and selections simply skip over it.

import type { Element, ElementContent, Root, RootContent, Text } from 'hast'

export interface SourceAnchor {
  start: number
  end: number
  quote: string
  section?: string | null // label of the section the span falls in, if any
}

/**
 * A source span to highlight. threadId null marks a draft (no badge yet) or,
 * with `comment`, a sent multi-anchor comment; closed marks a resolved or merged
 * thread (drawn muted).
 */
export interface Highlight {
  start: number
  end: number
  threadId: number | null
  count: number
  closed?: boolean
  comment?: { key: string; text: string }
}

/** Key shared by a sent comment's card (in the user message) and its highlight. */
export const commentKey = (messageId: number, index: number) => `${messageId}-${index}`

/** A section label in the source: `label` is e.g. "§2"; [start, end) also covers a trailing "." ":" or ")". */
export interface SectionLabel {
  label: string
  start: number
  end: number
}

// Line start, then any heading/list/quote markers and an opening emphasis marker.
const SECTION_LINE = /^[ \t]*(?:(?:#{1,6}|[-*+]|\d+[.)]|>)[ \t]+)*(?:\*\*|__|\*|_)?(§\d+)[.:)]?/gm

export function findSections(source: string): SectionLabel[] {
  return [...source.matchAll(SECTION_LINE)].map((m) => {
    const start = m.index + m[0].length - m[0].slice(m[0].indexOf('§')).length
    return { label: m[1], start, end: m.index + m[0].length }
  })
}

/** The section a source span falls in: the last label at or before it, unless it crosses another. */
export function sectionOf(source: string, start: number, end: number): string | null {
  const labels = findSections(source)
  if (labels.some((l) => l.start > start && l.start < end)) return null
  return labels.findLast((l) => l.start <= start)?.label ?? null
}

/** The source span of the section whose label starts at `labelStart`: up to the next label's line. */
export function sectionSpan(source: string, labelStart: number): Span {
  const next = findSections(source).find((l) => l.start > labelStart)
  let end = next ? source.lastIndexOf('\n', next.start) + 1 : source.length
  while (end > labelStart && /\s/.test(source[end - 1])) end--
  return [labelStart, end]
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
  return (tree: Root) =>
    annotate(tree, options.source, options.highlights ?? [], findSections(options.source))
}

type Parent = Root | Element

interface Piece {
  node: Element
  parent: Parent
  start: number
  end: number
}

function annotate(tree: Root, source: string, highlights: Highlight[], labels: SectionLabel[]): void {
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
        for (const piece of split(segment.text, segment.start, highlights, labels)) {
          const node = sourceSpan(piece)
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
    siblings.splice(siblings.indexOf(last.node) + 1, 0, badge(h))
  }
}

interface SplitPiece {
  text: string
  start: number
  highlights: Highlight[]
  label: SectionLabel | undefined
}

/** Cut a mapped run at every highlight and section-label boundary inside it. */
function split(value: string, start: number, highlights: Highlight[], labels: SectionLabel[]): SplitPiece[] {
  const end = start + value.length
  const cuts = new Set([start, end])
  for (const { start: s, end: e } of [...highlights, ...labels]) {
    if (s > start && s < end) cuts.add(s)
    if (e > start && e < end) cuts.add(e)
  }
  const sorted = [...cuts].sort((a, b) => a - b)
  return sorted.slice(0, -1).map((from, i) => {
    const to = sorted[i + 1]
    return {
      text: value.slice(from - start, to - start),
      start: from,
      highlights: highlights.filter((h) => h.start < to && h.end > from),
      label: labels.find((l) => l.start <= from && l.end >= to),
    }
  })
}

function sourceSpan({ text: value, start, highlights, label }: SplitPiece): Element {
  const properties: Element['properties'] = { dataS: start }
  const className = []
  if (label) {
    className.push('section-label')
    properties.dataSectionStart = label.start
    properties.title = `Select ${label.label}`
  }
  if (highlights.length) {
    className.push('anchor')
    const comments = highlights.filter((h) => h.comment)
    const threadIds = highlights.flatMap((h) => (h.threadId === null ? [] : [h.threadId]))
    if (comments.length === highlights.length) className.push('anchor-comment')
    else if (!threadIds.length) className.push('anchor-draft')
    else if (highlights.every((h) => h.closed)) className.push('anchor-closed')
    if (threadIds.length) properties.dataThreads = threadIds.join(' ')
    if (comments.length) {
      properties.dataComment = comments[0].comment!.key
      if (!label) properties.title = comments.map((h) => h.comment!.text).join('\n\n')
    }
  }
  if (className.length) properties.className = className
  return { type: 'element', tagName: 'span', properties, children: [text(value)] }
}

function badge({ threadId, count, closed }: Highlight): Element {
  return {
    type: 'element',
    tagName: 'sup',
    properties: {
      className: closed ? ['anchor-badge', 'anchor-closed'] : ['anchor-badge'],
      dataThread: threadId,
      title: 'Open side thread',
    },
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

/** A DOM range over the rendered text of the source span [start, end), or null if none of it is rendered. */
export function sourceToRange(root: Node, start: number, end: number): Range | null {
  const doc = root.ownerDocument ?? document
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let range: Range | null = null
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const offset = (node.parentNode as HTMLElement | null)?.dataset?.s
    if (offset === undefined) continue
    const from = Number(offset)
    const to = from + (node.textContent?.length ?? 0)
    if (to <= start || from >= end) continue
    if (!range) {
      range = doc.createRange()
      range.setStart(node, Math.max(start - from, 0))
    }
    range.setEnd(node, Math.min(end, to) - from)
  }
  return range
}

/**
 * Find an anchor's span in (possibly revised) content: its stored offsets if the
 * quote is still there, else the exact occurrence nearest them, else a close
 * fuzzy match (see fuzzyFind), else null: the anchor is detached.
 */
export function locateAnchor(source: string, anchor: SourceAnchor): Span | null {
  const { quote, start } = anchor
  if (source.slice(start, start + quote.length) === quote) return [start, start + quote.length]
  let best: number | null = null
  for (let at = source.indexOf(quote); at !== -1; at = source.indexOf(quote, at + 1)) {
    if (best === null || Math.abs(at - start) < Math.abs(best - start)) best = at
  }
  if (best !== null) return [best, best + quote.length]
  return cachedFuzzyFind(source, quote, start)
}

// Fuzzy re-anchoring only for quotes long enough that a few edits cannot land on
// unrelated text, and short enough that the O(source × quote) search stays cheap.
const FUZZY_MIN_CHARS = 12
const FUZZY_MAX_CHARS = 1000
const FUZZY_MAX_ERROR_RATE = 0.2
const FUZZY_CACHE_SIZE = 200

const fuzzyCache = new Map<string, Span | null>()

function cachedFuzzyFind(source: string, quote: string, near: number): Span | null {
  const key = `${near}\u0000${quote}\u0000${source}`
  if (!fuzzyCache.has(key)) {
    if (fuzzyCache.size >= FUZZY_CACHE_SIZE) fuzzyCache.clear()
    fuzzyCache.set(key, fuzzyFind(source, quote, near))
  }
  return fuzzyCache.get(key)!
}

/**
 * The span of `source` with the fewest edits (Levenshtein) from `quote`, if that
 * is at most 20% of the quote's length; ties go to the span nearest `near`.
 * Approximate substring search: dynamic programming over the quote, where a
 * match may start at any source position.
 */
export function fuzzyFind(source: string, quote: string, near: number): Span | null {
  const m = quote.length
  if (m < FUZZY_MIN_CHARS || m > FUZZY_MAX_CHARS) return null
  const limit = Math.floor(m * FUZZY_MAX_ERROR_RATE)
  // For the current source position j: dist[i] = edits to match quote[0..i) ending
  // at j, and from[i] = where that match starts in the source.
  let dist = Int32Array.from({ length: m + 1 }, (_, i) => i)
  let from = new Int32Array(m + 1)
  let nextDist = new Int32Array(m + 1)
  let nextFrom = new Int32Array(m + 1)
  let best: { cost: number; span: Span } | null = null
  for (let j = 1; j <= source.length; j++) {
    nextDist[0] = 0
    nextFrom[0] = j
    for (let i = 1; i <= m; i++) {
      const substitute = dist[i - 1] + (quote[i - 1] === source[j - 1] ? 0 : 1)
      const extraSourceChar = dist[i] + 1
      const missingQuoteChar = nextDist[i - 1] + 1
      if (substitute <= extraSourceChar && substitute <= missingQuoteChar) {
        nextDist[i] = substitute
        nextFrom[i] = from[i - 1]
      } else if (extraSourceChar <= missingQuoteChar) {
        nextDist[i] = extraSourceChar
        nextFrom[i] = from[i]
      } else {
        nextDist[i] = missingQuoteChar
        nextFrom[i] = nextFrom[i - 1]
      }
    }
    const cost = nextDist[m]
    if (cost <= limit) {
      const span: Span = [nextFrom[m], j]
      const closer = best && Math.abs(span[0] - near) < Math.abs(best.span[0] - near)
      if (!best || cost < best.cost || (cost === best.cost && closer)) best = { cost, span }
    }
    ;[dist, nextDist] = [nextDist, dist]
    ;[from, nextFrom] = [nextFrom, from]
  }
  return best?.span ?? null
}
