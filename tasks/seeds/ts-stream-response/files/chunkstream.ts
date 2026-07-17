// Chunked HTTP/1.1 response writer for the audit-log export endpoints.
// Wraps a raw connection (anything with write/close) and streams NDJSON
// records to the client without buffering the whole export in memory.

export interface Sink {
  write(data: Uint8Array): void;
  close(): void;
}

/** Payload bytes per chunk frame; longer records are split across frames. */
export const FRAME_BYTES = 64;

const encoder = new TextEncoder();

const STATUS_TEXT: Record<number, string> = {
  200: 'OK',
  500: 'Internal Server Error',
};

interface Head {
  status: number;
  headers: Record<string, string>;
}

export class ChunkedResponse {
  private sink: Sink;
  private head: Head | null;
  private state: 'idle' | 'body' | 'done';

  constructor(sink: Sink, status = 200, headers: Record<string, string> = {}) {
    this.sink = sink;
    this.state = 'idle';
    this.head = {
      status,
      headers: {
        'content-type': 'application/x-ndjson',
        'transfer-encoding': 'chunked',
        ...headers,
      },
    };
  }

  /** Add or replace a response header; only legal before anything is sent. */
  setHeader(name: string, value: string): void {
    if (!this.head) throw new Error('headers already sent');
    this.head.headers[name.toLowerCase()] = value;
  }

  private flushHead(): void {
    if (!this.head) return;
    const { status, headers } = this.head;
    this.head = null;
    const reason = STATUS_TEXT[status] ?? '';
    let raw = `HTTP/1.1 ${status} ${reason}\r\n`;
    for (const [name, value] of Object.entries(headers)) {
      raw += `${name}: ${value}\r\n`;
    }
    raw += '\r\n';
    this.sink.write(encoder.encode(raw));
    this.state = 'body';
  }

  /** Stream one NDJSON record; the newline is added here. */
  writeLine(line: string): void {
    if (this.state === 'done') throw new Error('response already ended');
    this.flushHead();
    const payload = encoder.encode(line + '\n');
    for (let offset = 0; offset <= payload.length; offset += FRAME_BYTES) {
      const part = payload.subarray(offset, Math.min(offset + FRAME_BYTES, payload.length));
      this.frame(part);
    }
  }

  private frame(part: Uint8Array): void {
    this.sink.write(encoder.encode(part.length.toString(16) + '\r\n'));
    this.sink.write(part);
    this.sink.write(encoder.encode('\r\n'));
  }

  /** Finish the body with the terminal chunk and release the connection. */
  end(): void {
    if (this.state === 'done') return;
    this.flushHead();
    this.sink.write(encoder.encode('0\r\n\r\n'));
    this.state = 'done';
    this.sink.close();
  }

  /**
   * Report a failure to the client and finish the response. Exports that
   * have not produced output yet get a complete 500; an export already on
   * the wire is finished with a trailing {"error": reason} record.
   */
  fail(reason: string): void {
    if (this.state === 'done') return;
    const body = encoder.encode(JSON.stringify({ error: reason }) + '\n');
    this.head = {
      status: 500,
      headers: {
        'content-type': 'application/json',
        'content-length': String(body.length),
      },
    };
    this.flushHead();
    this.sink.write(body);
    this.state = 'done';
    this.sink.close();
  }
}

/** Drive a whole export: pull records from the source, stream each one. */
export async function streamLines(
  res: ChunkedResponse,
  source: AsyncIterable<string>,
): Promise<void> {
  try {
    for await (const line of source) {
      res.writeLine(line);
    }
    res.end();
  } catch (err) {
    res.fail(err instanceof Error ? err.message : String(err));
  }
}
