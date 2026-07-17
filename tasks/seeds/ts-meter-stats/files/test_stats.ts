import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MeterHub } from './hub.ts';
import type { Reading } from './hub.ts';
import { EnergyStats } from './stats.ts';

function r(meterId: string, watts: number, at: number): Reading {
  return { meterId, watts, at };
}

test('attached collector accumulates totals per meter', () => {
  const hub = new MeterHub();
  const stats = new EnergyStats();
  stats.attach(hub);

  hub.publish('reading', r('kitchen', 120, 1_000));
  hub.publish('reading', r('kitchen', 80, 2_000));
  hub.publish('reading', r('garage', 300, 2_000));

  assert.equal(stats.totalFor('kitchen'), 200);
  assert.equal(stats.totalFor('garage'), 300);
  assert.equal(stats.sampleCount('kitchen'), 2);
  assert.deepEqual(stats.metersSeen(), ['garage', 'kitchen']);
});

test('averages reflect every published sample', () => {
  const hub = new MeterHub();
  const stats = new EnergyStats();
  stats.attach(hub);

  hub.publish('reading', r('heatpump', 900, 1_000));
  hub.publish('reading', r('heatpump', 1_100, 2_000));
  hub.publish('reading', r('heatpump', 1_000, 3_000));

  assert.equal(stats.averageFor('heatpump'), 1_000);
  assert.equal(stats.averageFor('unknown'), 0);
});

test('dropout events are logged in arrival order', () => {
  const hub = new MeterHub();
  const stats = new EnergyStats();
  stats.attach(hub);

  hub.publish('dropout', r('attic', 0, 1_000));
  hub.publish('dropout', r('kitchen', 0, 2_000));
  hub.publish('dropout', r('attic', 0, 3_000));

  assert.deepEqual(stats.dropoutLog(), ['attic', 'kitchen', 'attic']);
});

test('two homes attached to one hub aggregate independently and completely', () => {
  const hub = new MeterHub();
  const home1 = new EnergyStats();
  const home2 = new EnergyStats();
  home1.attach(hub);
  home2.attach(hub);

  hub.publish('reading', r('kitchen', 50, 1_000));
  hub.publish('reading', r('kitchen', 70, 2_000));

  assert.equal(home1.totalFor('kitchen'), 120);
  assert.equal(home2.totalFor('kitchen'), 120);
  assert.equal(hub.deliveryFailures(), 0);
});

test('hub still delivers to plain function subscribers alongside the collector', () => {
  const hub = new MeterHub();
  const stats = new EnergyStats();
  const seen: number[] = [];
  hub.subscribe('reading', (reading) => seen.push(reading.watts));
  stats.attach(hub);

  const delivered = hub.publish('reading', r('office', 42, 1_000));
  assert.deepEqual(seen, [42]);
  assert.equal(delivered, 2);
  assert.equal(stats.totalFor('office'), 42);
});
