import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ShortcutRegistry, normalizeCombo } from './shortcuts.ts';

test('a registered combo fires its handler and reports handled', () => {
  const r = new ShortcutRegistry();
  let fired = 0;
  r.register('ctrl+s', () => fired++);
  assert.equal(r.handle({ key: 's', ctrlKey: true }), true);
  assert.equal(fired, 1);
});

test('an unbound event reports unhandled and fires nothing', () => {
  const r = new ShortcutRegistry();
  r.register('ctrl+s', () => assert.fail('should not fire'));
  assert.equal(r.handle({ key: 's' }), false);
  assert.equal(r.handle({ key: 'k', ctrlKey: true }), false);
});

test('modifier order and case do not matter', () => {
  const r = new ShortcutRegistry();
  let fired = 0;
  r.register('shift+ctrl+P', () => fired++);
  assert.equal(r.handle({ key: 'P', ctrlKey: true, shiftKey: true }), true);
  assert.equal(fired, 1);
  assert.deepEqual(r.list(), ['ctrl+shift+p']);
});

test('normalizeCombo produces the canonical spelling', () => {
  assert.equal(normalizeCombo('meta+ALT+x'), 'alt+meta+x');
  assert.equal(normalizeCombo('k'), 'k');
});

test('registering the same combo twice throws', () => {
  const r = new ShortcutRegistry();
  r.register('ctrl+k', () => {});
  assert.throws(() => r.register('CTRL+K', () => {}));
});

test('unknown modifier tokens are rejected', () => {
  const r = new ShortcutRegistry();
  assert.throws(() => r.register('super+k', () => {}));
});

test('unregister removes a binding under any spelling', () => {
  const r = new ShortcutRegistry();
  r.register('ctrl+shift+f', () => {});
  assert.equal(r.unregister('shift+ctrl+F'), true);
  assert.equal(r.handle({ key: 'f', ctrlKey: true, shiftKey: true }), false);
  assert.equal(r.unregister('ctrl+shift+f'), false);
});
