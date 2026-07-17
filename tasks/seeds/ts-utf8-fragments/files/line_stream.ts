// Incremental line reader for the device console tail. The gateway forwards
// whatever bytes the serial bridge had buffered, so chunk boundaries land
// anywhere — mid-line, mid-escape, wherever. push() returns every line that
// completed with this chunk; flush() is called once at end of stream and
// returns whatever the stream still owes. Line numbers are 1-based and refer
// to the position in the overall stream, not the chunk.

import { Buffer } from 'node:buffer';

export interface ConsoleLine {
  line: number;
  text: string;
}

export class LineStream {
  private pending = '';
  private lineNo = 0;

  push(chunk: Uint8Array): ConsoleLine[] {
    this.pending += Buffer.from(chunk).toString('utf8');
    const out: ConsoleLine[] = [];
    let cut: number;
    while ((cut = this.pending.indexOf('\n')) !== -1) {
      const text = this.pending.slice(0, cut);
      this.pending = this.pending.slice(cut + 1);
      out.push({ line: ++this.lineNo, text });
    }
    return out;
  }

  flush(): ConsoleLine[] {
    const out = this.push(new Uint8Array(0));
    this.pending = '';
    return out;
  }
}
