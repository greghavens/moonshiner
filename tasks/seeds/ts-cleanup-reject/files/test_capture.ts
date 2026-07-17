import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withCapture } from './capture.ts';
import type { CaptureStream, CleanupHook, CleanupStage, DeviceHub } from './capture.ts';

interface Rig {
  opened: number;
  started: number;
  stopped: number;
  closed: number;
  cleanup: { stage: CleanupStage; error: unknown }[];
}

interface RigFaults {
  failStart?: Error;
  failStop?: Error;
  failClose?: Error;
}

function makeRig(faults: RigFaults = {}): { hub: DeviceHub; rig: Rig; hook: CleanupHook } {
  const rig: Rig = { opened: 0, started: 0, stopped: 0, closed: 0, cleanup: [] };
  const hub: DeviceHub = {
    async open(_deviceId: string) {
      rig.opened++;
      return {
        async startCapture() {
          if (faults.failStart) throw faults.failStart;
          rig.started++;
          return {
            async read() {
              return [{ seq: 1, payload: 'voltage nominal' }];
            },
            async stop() {
              rig.stopped++;
              if (faults.failStop) throw faults.failStop;
            },
          };
        },
        async close() {
          rig.closed++;
          if (faults.failClose) throw faults.failClose;
        },
      };
    },
  };
  const hook: CleanupHook = (error, stage) => rig.cleanup.push({ stage, error });
  return { hub, rig, hook };
}

test('a clean pass returns the work result and disposes both resources once', async () => {
  const { hub, rig, hook } = makeRig();
  const frames = await withCapture(hub, 'bench-04', (c) => c.read(), hook);
  assert.deepEqual(frames, [{ seq: 1, payload: 'voltage nominal' }]);
  assert.deepEqual(
    { opened: rig.opened, started: rig.started, stopped: rig.stopped, closed: rig.closed },
    { opened: 1, started: 1, stopped: 1, closed: 1 },
  );
  assert.deepEqual(rig.cleanup, []);
});

test('a failing pass rethrows the original error and still disposes everything', async () => {
  const { hub, rig, hook } = makeRig();
  const boom = new Error('probe assertion failed: rail 3 sagged');
  await assert.rejects(
    withCapture(hub, 'bench-04', async () => {
      throw boom;
    }, hook),
    (err: unknown) => err === boom,
  );
  assert.equal(rig.stopped, 1);
  assert.equal(rig.closed, 1);
  assert.deepEqual(rig.cleanup, []);
});

test('a synchronous throw inside the work callback is treated like a rejection', async () => {
  const { hub, rig, hook } = makeRig();
  const boom = new Error('bad probe map');
  await assert.rejects(
    withCapture(hub, 'bench-04', () => {
      throw boom;
    }, hook),
    (err: unknown) => err === boom,
  );
  assert.equal(rig.stopped, 1);
  assert.equal(rig.closed, 1);
});

test('a capture that fails to start still returns the device handle', async () => {
  const startErr = new Error('capture engine busy');
  const { hub, rig, hook } = makeRig({ failStart: startErr });
  await assert.rejects(
    withCapture(hub, 'bench-04', (c) => c.read(), hook),
    (err: unknown) => err === startErr,
  );
  assert.equal(rig.stopped, 0, 'nothing to stop, capture never started');
  assert.equal(rig.closed, 1, 'device handle was never returned to the hub');
  assert.deepEqual(rig.cleanup, []);
});

test('a stop failure never hides the error from the pass itself', async () => {
  const primary = new Error('probe assertion failed: overshoot on channel 2');
  const stopErr = new Error('stop timed out');
  const { hub, rig, hook } = makeRig({ failStop: stopErr });
  await assert.rejects(
    withCapture(hub, 'bench-04', async () => {
      throw primary;
    }, hook),
    (err: unknown) => err === primary,
  );
  assert.equal(rig.closed, 1, 'stop failure must not skip returning the handle');
  assert.deepEqual(rig.cleanup, [{ stage: 'capture', error: stopErr }]);
});

test('a stop failure after a good pass still returns the result', async () => {
  const stopErr = new Error('stop timed out');
  const { hub, rig, hook } = makeRig({ failStop: stopErr });
  const frames = await withCapture(hub, 'bench-04', (c) => c.read(), hook);
  assert.equal(frames.length, 1);
  assert.equal(rig.closed, 1);
  assert.deepEqual(rig.cleanup, [{ stage: 'capture', error: stopErr }]);
});

test('a close failure after a good pass is reported, not thrown', async () => {
  const closeErr = new Error('hub session already recycled');
  const { hub, rig, hook } = makeRig({ failClose: closeErr });
  const frames = await withCapture(hub, 'bench-04', (c) => c.read(), hook);
  assert.equal(frames.length, 1);
  assert.deepEqual(rig.cleanup, [{ stage: 'device', error: closeErr }]);
});

test('when everything fails, the pass error wins and both failures are reported in order', async () => {
  const primary = new Error('probe assertion failed');
  const stopErr = new Error('stop timed out');
  const closeErr = new Error('hub session already recycled');
  const { hub, rig, hook } = makeRig({ failStop: stopErr, failClose: closeErr });
  await assert.rejects(
    withCapture(hub, 'bench-04', async () => {
      throw primary;
    }, hook),
    (err: unknown) => err === primary,
  );
  assert.equal(rig.stopped, 1);
  assert.equal(rig.closed, 1);
  assert.deepEqual(rig.cleanup, [
    { stage: 'capture', error: stopErr },
    { stage: 'device', error: closeErr },
  ]);
});

test('cleanup failures are swallowed even when no hook is passed', async () => {
  const { hub, rig } = makeRig({ failStop: new Error('stop timed out') });
  const frames = await withCapture(hub, 'bench-04', (c: CaptureStream) => c.read());
  assert.equal(frames.length, 1);
  assert.equal(rig.closed, 1);
});
