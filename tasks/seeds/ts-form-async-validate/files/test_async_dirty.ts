import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FormValidator, required } from './validator.ts';

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function fakeTimers() {
  let nextId = 1;
  let now = 0;
  const pending = new Map<number, { fn: () => void; at: number }>();
  return {
    setTimer: (fn: () => void, ms: number) => {
      const id = nextId++;
      pending.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimer: (id: unknown) => {
      pending.delete(id as number);
    },
    advance(ms: number) {
      now += ms;
      for (const [id, t] of [...pending]) {
        if (t.at <= now) {
          pending.delete(id);
          t.fn();
        }
      }
    },
  };
}

const settle = () => new Promise<void>((r) => setImmediate(r));

// --- async validators ---

test('async validators run after sync passes and their errors are stored', async () => {
  const form = new FormValidator();
  form.addField('username', [required()]);
  form.addAsync('username', async (value: unknown) =>
    value === 'admin' ? 'name is taken' : null,
  );
  form.setValue('username', 'admin');

  const events: Array<[string, string[]]> = [];
  form.onValidated((name: string, errors: string[]) => events.push([name, errors]));

  assert.deepEqual(await form.validateFieldAsync('username'), ['name is taken']);
  assert.deepEqual(form.getErrors('username'), ['name is taken']);
  assert.deepEqual(events, [['username', ['name is taken']]]);

  form.setValue('username', 'ada');
  assert.deepEqual(await form.validateFieldAsync('username'), []);
  assert.deepEqual(form.getErrors('username'), []);
});

test('sync failures short-circuit: async validators never run', async () => {
  const form = new FormValidator();
  let asyncCalls = 0;
  form.addField('email', [required('email required')]);
  form.addAsync('email', async () => {
    asyncCalls++;
    return null;
  });
  form.setValue('email', '');
  assert.deepEqual(await form.validateFieldAsync('email'), ['email required']);
  assert.deepEqual(form.getErrors('email'), ['email required']);
  assert.equal(asyncCalls, 0);
});

test('multiple async validators all report, in registration order', async () => {
  const form = new FormValidator();
  form.addField('handle', []);
  form.addAsync('handle', async () => 'first problem');
  form.addAsync('handle', async () => null);
  form.addAsync('handle', async () => 'second problem');
  form.setValue('handle', 'x');
  assert.deepEqual(await form.validateFieldAsync('handle'), [
    'first problem',
    'second problem',
  ]);
});

test('a slow stale run cannot overwrite a newer result', async () => {
  const form = new FormValidator();
  const first = deferred<string | null>();
  const second = deferred<string | null>();
  const queue = [first, second];
  form.addField('slug', []);
  form.addAsync('slug', (_value: unknown) => queue.shift()!.promise);

  let validatedCount = 0;
  form.onValidated(() => validatedCount++);

  form.setValue('slug', 'aaa');
  const runA = form.validateFieldAsync('slug');
  form.setValue('slug', 'bbb');
  const runB = form.validateFieldAsync('slug');

  second.resolve('slug is taken');
  assert.deepEqual(await runB, ['slug is taken']);
  assert.deepEqual(form.getErrors('slug'), ['slug is taken']);

  first.resolve(null); // the older run finishes last...
  await runA;
  await settle();
  assert.deepEqual(form.getErrors('slug'), ['slug is taken']); // ...and is discarded
  assert.equal(validatedCount, 1);
});

test('setValue while a run is in flight invalidates that run', async () => {
  const form = new FormValidator();
  const gate = deferred<string | null>();
  form.addField('city', []);
  form.addAsync('city', () => gate.promise);
  let validatedCount = 0;
  form.onValidated(() => validatedCount++);

  form.setValue('city', 'paris');
  const run = form.validateFieldAsync('city');
  form.setValue('city', 'lyon');
  gate.resolve('no such city');
  await run;
  await settle();
  assert.deepEqual(form.getErrors('city'), []);
  assert.equal(validatedCount, 0);
});

// --- debounced validation ---

test('rapid validateDebounced calls collapse into one trailing run', async () => {
  const timers = fakeTimers();
  const form = new FormValidator({ setTimer: timers.setTimer, clearTimer: timers.clearTimer });
  let asyncCalls = 0;
  form.addField('query', []);
  form.addAsync('query', async () => {
    asyncCalls++;
    return null;
  });
  form.setValue('query', 'a');

  form.validateDebounced('query', 50);
  form.validateDebounced('query', 50);
  form.validateDebounced('query', 50);
  assert.equal(asyncCalls, 0);
  timers.advance(49);
  assert.equal(asyncCalls, 0);
  timers.advance(1);
  await settle();
  assert.equal(asyncCalls, 1);
});

test('each debounced call pushes the deadline back', async () => {
  const timers = fakeTimers();
  const form = new FormValidator({ setTimer: timers.setTimer, clearTimer: timers.clearTimer });
  let asyncCalls = 0;
  form.addField('query', []);
  form.addAsync('query', async () => {
    asyncCalls++;
    return null;
  });
  form.setValue('query', 'a');

  form.validateDebounced('query', 50);
  timers.advance(30);
  form.validateDebounced('query', 50);
  timers.advance(30); // 60ms after the first call, 30 after the second
  assert.equal(asyncCalls, 0);
  timers.advance(20);
  await settle();
  assert.equal(asyncCalls, 1);
});

test('the debounced run stores errors and notifies like a direct run', async () => {
  const timers = fakeTimers();
  const form = new FormValidator({ setTimer: timers.setTimer, clearTimer: timers.clearTimer });
  form.addField('code', []);
  form.addAsync('code', async (value: unknown) => (value === 'used' ? 'code already used' : null));
  form.setValue('code', 'used');
  const events: Array<[string, string[]]> = [];
  form.onValidated((name: string, errors: string[]) => events.push([name, errors]));

  form.validateDebounced('code', 10);
  timers.advance(10);
  await settle();
  assert.deepEqual(form.getErrors('code'), ['code already used']);
  assert.deepEqual(events, [['code', ['code already used']]]);
});

// --- dirty tracking ---

test('fields start clean and setValue makes them dirty', () => {
  const form = new FormValidator();
  form.addField('name', []);
  assert.equal(form.isDirty('name'), false);
  form.setValue('name', 'Ada');
  assert.equal(form.isDirty('name'), true);
  assert.deepEqual(form.dirtyFields(), ['name']);
});

test('setting a field back to its initial value makes it clean again', () => {
  const form = new FormValidator();
  form.addField('role', [], 'viewer');
  assert.equal(form.getValue('role'), 'viewer');
  form.setValue('role', 'admin');
  assert.equal(form.isDirty('role'), true);
  form.setValue('role', 'viewer');
  assert.equal(form.isDirty('role'), false);
});

test('markClean adopts current values as the new baseline', () => {
  const form = new FormValidator();
  form.addField('a', [], 1);
  form.addField('b', [], 2);
  form.setValue('a', 10);
  form.setValue('b', 20);
  assert.deepEqual(form.dirtyFields(), ['a', 'b']);
  form.markClean();
  assert.deepEqual(form.dirtyFields(), []);
  form.setValue('a', 1);
  assert.deepEqual(form.dirtyFields(), ['a']); // dirty relative to the NEW baseline
});

test('addAsync and isDirty reject unknown fields', () => {
  const form = new FormValidator();
  assert.throws(() => form.addAsync('ghost', async () => null));
  assert.throws(() => form.isDirty('ghost'));
});
