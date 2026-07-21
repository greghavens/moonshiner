export interface Clock {
  now(): number;
}

export interface Job<T> {
  readonly id: string;
  readonly payload: T;
  readonly enqueuedAtMs: number;
}

export interface Delivery<T> extends Job<T> {
  readonly attempt: number;
  readonly receipt: string;
}

export interface BacklogTelemetry {
  readonly ready: number;
  readonly inFlight: number;
  readonly backlog: number;
  readonly oldestAgeMs: number;
}

export interface QueuePort<T> {
  reserve(nowMs: number): Delivery<T> | undefined;
  acknowledge(receipt: string): boolean;
  release(receipt: string): boolean;
  backlog(nowMs: number): BacklogTelemetry;
}

export interface SpanStart {
  readonly jobId: string;
  readonly attempt: number;
  readonly startedAtMs: number;
  readonly queueLagMs: number;
  readonly backlogAtStart: BacklogTelemetry;
}

export interface SpanEnd {
  readonly finishedAtMs: number;
  readonly outcome: "succeeded" | "failed";
  readonly error?: string;
  readonly backlogAtEnd: BacklogTelemetry;
}

export interface ActiveSpan {
  end(result: SpanEnd): void;
}

export interface SpanRecorder {
  startSpan(start: SpanStart): ActiveSpan;
}

export type RunResult =
  | { readonly outcome: "idle"; readonly backlog: BacklogTelemetry }
  | {
      readonly outcome: "succeeded";
      readonly jobId: string;
      readonly attempt: number;
      readonly backlog: BacklogTelemetry;
    }
  | {
      readonly outcome: "failed";
      readonly jobId: string;
      readonly attempt: number;
      readonly error: string;
      readonly backlog: BacklogTelemetry;
    };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class InMemoryQueue<T> implements QueuePort<T> {
  readonly #ready: Job<T>[] = [];
  readonly #inFlight = new Map<string, Delivery<T>>();
  readonly #attempts = new Map<string, number>();
  #nextReceipt = 1;

  enqueue(job: Job<T>): void {
    if (
      this.#ready.some((candidate) => candidate.id === job.id) ||
      [...this.#inFlight.values()].some((candidate) => candidate.id === job.id)
    ) {
      throw new Error(`job already queued: ${job.id}`);
    }
    this.#ready.push({ ...job });
  }

  reserve(_nowMs: number): Delivery<T> | undefined {
    const job = this.#ready.shift();
    if (job === undefined) {
      return undefined;
    }

    const attempt = (this.#attempts.get(job.id) ?? 0) + 1;
    this.#attempts.set(job.id, attempt);
    const receipt = `receipt-${this.#nextReceipt++}`;
    const delivery: Delivery<T> = { ...job, attempt, receipt };
    this.#inFlight.set(receipt, delivery);
    return delivery;
  }

  acknowledge(receipt: string): boolean {
    return this.#inFlight.delete(receipt);
  }

  release(receipt: string): boolean {
    const delivery = this.#inFlight.get(receipt);
    if (delivery === undefined) {
      return false;
    }

    this.#inFlight.delete(receipt);
    this.#ready.unshift({
      id: delivery.id,
      payload: delivery.payload,
      enqueuedAtMs: delivery.enqueuedAtMs,
    });
    return true;
  }

  backlog(nowMs: number): BacklogTelemetry {
    const unfinished = [
      ...this.#ready.map((job) => job.enqueuedAtMs),
      ...[...this.#inFlight.values()].map((job) => job.enqueuedAtMs),
    ];
    const oldestEnqueuedAt =
      unfinished.length === 0 ? undefined : Math.min(...unfinished);

    return {
      ready: this.#ready.length,
      inFlight: this.#inFlight.size,
      backlog: this.#ready.length + this.#inFlight.size,
      oldestAgeMs:
        oldestEnqueuedAt === undefined
          ? 0
          : Math.max(0, nowMs - oldestEnqueuedAt),
    };
  }
}

export interface CompletedSpan extends SpanStart, SpanEnd {}

export class InMemorySpanRecorder implements SpanRecorder {
  readonly #started: SpanStart[] = [];
  readonly #completed: CompletedSpan[] = [];

  startSpan(start: SpanStart): ActiveSpan {
    const capturedStart: SpanStart = {
      ...start,
      backlogAtStart: { ...start.backlogAtStart },
    };
    this.#started.push(capturedStart);

    let ended = false;
    return {
      end: (result) => {
        if (ended) {
          throw new Error(`span already ended for job ${start.jobId}`);
        }
        ended = true;
        this.#completed.push({
          ...capturedStart,
          ...result,
          backlogAtEnd: { ...result.backlogAtEnd },
        });
      },
    };
  }

  started(): readonly SpanStart[] {
    return this.#started.map((span) => ({
      ...span,
      backlogAtStart: { ...span.backlogAtStart },
    }));
  }

  completed(): readonly CompletedSpan[] {
    return this.#completed.map((span) => ({
      ...span,
      backlogAtStart: { ...span.backlogAtStart },
      backlogAtEnd: { ...span.backlogAtEnd },
    }));
  }
}

export class QueueWorker<T> {
  readonly #queue: QueuePort<T>;
  readonly #clock: Clock;
  readonly #spans: SpanRecorder;
  readonly #handler: (delivery: Delivery<T>) => Promise<void>;

  constructor(
    queue: QueuePort<T>,
    clock: Clock,
    spans: SpanRecorder,
    handler: (delivery: Delivery<T>) => Promise<void>,
  ) {
    this.#queue = queue;
    this.#clock = clock;
    this.#spans = spans;
    this.#handler = handler;
  }

  backlog(): BacklogTelemetry {
    return this.#queue.backlog(this.#clock.now());
  }

  async runOne(): Promise<RunResult> {
    const delivery = this.#queue.reserve(this.#clock.now());
    if (delivery === undefined) {
      return { outcome: "idle", backlog: this.backlog() };
    }

    this.#queue.acknowledge(delivery.receipt);

    const startedAtMs = this.#clock.now();
    const span = this.#spans.startSpan({
      jobId: delivery.id,
      attempt: delivery.attempt,
      startedAtMs,
      queueLagMs: Math.max(0, startedAtMs - delivery.enqueuedAtMs),
      backlogAtStart: this.#queue.backlog(startedAtMs),
    });

    try {
      await this.#handler(delivery);
      const finishedAtMs = this.#clock.now();
      const backlog = this.#queue.backlog(finishedAtMs);
      span.end({ finishedAtMs, outcome: "succeeded", backlogAtEnd: backlog });
      return {
        outcome: "succeeded",
        jobId: delivery.id,
        attempt: delivery.attempt,
        backlog,
      };
    } catch (error) {
      this.#queue.release(delivery.receipt);
      const message = errorMessage(error);
      const finishedAtMs = this.#clock.now();
      const backlog = this.#queue.backlog(finishedAtMs);
      span.end({
        finishedAtMs,
        outcome: "failed",
        error: message,
        backlogAtEnd: backlog,
      });
      return {
        outcome: "failed",
        jobId: delivery.id,
        attempt: delivery.attempt,
        error: message,
        backlog,
      };
    }
  }
}
