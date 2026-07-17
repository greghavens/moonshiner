import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, copyFileSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const fixture = fileURLToPath(new URL('./fixtures/guide.md', import.meta.url));
const cli = fileURLToPath(new URL('./cli.ts', import.meta.url));

function run(args: string[]) {
  return spawnSync(process.execPath, [cli, ...args], { encoding: 'utf8' });
}

function tmpGuide(t: { after: (fn: () => void) => void }) {
  const dir = mkdtempSync('toc-cli-');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, 'guide.md');
  copyFileSync(fixture, file);
  return { dir, file };
}

test('rewrites a stale file in place and reports it', (t) => {
  const { file } = tmpGuide(t);
  const before = readFileSync(file, 'utf8');
  const r = run([file]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), `updated ${file}`);
  const after = readFileSync(file, 'utf8');
  assert.notEqual(after, before);
  assert.ok(after.includes('- [Field Guide](#field-guide)'));
  assert.ok(after.includes('  - [Usage](#usage-1)'));
  assert.ok(after.includes('<!-- tocstop -->'));
});

test('a second run is a no-op and says so', (t) => {
  const { file } = tmpGuide(t);
  run([file]);
  const settled = readFileSync(file, 'utf8');
  const r = run([file]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), `unchanged ${file}`);
  assert.equal(readFileSync(file, 'utf8'), settled);
});

test('--check exits 1 on a stale file without writing', (t) => {
  const { file } = tmpGuide(t);
  const before = readFileSync(file, 'utf8');
  const r = run(['--check', file]);
  assert.equal(r.status, 1);
  assert.equal(r.stdout.trim(), `stale ${file}`);
  assert.equal(readFileSync(file, 'utf8'), before);
});

test('--check exits 0 once the file is current', (t) => {
  const { file } = tmpGuide(t);
  run([file]);
  const r = run(['--check', file]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), `ok ${file}`);
});

test('--max-level caps the generated depth', (t) => {
  const { file } = tmpGuide(t);
  const r = run(['--max-level', '2', file]);
  assert.equal(r.status, 0, r.stderr);
  const after = readFileSync(file, 'utf8');
  assert.ok(after.includes('- [Field Guide](#field-guide)'));
  assert.ok(after.includes('  - [Usage](#usage-1)'));
  assert.ok(!after.includes('(#install--run)'));
  assert.ok(!after.includes('(#basics)'));
});

test('a file without markers is refused, untouched, exit 2', (t) => {
  const dir = mkdtempSync('toc-cli-');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, 'plain.md');
  writeFileSync(file, '# No Markers\n\nprose\n');
  const r = run([file]);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes(`no toc markers in ${file}`), r.stderr);
  assert.equal(readFileSync(file, 'utf8'), '# No Markers\n\nprose\n');
});

test('no arguments prints usage on stderr and exits 2', () => {
  const r = run([]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /usage/i);
});

test('a missing file exits 2 and names the file on stderr', () => {
  const r = run(['definitely-not-here.md']);
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes('definitely-not-here.md'), r.stderr);
});
