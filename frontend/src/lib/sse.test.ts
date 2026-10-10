import { describe, expect, it } from 'vitest'
import { SSEParser, readSSE } from './sse'

describe('SSEParser', () => {
  it('parses complete events', () => {
    const parser = new SSEParser()
    expect(parser.feed('event: delta\ndata: {"text":"hi"}\n\nevent: done\ndata: {}\n\n')).toEqual([
      { event: 'delta', data: '{"text":"hi"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('buffers events split across chunks', () => {
    const parser = new SSEParser()
    expect(parser.feed('event: del')).toEqual([])
    expect(parser.feed('ta\ndata: x')).toEqual([])
    expect(parser.feed('\n\n')).toEqual([{ event: 'delta', data: 'x' }])
  })

  it('handles CRLF, comments, multi-line data and a default event name', () => {
    const parser = new SSEParser()
    expect(parser.feed(': keepalive\r\ndata: a\r\ndata: b\r\n\r\n')).toEqual([
      { event: 'message', data: 'a\nb' },
    ])
  })

  it('treats a CRLF split across chunks as one line break', () => {
    const parser = new SSEParser()
    expect(parser.feed('data: x\r')).toEqual([])
    expect(parser.feed('\ndata: y\r\n\r\n')).toEqual([{ event: 'message', data: 'x\ny' }])
  })

  it('keeps only the first space after the colon', () => {
    expect(new SSEParser().feed('data:  two spaces\n\n')).toEqual([
      { event: 'message', data: ' two spaces' },
    ])
  })
})

describe('readSSE', () => {
  it('decodes a byte stream into events', async () => {
    const bytes = new TextEncoder().encode('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n')
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 7))
        controller.enqueue(bytes.slice(7))
        controller.close()
      },
    })
    const events = []
    for await (const e of readSSE(body)) events.push(e)
    expect(events).toEqual([
      { event: 'a', data: '1' },
      { event: 'b', data: '2' },
    ])
  })
})
