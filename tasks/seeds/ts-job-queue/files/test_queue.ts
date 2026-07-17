import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JobQueue } from './queue.ts';

test('claim on an empty queue returns null', () => {
  const q = new JobQueue();
  assert.equal(q.claim(), null);
});

test('ids are sequential per queue', () => {
  const q = new JobQueue();
  assert.equal(q.enqueue('a'), 'job-1');
  assert.equal(q.enqueue('b'), 'job-2');
  const other = new JobQueue();
  assert.equal(other.enqueue('c'), 'job-1');
});

test('jobs come out in enqueue order within one priority', () => {
  const q = new JobQueue();
  q.enqueue('first');
  q.enqueue('second');
  q.enqueue('third');
  const order = [q.claim(), q.claim(), q.claim()].map((j) => j!.type);
  assert.deepEqual(order, ['first', 'second', 'third']);
  assert.equal(q.claim(), null);
});

test('higher priority claims first even when enqueued later', () => {
  const q = new JobQueue();
  q.enqueue('routine');
  q.enqueue('urgent', null, { priority: 10 });
  q.enqueue('background', null, { priority: -5 });
  assert.equal(q.claim()!.type, 'urgent');
  assert.equal(q.claim()!.type, 'routine');
  assert.equal(q.claim()!.type, 'background');
});

test('within a priority the earlier readyAt wins over enqueue order', () => {
  const q = new JobQueue();
  q.enqueue('slow', null, { delayMs: 50 });
  q.enqueue('quick', null, { delayMs: 10 });
  q.advance(60);
  assert.equal(q.claim()!.type, 'quick');
  assert.equal(q.claim()!.type, 'slow');
});

test('delayed jobs are invisible until the clock reaches readyAt', () => {
  const q = new JobQueue();
  const id = q.enqueue('digest', { week: 12 }, { delayMs: 100 });
  assert.equal(q.claim(), null);
  q.advance(99);
  assert.equal(q.claim(), null);
  q.advance(1); // now exactly at readyAt
  const job = q.claim();
  assert.equal(job!.id, id);
  assert.deepEqual(job!.payload, { week: 12 });
});

test('claims report the attempt number and payload', () => {
  const q = new JobQueue();
  const id = q.enqueue('render', { page: 4 });
  const job = q.claim()!;
  assert.deepEqual(job, { id, type: 'render', payload: { page: 4 }, attempt: 1 });
});

test('failed jobs retry on the backoff schedule', () => {
  const q = new JobQueue();
  const id = q.enqueue('sync', null, { maxAttempts: 3, backoff: { baseMs: 100, factor: 2 } });

  assert.equal(q.claim()!.attempt, 1);
  q.fail(id, 'timeout 1'); // at t=0, next try at t=100
  assert.equal(q.claim(), null);
  q.advance(99);
  assert.equal(q.claim(), null);
  q.advance(1);
  assert.equal(q.claim()!.attempt, 2);

  q.fail(id, 'timeout 2'); // at t=100, next try at t=100+200=300
  q.advance(199);
  assert.equal(q.claim(), null);
  q.advance(1);
  assert.equal(q.claim()!.attempt, 3);

  q.fail(id, 'timeout 3'); // third of three attempts: dead
  assert.equal(q.claim(), null);
  assert.equal(q.job(id).state, 'dead');
  assert.deepEqual(q.deadLetters(), [
    {
      id,
      type: 'sync',
      payload: null,
      errors: [
        { attempt: 1, reason: 'timeout 1', at: 0 },
        { attempt: 2, reason: 'timeout 2', at: 100 },
        { attempt: 3, reason: 'timeout 3', at: 300 },
      ],
    },
  ]);
});

test('retry delay is capped by maxMs', () => {
  const q = new JobQueue();
  const id = q.enqueue('probe', null, { maxAttempts: 3, backoff: { baseMs: 100, factor: 10, maxMs: 250 } });
  q.claim();
  q.fail(id, 'down'); // next at 100
  q.advance(100);
  q.claim();
  q.fail(id, 'down again'); // raw 1000 capped to 250 -> ready at t=350
  q.advance(249);
  assert.equal(q.claim(), null);
  q.advance(1);
  assert.equal(q.claim()!.id, id);
});

test('complete moves an active job to done and out of the queue', () => {
  const q = new JobQueue();
  const id = q.enqueue('report');
  q.claim();
  q.complete(id);
  assert.equal(q.job(id).state, 'done');
  assert.equal(q.claim(), null);
  assert.deepEqual(q.stats(), { waiting: 0, active: 0, done: 1, dead: 0 });
});

test('only active jobs can be completed or failed', () => {
  const q = new JobQueue();
  const id = q.enqueue('a');
  assert.throws(() => q.complete(id), /not active/);
  assert.throws(() => q.fail(id, 'x'), /not active/);
  q.claim();
  q.complete(id);
  assert.throws(() => q.complete(id), /not active/);
  assert.throws(() => q.fail(id, 'x'), /not active/);
  assert.throws(() => q.complete('job-99'), /unknown job/);
  assert.throws(() => q.fail('job-99', 'x'), /unknown job/);
  assert.throws(() => q.job('job-99'), /unknown job/);
});

test('a job dies after exactly maxAttempts failures', () => {
  const q = new JobQueue();
  const id = q.enqueue('once', null, { maxAttempts: 1 });
  q.claim();
  q.fail(id, 'no luck');
  assert.equal(q.job(id).state, 'dead');
  assert.equal(q.deadLetters().length, 1);
  q.advance(1_000_000);
  assert.equal(q.claim(), null);
});

test('requeue resurrects a dead job with a fresh attempt budget', () => {
  const q = new JobQueue();
  const id = q.enqueue('flaky', { n: 1 }, { maxAttempts: 1 });
  q.claim();
  q.fail(id, 'first life');
  assert.equal(q.deadLetters().length, 1);

  q.requeue(id);
  assert.deepEqual(q.deadLetters(), []);
  const snap = q.job(id);
  assert.equal(snap.state, 'waiting');
  assert.equal(snap.attempts, 0);
  assert.equal(snap.errors.length, 1); // history preserved

  const job = q.claim()!;
  assert.equal(job.id, id);
  assert.equal(job.attempt, 1);
});

test('requeue rejects jobs that are not dead', () => {
  const q = new JobQueue();
  const id = q.enqueue('alive');
  assert.throws(() => q.requeue(id), /not dead/);
  q.claim();
  assert.throws(() => q.requeue(id), /not dead/);
  assert.throws(() => q.requeue('job-42'), /unknown job/);
});

test('snapshots are copies, not live references', () => {
  const q = new JobQueue();
  const id = q.enqueue('audit', null, { maxAttempts: 2 });
  q.claim();
  q.fail(id, 'hiccup');
  const snap = q.job(id);
  snap.errors.push({ attempt: 99, reason: 'forged', at: 0 });
  assert.equal(q.job(id).errors.length, 1);
});

test('stats counts jobs by state', () => {
  const q = new JobQueue();
  q.enqueue('w1');
  q.enqueue('w2', null, { delayMs: 500 });
  const active = q.enqueue('a1', null, { priority: 5 });
  const dying = q.enqueue('d1', null, { priority: 4, maxAttempts: 1 });
  q.claim(); // a1 (priority 5)
  const claimed = q.claim(); // d1 (priority 4)
  assert.equal(claimed!.id, dying);
  q.fail(dying, 'gone');
  assert.deepEqual(q.stats(), { waiting: 2, active: 1, done: 0, dead: 1 });
  q.complete(active);
  assert.deepEqual(q.stats(), { waiting: 2, active: 0, done: 1, dead: 1 });
});

test('the queue clock starts where you tell it to', () => {
  const q = new JobQueue({ now: 5000 });
  assert.equal(q.now(), 5000);
  q.enqueue('t', null, { delayMs: 100 });
  assert.equal(q.claim(), null);
  q.advance(100);
  assert.equal(q.now(), 5100);
  assert.notEqual(q.claim(), null);
});

test('bad arguments are rejected up front', () => {
  const q = new JobQueue();
  assert.throws(() => q.enqueue(''), /job type/);
  assert.throws(() => q.enqueue('   '), /job type/);
  assert.throws(() => q.enqueue('x', null, { delayMs: -1 }), RangeError);
  assert.throws(() => q.enqueue('x', null, { maxAttempts: 0 }), RangeError);
  assert.throws(() => q.enqueue('x', null, { maxAttempts: 2.5 }), RangeError);
  assert.throws(() => q.advance(-1), RangeError);
  assert.throws(() => q.advance(NaN), RangeError);
  assert.deepEqual(q.stats(), { waiting: 0, active: 0, done: 0, dead: 0 });
});
