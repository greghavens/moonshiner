import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseEvent, compareIds, nextId, CheckpointStore } from './checkpoint.ts';

const LO = '9007199254740992'; // 2^53
const HI = '9007199254740993'; // 2^53 + 1
const HUGE = '18446744073709551615'; // 2^64 - 1

function line(id: string, kind = 'order.updated'): string {
  return `{"id":${id},"kind":"${kind}"}`;
}

test('adjacent ids across the 2^53 boundary stay distinct', () => {
  const a = parseEvent(line(LO));
  const b = parseEvent(line(HI));
  assert.equal(a.id.toString(), LO);
  assert.equal(b.id.toString(), HI);
  assert.equal(compareIds(a.id, b.id), -1);
  assert.equal(compareIds(b.id, a.id), 1);
  assert.equal(compareIds(a.id, a.id), 0);
});

test('consecutive events above the boundary both advance the checkpoint', () => {
  const store = new CheckpointStore();
  assert.equal(store.advance(parseEvent(line(LO)).id), true);
  assert.equal(
    store.advance(parseEvent(line(HI)).id),
    true,
    'a fresh event was classified as a replay',
  );
  assert.equal(store.lastId, 9007199254740993n);
});

test('a full 64-bit id survives parse and serialize digit for digit', () => {
  const store = new CheckpointStore();
  assert.equal(store.advance(parseEvent(line(HUGE)).id), true);
  assert.equal(store.serialize(), `{"lastId":${HUGE}}`);
});

test('nextId of a parsed id matches the next wire id exactly', () => {
  const current = parseEvent(line(HI)).id;
  const following = parseEvent(line('9007199254740994')).id;
  assert.equal(nextId(current).toString(), '9007199254740994');
  assert.equal(compareIds(nextId(current), following), 0);
});

test('replayed and stale ids are skipped, fresh ids accepted', () => {
  const store = new CheckpointStore();
  assert.equal(store.advance(parseEvent(line('41')).id), true);
  assert.equal(store.advance(parseEvent(line('41')).id), false);
  assert.equal(store.advance(parseEvent(line('40')).id), false);
  assert.equal(store.advance(parseEvent(line('42')).id), true);
  assert.equal(store.lastId, 42n);
});

test('fractional, exponent, negative, quoted and non-numeric ids are rejected', () => {
  const rejected = ['12.5', '9007199254740993.5', '1e3', '2E5', '4.0e2', '-3', '"123"', 'null', 'true'];
  for (const bad of rejected) {
    assert.throws(
      () => parseEvent(`{"id":${bad},"kind":"order.updated"}`),
      /invalid event id/,
      `id literal ${bad} was accepted`,
    );
  }
});

test('small ids keep working end to end', () => {
  const store = new CheckpointStore();
  const ev = parseEvent(line('7', 'order.created'));
  assert.deepEqual(ev, { id: 7n, kind: 'order.created' });
  assert.equal(store.advance(ev.id), true);
  assert.equal(store.serialize(), '{"lastId":7}');
});

test('an empty store serializes a null checkpoint', () => {
  assert.equal(new CheckpointStore().serialize(), '{"lastId":null}');
});

test('missing kind or unparseable input is reported as malformed', () => {
  assert.throws(() => parseEvent('{"id":41}'), /malformed event/);
  assert.throws(() => parseEvent('not json at all'), /malformed event/);
  assert.throws(() => parseEvent('[41]'), /malformed event/);
});
