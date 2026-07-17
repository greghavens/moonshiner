import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ShortcutRegistry } from './shortcuts.ts';

test('a two-key chord fires after both keys, both presses consumed', () => {
  const r = new ShortcutRegistry();
  let fired = 0;
  r.register('g d', () => fired++);
  assert.equal(r.handle({ key: 'g' }), true);
  assert.equal(fired, 0);
  assert.equal(r.handle({ key: 'd' }), true);
  assert.equal(fired, 1);
});

test('completing a chord does not also fire a standalone binding for the last key', () => {
  const r = new ShortcutRegistry();
  const log: string[] = [];
  r.register('g d', () => log.push('chord'));
  r.register('d', () => log.push('solo'));
  r.handle({ key: 'g' });
  r.handle({ key: 'd' });
  assert.deepEqual(log, ['chord']);
  // with no pending prefix, the standalone binding works as before
  r.handle({ key: 'd' });
  assert.deepEqual(log, ['chord', 'solo']);
});

test('a key that breaks the chord is re-dispatched on its own', () => {
  const r = new ShortcutRegistry();
  const log: string[] = [];
  r.register('g d', () => log.push('chord'));
  r.register('x', () => log.push('x'));
  assert.equal(r.handle({ key: 'g' }), true);
  assert.equal(r.handle({ key: 'x' }), true); // aborts the chord, then matches solo x
  assert.deepEqual(log, ['x']);
  // the aborted prefix is gone: d alone does nothing now
  assert.equal(r.handle({ key: 'd' }), false);
});

test('a breaking key may start a new chord', () => {
  const r = new ShortcutRegistry();
  const log: string[] = [];
  r.register('g d', () => log.push('gd'));
  r.register('v i', () => log.push('vi'));
  r.handle({ key: 'g' });
  assert.equal(r.handle({ key: 'v' }), true);
  r.handle({ key: 'i' });
  assert.deepEqual(log, ['vi']);
});

test('chords sharing a first key coexist and resolve by the second key', () => {
  const r = new ShortcutRegistry();
  const log: string[] = [];
  r.register('g d', () => log.push('dashboard'));
  r.register('g s', () => log.push('settings'));
  r.handle({ key: 'g' });
  r.handle({ key: 's' });
  r.handle({ key: 'g' });
  r.handle({ key: 'd' });
  assert.deepEqual(log, ['settings', 'dashboard']);
});

test('pending() exposes the normalized buffer and clears on completion and abort', () => {
  const r = new ShortcutRegistry();
  r.register('ctrl+k ctrl+s', () => {});
  assert.deepEqual(r.pending(), []);
  r.handle({ key: 'K', ctrlKey: true });
  assert.deepEqual(r.pending(), ['ctrl+k']);
  r.handle({ key: 's', ctrlKey: true });
  assert.deepEqual(r.pending(), []);
  r.handle({ key: 'k', ctrlKey: true });
  r.handle({ key: 'q' });
  assert.deepEqual(r.pending(), []);
});

test('chords normalize each step like single combos', () => {
  const r = new ShortcutRegistry();
  let fired = 0;
  r.register('shift+ctrl+K meta+D', () => fired++);
  assert.deepEqual(r.list(), ['ctrl+shift+k meta+d']);
  r.handle({ key: 'k', ctrlKey: true, shiftKey: true });
  r.handle({ key: 'd', metaKey: true });
  assert.equal(fired, 1);
});

test('binding a prefix of an existing chord is a conflict', () => {
  const r = new ShortcutRegistry();
  r.register('g d', () => {});
  assert.throws(() => r.register('g', () => {}), /conflict|prefix/i);
  assert.throws(() => r.register('g d p', () => {}), /conflict|prefix/i);
});

test('binding a chord under an existing shorter binding is a conflict', () => {
  const r = new ShortcutRegistry();
  r.register('g', () => {});
  assert.throws(() => r.register('g d', () => {}), /conflict|prefix/i);
});

test('registering the identical chord twice still throws', () => {
  const r = new ShortcutRegistry();
  r.register('g d', () => {});
  assert.throws(() => r.register('G D', () => {}));
});

test('unregistering a chord frees its prefix for reuse', () => {
  const r = new ShortcutRegistry();
  r.register('g d', () => {});
  assert.equal(r.unregister('g d'), true);
  r.register('g', () => {}); // no longer a conflict
  let fired = 0;
  r.unregister('g');
  r.register('g x', () => fired++);
  r.handle({ key: 'g' });
  r.handle({ key: 'x' });
  assert.equal(fired, 1);
});
