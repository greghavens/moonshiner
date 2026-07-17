import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HealthGate } from './healthgate.ts';

// ---------------------------------------------------------------------------
// All timing goes through the injected scheduler: the suite never touches a
// real setTimeout, so every test is instant and deterministic. settle() only
// drains already-queued promise callbacks; it does not pass wall-clock time.
// ---------------------------------------------------------------------------

class FakeScheduler {
  now = 0;
  private nextId = 1;
  private timers = new Map<number, { at: number; fn: () => void }>();

  setTimeout(fn: () => void, delayMs: number): number {
    const id = this.nextId++;
    this.timers.set(id, { at: this.now + delayMs, fn });
    return id;
  }

  clearTimeout(id: number): void {
    this.timers.delete(id);
  }

  pending(): number {
    return this.timers.size;
  }

  advance(ms: number): void {
    const target = this.now + ms;
    for (;;) {
      const due = [...this.timers.entries()]
        .filter(([, t]) => t.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0]);
      if (due.length === 0) break;
      const [id, timer] = due[0];
      this.timers.delete(id);
      this.now = timer.at;
      timer.fn();
    }
    this.now = target;
  }
}

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
}

type Step = 'ok' | 'fail' | 'hang';

// One scripted outcome per attempt, in order; the last step repeats.
function scripted(steps: Step[], failMessage = 'boom') {
  let calls = 0;
  const run = (): Promise<void> => {
    const step = steps[Math.min(calls, steps.length - 1)];
    calls++;
    if (step === 'ok') return Promise.resolve();
    if (step === 'fail') return Promise.reject(new Error(failMessage));
    return new Promise<void>(() => {});
  };
  return { run, count: () => calls };
}

const ok = () => Promise.resolve();

test('checks run in dependency order, registration order breaking ties, one at a time', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock });
  const log: string[] = [];
  const mk = (name: string) => async () => {
    log.push(`${name}:start`);
    await Promise.resolve();
    log.push(`${name}:end`);
  };
  gate.add({ name: 'web', run: mk('web'), after: ['api', 'cache'] });
  gate.add({ name: 'api', run: mk('api'), after: ['db'] });
  gate.add({ name: 'cache', run: mk('cache'), after: ['db'] });
  gate.add({ name: 'db', run: mk('db') });

  const report = await gate.runRound();
  // db unblocks api and cache together; api was registered first, so it goes first.
  assert.deepEqual(log, [
    'db:start', 'db:end',
    'api:start', 'api:end',
    'cache:start', 'cache:end',
    'web:start', 'web:end',
  ]);
  assert.deepEqual(
    report.results.map((r) => r.name),
    ['db', 'api', 'cache', 'web'],
  );
  assert.equal(report.overall, 'healthy');
  assert.equal(clock.pending(), 0);
});

test('registering the same check name twice is rejected', () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  gate.add({ name: 'db', run: ok });
  assert.throws(
    () => gate.add({ name: 'db', run: ok }),
    (err: Error) => err.message.includes('db'),
  );
});

test('a dependency on an unregistered check fails the round with both names', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  gate.add({ name: 'api', run: ok, after: ['db'] });
  await assert.rejects(
    gate.runRound(),
    (err: Error) =>
      err.message.includes('unknown dependency') &&
      err.message.includes('"db"') &&
      err.message.includes('"api"'),
  );
});

test('dependency cycles are detected', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  gate.add({ name: 'a', run: ok, after: ['b'] });
  gate.add({ name: 'b', run: ok, after: ['a'] });
  await assert.rejects(gate.runRound(), (err: Error) => err.message.includes('cycle'));
});

test('a hung check fails with reason "timeout" once scheduler time passes timeoutMs', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock, timeoutMs: 5000 });
  const api = scripted(['hang']);
  gate.add({ name: 'api', run: api.run });

  let done = false;
  const round = gate.runRound().then((r) => {
    done = true;
    return r;
  });
  await settle();
  assert.equal(api.count(), 1);
  clock.advance(4999);
  await settle();
  assert.equal(done, false, 'round must still be waiting 1ms before the deadline');
  clock.advance(1);
  await settle();
  assert.equal(done, true);
  const report = await round;
  assert.deepEqual(report.results, [
    { name: 'api', result: 'fail', reason: 'timeout', attempts: 1 },
  ]);
  assert.equal(clock.pending(), 0, 'no timers may be left behind');
});

test('a failing check is retried after retryDelayMs on the injected scheduler', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock });
  const api = scripted(['fail', 'ok']);
  gate.add({ name: 'api', run: api.run, retries: 1, retryDelayMs: 1000 });

  const round = gate.runRound();
  await settle();
  assert.equal(api.count(), 1, 'first attempt should have failed already');
  clock.advance(999);
  await settle();
  assert.equal(api.count(), 1, 'retry must not fire before its delay');
  clock.advance(1);
  await settle();
  assert.equal(api.count(), 2);

  const report = await round;
  assert.deepEqual(report.results, [
    { name: 'api', result: 'pass', reason: null, attempts: 2 },
  ]);
  assert.equal(clock.pending(), 0);
});

test('retries exhausted: result is fail with the last rejection message and total attempts', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  const api = scripted(['fail'], 'connection refused');
  // retryDelayMs 0 means the next attempt follows immediately — no timer needed.
  gate.add({ name: 'api', run: api.run, retries: 2, retryDelayMs: 0 });
  const report = await gate.runRound();
  assert.deepEqual(report.results, [
    { name: 'api', result: 'fail', reason: 'connection refused', attempts: 3 },
  ]);
  assert.equal(api.count(), 3);
});

test('a check that throws synchronously counts as a failed attempt', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  gate.add({
    name: 'api',
    run: () => {
      throw new Error('config missing');
    },
  });
  const report = await gate.runRound();
  assert.deepEqual(report.results, [
    { name: 'api', result: 'fail', reason: 'config missing', attempts: 1 },
  ]);
});

test('dependents of a failed check are skipped, transitively, without being invoked', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock });
  const db = scripted(['fail']);
  const api = scripted(['ok']);
  const web = scripted(['ok']);
  gate.add({ name: 'db', run: db.run });
  gate.add({ name: 'api', run: api.run, after: ['db'] });
  gate.add({ name: 'web', run: web.run, after: ['api'] });

  const report = await gate.runRound();
  assert.deepEqual(report.results, [
    { name: 'db', result: 'fail', reason: 'boom', attempts: 1 },
    { name: 'api', result: 'skipped', reason: 'dependency "db" did not pass', attempts: 0 },
    { name: 'web', result: 'skipped', reason: 'dependency "api" did not pass', attempts: 0 },
  ]);
  assert.equal(api.count(), 0);
  assert.equal(web.count(), 0);
  // Skipped checks are excluded from the aggregate; db is non-critical.
  assert.equal(report.overall, 'degraded');
});

test('aggregate status: critical failure is down, non-critical is degraded, otherwise healthy', async () => {
  const healthy = new HealthGate({ scheduler: new FakeScheduler() });
  healthy.add({ name: 'a', run: ok });
  healthy.add({ name: 'b', run: ok });
  assert.equal((await healthy.runRound()).overall, 'healthy');

  const degraded = new HealthGate({ scheduler: new FakeScheduler() });
  degraded.add({ name: 'a', run: ok });
  degraded.add({ name: 'b', run: scripted(['fail']).run });
  assert.equal((await degraded.runRound()).overall, 'degraded');

  const down = new HealthGate({ scheduler: new FakeScheduler() });
  down.add({ name: 'a', run: ok });
  down.add({ name: 'b', run: scripted(['fail']).run, critical: true });
  assert.equal((await down.runRound()).overall, 'down');
});

// Flap damping semantics, pinned here:
//  - The FIRST observed result of a check is adopted as its reported state.
//  - Afterwards, the reported state only flips after flapThreshold CONSECUTIVE
//    rounds observing the opposite result; an agreeing round resets the streak.
//  - The round report's `results` carry the raw per-round outcome, while
//    `overall` and status() are computed from the damped (reported) states.
test('flap damping: a single bad round does not flip the reported state', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler(), flapThreshold: 2 });
  const api = scripted(['ok', 'fail', 'ok', 'fail', 'fail']);
  gate.add({ name: 'api', run: api.run });

  const overalls: string[] = [];
  const raw: string[] = [];
  for (let round = 0; round < 5; round++) {
    const report = await gate.runRound();
    overalls.push(report.overall);
    raw.push(report.results[0].result);
  }
  assert.deepEqual(raw, ['pass', 'fail', 'pass', 'fail', 'fail']);
  // The lone failure in round 2 and the interrupted streak in round 4 are
  // damped away; only the second consecutive failure (round 5) flips it.
  assert.deepEqual(overalls, ['healthy', 'healthy', 'healthy', 'healthy', 'degraded']);
  assert.deepEqual(gate.status(), { overall: 'degraded', checks: { api: 'fail' } });
});

test('recovery is damped exactly the same way', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler(), flapThreshold: 2 });
  const api = scripted(['fail', 'ok', 'ok']);
  gate.add({ name: 'api', run: api.run });

  assert.equal((await gate.runRound()).overall, 'degraded'); // first observation adopted
  assert.equal((await gate.runRound()).overall, 'degraded'); // one good round is not enough
  assert.equal((await gate.runRound()).overall, 'healthy');  // two in a row flip it back
  assert.deepEqual(gate.status().checks, { api: 'pass' });
});

test('skipped rounds neither count toward nor reset a flap streak', async () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler(), flapThreshold: 2 });
  const db = scripted(['ok', 'ok', 'fail', 'ok']);
  const api = scripted(['fail', 'ok', 'ok']); // consumed in rounds 1, 2 and 4
  gate.add({ name: 'db', run: db.run });
  gate.add({ name: 'api', run: api.run, after: ['db'] });

  const reported: (string | undefined)[] = [];
  for (let round = 0; round < 4; round++) {
    await gate.runRound();
    reported.push(gate.status().checks.api);
  }
  // Round 1: fail adopted. Round 2: ok, streak 1. Round 3: db fails, api is
  // skipped — the streak must survive untouched. Round 4: ok, streak 2, flip.
  assert.deepEqual(reported, ['fail', 'fail', 'fail', 'pass']);
  assert.equal(api.count(), 3, 'api must not run in the round its dependency failed');
});

test('timeout timers are cleared when the check settles first', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock, timeoutMs: 30_000 });
  gate.add({ name: 'a', run: ok });
  gate.add({ name: 'b', run: ok });
  await gate.runRound();
  assert.equal(clock.pending(), 0);
});

test('a late settlement after a timeout is ignored', async () => {
  const clock = new FakeScheduler();
  const gate = new HealthGate({ scheduler: clock, timeoutMs: 100 });
  let release!: () => void;
  gate.add({ name: 'api', run: () => new Promise<void>((r) => (release = r)) });

  const round = gate.runRound();
  await settle();
  clock.advance(100);
  await settle();
  const report = await round;
  assert.deepEqual(report.results, [
    { name: 'api', result: 'fail', reason: 'timeout', attempts: 1 },
  ]);

  release(); // the check finally comes back, long after we gave up on it
  await settle();
  assert.deepEqual(gate.status(), { overall: 'degraded', checks: { api: 'fail' } });
});

test('status before any round reports healthy with no checks', () => {
  const gate = new HealthGate({ scheduler: new FakeScheduler() });
  gate.add({ name: 'api', run: ok });
  assert.deepEqual(gate.status(), { overall: 'healthy', checks: {} });
});
