// Server-Sent Events over fetch. EventSource only supports GET, but our
// streams start from a POST, so we parse the response body ourselves.

export interface SSEEvent {
  event: string
  data: string
}

/** Incremental parser: feed it text chunks, get back complete events. */
export class SSEParser {
  private buffer = ''
  private pendingCR = false // a chunk ended in '\r' that may be half of '\r\n'

  feed(chunk: string): SSEEvent[] {
    let text = (this.pendingCR ? '\r' : '') + chunk
    this.pendingCR = text.endsWith('\r')
    if (this.pendingCR) text = text.slice(0, -1)
    this.buffer += text.replace(/\r\n?/g, '\n')
    const events: SSEEvent[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      const event = parseBlock(block)
      if (event) events.push(event)
    }
    return events
  }
}

function parseBlock(block: string): SSEEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line === '' || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  return data.length ? { event, data: data.join('\n') } : null
}

export async function* readSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
  const parser = new SSEParser()
  const decoder = new TextDecoder()
  const reader = body.getReader()
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return
      yield* parser.feed(decoder.decode(value, { stream: true }))
    }
  } finally {
    reader.releaseLock()
  }
}
