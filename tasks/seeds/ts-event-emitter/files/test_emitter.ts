import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Emitter } from './emitter.ts';

test('on + emit delivers the payload', () => {
  const em = new Emitter();
  const seen: unknown[] = [];
  em.on('data', (x) => seen.push(x));
  em.emit('data', 42);
  assert.deepEqual(seen, [42]);
});

test('off removes the handler', () => {
  const em = new Emitter();
  let count = 0;
  const fn = () => count++;
  em.on('tick', fn);
  em.emit('tick');
  em.off('tick', fn);
  em.emit('tick');
  assert.equal(count, 1);
});

test('off only removes the matching handler', () => {
  const em = new Emitter();
  let a = 0;
  let b = 0;
  const fnA = () => a++;
  const fnB = () => b++;
  em.on('tick', fnA);
  em.on('tick', fnB);
  em.off('tick', fnB);
  em.emit('tick');
  assert.equal(a, 1);
  assert.equal(b, 0);
});

test('once fires exactly once', () => {
  const em = new Emitter();
  let count = 0;
  em.once('boom', () => count++);
  em.emit('boom');
  em.emit('boom');
  em.emit('boom');
  assert.equal(count, 1);
});
