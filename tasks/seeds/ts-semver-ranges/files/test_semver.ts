import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parse, compare, satisfies, maxSatisfying } from './semver.ts';

test('parse splits the version triple into numbers', () => {
  const v = parse('1.22.333');
  assert.equal(v.major, 1);
  assert.equal(v.minor, 22);
  assert.equal(v.patch, 333);
  assert.deepEqual(v.prerelease, []);
  assert.equal(v.build, null);
});

test('parse splits prerelease into dot-separated identifiers', () => {
  const v = parse('2.0.0-rc.1.hotfix');
  assert.deepEqual(v.prerelease, ['rc', '1', 'hotfix']);
});

test('parse captures build metadata separately from prerelease', () => {
  const v = parse('1.0.0-beta.2+build.99');
  assert.deepEqual(v.prerelease, ['beta', '2']);
  assert.equal(v.build, 'build.99');
  const w = parse('1.0.0+sha.abc');
  assert.deepEqual(w.prerelease, []);
  assert.equal(w.build, 'sha.abc');
});

test('parse tolerates a leading v', () => {
  const v = parse('v3.1.4');
  assert.deepEqual([v.major, v.minor, v.patch], [3, 1, 4]);
});

test('parse throws TypeError on malformed input', () => {
  for (const bad of ['1.2', '1.2.3.4', 'banana', '', '1.2.x', '1.02.3', '01.0.0', '1.2.-3']) {
    assert.throws(() => parse(bad), TypeError, `expected throw for ${JSON.stringify(bad)}`);
  }
});

test('compare orders by major, then minor, then patch', () => {
  assert.equal(compare('2.0.0', '1.9.9'), 1);
  assert.equal(compare('1.2.0', '1.10.0'), -1);
  assert.equal(compare('1.2.3', '1.2.4'), -1);
  assert.equal(compare('1.2.3', '1.2.3'), 0);
});

test('a release outranks any prerelease of the same triple', () => {
  assert.equal(compare('1.0.0', '1.0.0-rc.9'), 1);
  assert.equal(compare('1.0.0-alpha', '1.0.0'), -1);
});

test('numeric prerelease identifiers compare as numbers', () => {
  assert.equal(compare('1.0.0-alpha.2', '1.0.0-alpha.10'), -1);
});

test('numeric identifiers rank below alphanumeric ones', () => {
  assert.equal(compare('1.0.0-alpha.1', '1.0.0-alpha.beta'), -1);
  assert.equal(compare('1.0.0-99999', '1.0.0-a'), -1);
});

test('a prerelease list that is a prefix of another sorts first', () => {
  assert.equal(compare('1.0.0-alpha', '1.0.0-alpha.1'), -1);
  assert.equal(compare('1.0.0-alpha.1.2', '1.0.0-alpha.1'), 1);
});

test('the canonical semver §11 chain is ordered correctly', () => {
  const chain = [
    '1.0.0-alpha',
    '1.0.0-alpha.1',
    '1.0.0-alpha.beta',
    '1.0.0-beta',
    '1.0.0-beta.2',
    '1.0.0-beta.11',
    '1.0.0-rc.1',
    '1.0.0',
  ];
  for (let i = 0; i + 1 < chain.length; i++) {
    assert.equal(compare(chain[i], chain[i + 1]), -1, `${chain[i]} < ${chain[i + 1]}`);
    assert.equal(compare(chain[i + 1], chain[i]), 1, `${chain[i + 1]} > ${chain[i]}`);
  }
});

test('build metadata never affects precedence', () => {
  assert.equal(compare('1.0.0+linux', '1.0.0+darwin'), 0);
  assert.equal(compare('1.0.0-rc.1+b1', '1.0.0-rc.1+b2'), 0);
});

test('an exact range matches only that version', () => {
  assert.equal(satisfies('1.2.3', '1.2.3'), true);
  assert.equal(satisfies('1.2.4', '1.2.3'), false);
  assert.equal(satisfies('v1.2.3', '1.2.3'), true);
});

test('caret allows compatible changes below the next major', () => {
  assert.equal(satisfies('1.2.3', '^1.2.3'), true);
  assert.equal(satisfies('1.9.0', '^1.2.3'), true);
  assert.equal(satisfies('2.0.0', '^1.2.3'), false);
  assert.equal(satisfies('1.2.2', '^1.2.3'), false);
});

test('caret on 0.x pins the minor', () => {
  assert.equal(satisfies('0.2.5', '^0.2.3'), true);
  assert.equal(satisfies('0.3.0', '^0.2.3'), false);
  assert.equal(satisfies('0.2.2', '^0.2.3'), false);
});

test('caret on 0.0.x pins the patch', () => {
  assert.equal(satisfies('0.0.3', '^0.0.3'), true);
  assert.equal(satisfies('0.0.4', '^0.0.3'), false);
});

test('tilde pins the minor', () => {
  assert.equal(satisfies('1.2.3', '~1.2.3'), true);
  assert.equal(satisfies('1.2.99', '~1.2.3'), true);
  assert.equal(satisfies('1.3.0', '~1.2.3'), false);
  assert.equal(satisfies('1.2.2', '~1.2.3'), false);
});

test('prerelease versions only satisfy ranges anchored at the same prerelease triple', () => {
  assert.equal(satisfies('1.3.0-alpha', '^1.2.0'), false);
  assert.equal(satisfies('1.2.5-beta', '~1.2.0'), false);
  assert.equal(satisfies('2.0.0-rc.2', '^2.0.0-rc.1'), true);
  assert.equal(satisfies('2.0.0-rc.1', '^2.0.0-rc.2'), false);
  assert.equal(satisfies('2.0.1-rc.1', '^2.0.0-rc.1'), false);
  assert.equal(satisfies('2.0.0', '^2.0.0-rc.1'), true);
});

test('maxSatisfying picks the highest matching version', () => {
  const pool = ['1.1.0', '1.2.3', '1.4.9', '2.0.0', '1.4.10-beta'];
  assert.equal(maxSatisfying(pool, '^1.2.0'), '1.4.9');
  assert.equal(maxSatisfying(pool, '~1.2.0'), '1.2.3');
  assert.equal(maxSatisfying(pool, '^3.0.0'), null);
});

test('maxSatisfying is not fooled by input order', () => {
  assert.equal(maxSatisfying(['1.10.0', '1.2.0', '1.9.0'], '^1.0.0'), '1.10.0');
});
