import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('./cli.ts', import.meta.url));

type Ctx = { after: (fn: () => void) => void };

const BASIC = [
  {
    version: 3,
    name: 'rename-plan',
    up: [{ rename: { from: 'profile.plan', to: 'profile.tier' } }],
    down: [{ rename: { from: 'profile.tier', to: 'profile.plan' } }],
  },
  {
    version: 1,
    name: 'create-profile',
    up: [{ set: { path: 'profile', value: {} } }, { set: { path: 'profile.plan', value: 'free' } }],
    down: [{ unset: { path: 'profile' } }],
  },
  {
    version: 2,
    name: 'add-flags',
    up: [{ set: { path: 'flags.beta', value: false } }],
    down: [{ unset: { path: 'flags' } }],
  },
];

const FAILING = [
  BASIC[1], // version 1, create-profile
  {
    version: 2,
    name: 'add-flags',
    up: [{ set: { path: 'flags.beta', value: false } }, { unset: { path: 'settings.legacy' } }],
    down: [{ unset: { path: 'flags' } }],
  },
  BASIC[0], // version 3, rename-plan
];

function project(t: Ctx, migrations?: unknown) {
  const dir = mkdtempSync('mig-');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  if (migrations !== undefined) {
    writeFileSync(join(dir, 'migrations.json'), JSON.stringify(migrations, null, 2));
  }
  return dir;
}

function run(dir: string, args: string[]) {
  return spawnSync(process.execPath, [cli, ...args], { cwd: dir, encoding: 'utf8' });
}

function lines(out: string) {
  return out.split('\n').filter((l) => l !== '');
}

function readJson(dir: string, name: string) {
  return JSON.parse(readFileSync(join(dir, name), 'utf8'));
}

test('status on a fresh project', (t) => {
  const dir = project(t, BASIC);
  const r = run(dir, ['status']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['version: 0', 'pending: 3']);
});

test('up applies everything by version regardless of file order', (t) => {
  const dir = project(t, BASIC);
  const r = run(dir, ['up']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['up 1 create-profile', 'up 2 add-flags', 'up 3 rename-plan']);
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { tier: 'free' }, flags: { beta: false } });
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 3, dirty: false });
  const s = run(dir, ['status']);
  assert.deepEqual(lines(s.stdout), ['version: 3', 'pending: 0']);
});

test('up with nothing pending is a no-op', (t) => {
  const dir = project(t, BASIC);
  run(dir, ['up']);
  const r = run(dir, ['up']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['nothing to do']);
});

test('state survives across invocations: down reverts one version', (t) => {
  const dir = project(t, BASIC);
  run(dir, ['up']);
  const r = run(dir, ['down']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['down 3 rename-plan']);
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { plan: 'free' }, flags: { beta: false } });
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 2, dirty: false });
});

test('down at version zero has nothing to revert', (t) => {
  const dir = project(t, BASIC);
  const r = run(dir, ['down']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['nothing to do']);
});

test('to walks down to an exact version and back up', (t) => {
  const dir = project(t, BASIC);
  run(dir, ['up']);
  const down = run(dir, ['to', '1']);
  assert.equal(down.status, 0, down.stderr);
  assert.deepEqual(lines(down.stdout), ['down 3 rename-plan', 'down 2 add-flags']);
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { plan: 'free' } });
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 1, dirty: false });

  const up = run(dir, ['to', '3']);
  assert.deepEqual(lines(up.stdout), ['up 2 add-flags', 'up 3 rename-plan']);
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { tier: 'free' }, flags: { beta: false } });

  const same = run(dir, ['to', '3']);
  assert.deepEqual(lines(same.stdout), ['nothing to do']);
});

test('to 0 unwinds the whole set', (t) => {
  const dir = project(t, BASIC);
  run(dir, ['up']);
  const r = run(dir, ['to', '0']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['down 3 rename-plan', 'down 2 add-flags', 'down 1 create-profile']);
  assert.deepEqual(readJson(dir, 'db.json'), {});
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 0, dirty: false });
});

test('to an unknown version is refused', (t) => {
  const dir = project(t, BASIC);
  const r = run(dir, ['to', '9']);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes('unknown version 9'), r.stderr);
});

test('version numbers may have gaps', (t) => {
  const migs = [
    { version: 1, name: 'one', up: [{ set: { path: 'a', value: 1 } }], down: [{ unset: { path: 'a' } }] },
    { version: 9, name: 'nine', up: [{ set: { path: 'c', value: 9 } }], down: [{ unset: { path: 'c' } }] },
    { version: 5, name: 'five', up: [{ set: { path: 'b', value: 5 } }], down: [{ unset: { path: 'b' } }] },
  ];
  const dir = project(t, migs);
  const r = run(dir, ['to', '5']);
  assert.deepEqual(lines(r.stdout), ['up 1 one', 'up 5 five']);
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 5, dirty: false });
  const d = run(dir, ['down']);
  assert.deepEqual(lines(d.stdout), ['down 5 five']);
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 1, dirty: false });
});

test('a failing op leaves the completed part of the migration and goes dirty', (t) => {
  const dir = project(t, FAILING);
  const r = run(dir, ['up']);
  assert.equal(r.status, 1);
  assert.deepEqual(lines(r.stdout), ['up 1 create-profile']);
  assert.match(r.stderr, /migration 2 add-flags failed:/);
  assert.match(r.stderr, /no such path/);
  // migration 1 fully applied, migration 2's first op persisted, then the failure
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { plan: 'free' }, flags: { beta: false } });
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 2, dirty: true });
});

test('dirty state blocks up, down and to, but not status', (t) => {
  const dir = project(t, FAILING);
  run(dir, ['up']);
  const dbBefore = readFileSync(join(dir, 'db.json'), 'utf8');

  for (const args of [['up'], ['down'], ['to', '0']]) {
    const r = run(dir, args);
    assert.equal(r.status, 3, `expected ${args.join(' ')} to be blocked`);
    assert.ok(r.stderr.includes('dirty state at version 2'), r.stderr);
  }
  assert.equal(readFileSync(join(dir, 'db.json'), 'utf8'), dbBefore, 'db must be untouched while dirty');

  const s = run(dir, ['status']);
  assert.equal(s.status, 0);
  assert.deepEqual(lines(s.stdout), ['version: 2 (dirty)', 'pending: 1']);
});

test('force clears dirty state and the run can be repaired', (t) => {
  const dir = project(t, FAILING);
  run(dir, ['up']);

  const f = run(dir, ['force', '1']);
  assert.equal(f.status, 0, f.stderr);
  assert.deepEqual(lines(f.stdout), ['forced to 1']);
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 1, dirty: false });

  // the human repaired the migration set; re-run picks up from version 2
  writeFileSync(join(dir, 'migrations.json'), JSON.stringify(BASIC, null, 2));
  const r = run(dir, ['up']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(lines(r.stdout), ['up 2 add-flags', 'up 3 rename-plan']);
  assert.deepEqual(readJson(dir, 'db.json'), { profile: { tier: 'free' }, flags: { beta: false } });
  assert.deepEqual(readJson(dir, 'migrate-state.json'), { version: 3, dirty: false });
});

test('force refuses versions that do not exist', (t) => {
  const dir = project(t, BASIC);
  const r = run(dir, ['force', '7']);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes('unknown version 7'), r.stderr);
  assert.equal(existsSync(join(dir, 'migrate-state.json')), false);
});

test('a missing migrations.json is reported', (t) => {
  const dir = project(t);
  const r = run(dir, ['status']);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes('no migrations.json'), r.stderr);
});

test('duplicate versions in migrations.json are rejected', (t) => {
  const dupes = [
    { version: 1, name: 'a', up: [], down: [] },
    { version: 1, name: 'b', up: [], down: [] },
  ];
  const dir = project(t, dupes);
  const r = run(dir, ['up']);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes('invalid migrations'), r.stderr);
});

test('usage errors exit 2', (t) => {
  const dir = project(t, BASIC);
  for (const args of [[], ['bogus'], ['to'], ['force']]) {
    const r = run(dir, args);
    assert.equal(r.status, 2, `args: ${args.join(' ') || '(none)'}`);
    assert.match(r.stderr, /usage/i);
  }
});
