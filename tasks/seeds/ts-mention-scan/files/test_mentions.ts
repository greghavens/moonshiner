import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractMentions, hasMention, isValidUsername, notifyList } from './mentions.ts';

test('extracts every mention in order, deduplicated', () => {
  assert.deepEqual(extractMentions('cc @alice and @bob re: rollout, thanks @alice'), [
    'alice',
    'bob',
  ]);
});

test('hasMention answers consistently for the same message', () => {
  const message = 'ping @charlie — any update?';
  assert.equal(hasMention(message), true);
  assert.equal(hasMention(message), true);
  assert.equal(hasMention('no handles here'), false);
});

test('notifyList pings every registered user mentioned', () => {
  const pinged = notifyList('@dana can you and @erin review the RFC?', 'frank', [
    'dana',
    'erin',
    'frank',
  ]);
  assert.deepEqual(pinged, ['dana', 'erin']);
});

test('the author is not pinged for self-mentions', () => {
  const pinged = notifyList('thanks @gus, @hana approved it', 'gus', ['gus', 'hana']);
  assert.deepEqual(pinged, ['hana']);
});

test('back-to-back messages each get a full scan', () => {
  assert.deepEqual(notifyList('@ivy first message', 'zed', ['ivy']), ['ivy']);
  assert.deepEqual(notifyList('@ivy second message', 'zed', ['ivy']), ['ivy']);
});

test('validates handles against the allowed shape', () => {
  assert.equal(isValidUsername('build_bot42'), true);
  assert.equal(isValidUsername('ok'), true);
  assert.equal(isValidUsername('no spaces allowed'), false);
  assert.equal(isValidUsername('Dr.Evil'), false);
  assert.equal(isValidUsername('x'), false);
  assert.equal(isValidUsername('way_too_long_for_a_handle_here'), false);
});
