// Client for the depth-of-book feed. The transport hands us a raw socket
// that speaks in events: 'envelope' for data frames, 'fault' for recoverable
// transport errors, 'closed' when the peer hangs up. The reconnect supervisor
// calls connect()/disconnect() around every network blip, so those two have
// to be safe to call in any order, any number of times.

import type { EventEmitter } from 'node:events';

export interface Envelope {
  seq: number;
  body: string;
}

export interface FeedSink {
  onEnvelope(env: Envelope): void;
  onFault(message: string): void;
  onClosed(): void;
}

export class FeedClient {
  private socket: EventEmitter;
  private sink: FeedSink;
  private connected = false;

  constructor(socket: EventEmitter, sink: FeedSink) {
    this.socket = socket;
    this.sink = sink;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  connect(): void {
    this.socket.on('envelope', (env: Envelope) => this.handleEnvelope(env));
    this.socket.on('fault', (err: Error) => this.handleFault(err));
    this.socket.on('closed', () => this.handleClosed());
    this.connected = true;
  }

  disconnect(): void {
    this.socket.removeListener('envelope', this.handleEnvelope);
    this.socket.removeListener('fault', this.handleFault);
    this.socket.removeListener('closed', this.handleClosed);
    this.connected = false;
  }

  private handleEnvelope(env: Envelope): void {
    this.sink.onEnvelope(env);
  }

  private handleFault(err: Error): void {
    this.sink.onFault(err.message);
  }

  private handleClosed(): void {
    this.connected = false;
    this.sink.onClosed();
  }
}
