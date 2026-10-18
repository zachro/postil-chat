// Line diff for reviewing a revision: a longest-common-subsequence over lines,
// which is plenty for chat replies of a few hundred lines.

export type DiffOp = { type: 'same' | 'del' | 'add'; text: string }
export type DiffItem = DiffOp | { type: 'skip'; count: number }

export function diffLines(before: string, after: string): DiffOp[] {
  const a = before.split('\n')
  const b = after.split('\n')
  // lcs[i][j]: length of the LCS of a[i..] and b[j..]
  const lcs = Array.from({ length: a.length + 1 }, () => new Int32Array(b.length + 1))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  const ops: DiffOp[] = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      ops.push({ type: 'same', text: a[i++] })
      j++
    } else if (j >= b.length || (i < a.length && lcs[i + 1][j] >= lcs[i][j + 1])) {
      ops.push({ type: 'del', text: a[i++] })
    } else {
      ops.push({ type: 'add', text: b[j++] })
    }
  }
  return ops
}

/** Collapse unchanged runs to a `skip` marker, keeping `context` lines next to each change. */
export function withContext(ops: DiffOp[], context: number): DiffItem[] {
  const keep = ops.map((op) => op.type !== 'same')
  ops.forEach((op, i) => {
    if (op.type === 'same') return
    for (let k = Math.max(0, i - context); k <= Math.min(ops.length - 1, i + context); k++) keep[k] = true
  })
  const items: DiffItem[] = []
  let hidden: DiffOp[] = []
  const flush = () => {
    if (hidden.length > 1) items.push({ type: 'skip', count: hidden.length })
    else items.push(...hidden)
    hidden = []
  }
  ops.forEach((op, i) => {
    if (keep[i]) {
      flush()
      items.push(op)
    } else {
      hidden.push(op)
    }
  })
  flush()
  return items
}
