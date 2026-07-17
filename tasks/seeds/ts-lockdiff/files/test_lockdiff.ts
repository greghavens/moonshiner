import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compareVersions, classifyChange, diffTrees, dedupeReport } from './lockdiff.ts';

// ---------------------------------------------------------------------------
// compareVersions
// ---------------------------------------------------------------------------

test('compareVersions orders the numeric triple numerically, not lexically', () => {
  assert.equal(compareVersions('1.2.3', '1.2.10'), -1);
  assert.equal(compareVersions('1.10.0', '1.9.0'), 1);
  assert.equal(compareVersions('2.0.0', '10.0.0'), -1);
  assert.equal(compareVersions('1.2.3', '1.2.3'), 0);
});

test('compareVersions ignores build metadata entirely', () => {
  assert.equal(compareVersions('1.2.3+linux', '1.2.3+darwin'), 0);
  assert.equal(compareVersions('1.2.3+build.99', '1.2.3'), 0);
});

test('compareVersions ranks any prerelease below its release', () => {
  assert.equal(compareVersions('1.0.0-rc.9', '1.0.0'), -1);
  assert.equal(compareVersions('1.0.0', '1.0.0-rc.9'), 1);
});

test('compareVersions applies semver prerelease precedence', () => {
  const ascending = [
    '1.0.0-alpha',
    '1.0.0-alpha.1',
    '1.0.0-alpha.beta',
    '1.0.0-beta',
    '1.0.0-beta.2',
    '1.0.0-beta.11',
    '1.0.0-rc.1',
    '1.0.0',
  ];
  for (let i = 0; i + 1 < ascending.length; i++) {
    assert.equal(
      compareVersions(ascending[i], ascending[i + 1]),
      -1,
      `${ascending[i]} should sort before ${ascending[i + 1]}`,
    );
    assert.equal(compareVersions(ascending[i + 1], ascending[i]), 1);
  }
  // Numeric identifiers rank below alphanumeric ones.
  assert.equal(compareVersions('1.0.0-11', '1.0.0-a'), -1);
});

test('compareVersions rejects malformed versions with TypeError', () => {
  for (const bad of ['v1.2.3', '1.2', '1.2.3.4', '01.2.3', '1.2.3-', '1.2.x', '1.0.0-alpha.01']) {
    assert.throws(() => compareVersions(bad, '1.0.0'), TypeError, `expected TypeError for ${bad}`);
    assert.throws(() => compareVersions('1.0.0', bad), TypeError);
  }
  assert.throws(
    () => compareVersions('v1.2.3', '1.0.0'),
    (err: Error) => err instanceof TypeError && err.message.includes('v1.2.3'),
  );
});

// ---------------------------------------------------------------------------
// classifyChange
// ---------------------------------------------------------------------------

test('classifyChange reports the highest-precedence position that differs', () => {
  assert.deepEqual(classifyChange('1.2.3', '1.2.3'), { level: 'none', direction: 'none' });
  assert.deepEqual(classifyChange('1.2.3', '1.2.3+b2'), { level: 'none', direction: 'none' });
  assert.deepEqual(classifyChange('1.2.3', '1.2.4'), { level: 'patch', direction: 'upgrade' });
  assert.deepEqual(classifyChange('1.2.4', '1.2.3'), { level: 'patch', direction: 'downgrade' });
  assert.deepEqual(classifyChange('1.2.3', '1.3.0'), { level: 'minor', direction: 'upgrade' });
  assert.deepEqual(classifyChange('1.2.3', '2.0.0'), { level: 'major', direction: 'upgrade' });
  assert.deepEqual(classifyChange('2.0.0', '1.9.9'), { level: 'major', direction: 'downgrade' });
});

test('classifyChange calls a same-triple prerelease move "prerelease"', () => {
  assert.deepEqual(classifyChange('1.0.0-rc.1', '1.0.0'), { level: 'prerelease', direction: 'upgrade' });
  assert.deepEqual(classifyChange('1.0.0', '1.0.0-rc.1'), { level: 'prerelease', direction: 'downgrade' });
  assert.deepEqual(classifyChange('1.0.0-alpha', '1.0.0-beta'), { level: 'prerelease', direction: 'upgrade' });
});

// ---------------------------------------------------------------------------
// diffTrees
// ---------------------------------------------------------------------------

const before = {
  chalk: { version: '4.1.2' },
  rimraf: { version: '3.0.2' },
  yargs: {
    version: '17.7.2',
    deps: {
      'yargs-parser': { version: '21.1.1' },
      cliui: {
        version: '8.0.1',
        deps: { 'wrap-ansi': { version: '7.0.0' } },
      },
    },
  },
};

const after = {
  chalk: { version: '5.3.0' },
  'strip-ansi': { version: '7.1.0' },
  yargs: {
    version: '17.7.2',
    deps: {
      'yargs-parser': { version: '21.1.2' },
      cliui: {
        version: '8.0.1',
        deps: { 'wrap-ansi': { version: '6.2.0' } },
      },
    },
  },
};

test('diffTrees reports added, removed and changed nodes by path', () => {
  assert.deepEqual(diffTrees(before, after), {
    added: [{ path: 'strip-ansi', name: 'strip-ansi', version: '7.1.0' }],
    removed: [{ path: 'rimraf', name: 'rimraf', version: '3.0.2' }],
    changed: [
      { path: 'chalk', name: 'chalk', from: '4.1.2', to: '5.3.0', level: 'major', direction: 'upgrade' },
      { path: 'yargs > cliui > wrap-ansi', name: 'wrap-ansi', from: '7.0.0', to: '6.2.0', level: 'major', direction: 'downgrade' },
      { path: 'yargs > yargs-parser', name: 'yargs-parser', from: '21.1.1', to: '21.1.2', level: 'patch', direction: 'upgrade' },
    ],
    summary: {
      added: 1,
      removed: 1,
      changed: { major: 2, minor: 0, patch: 1, prerelease: 0 },
    },
  });
});

test('diffTrees of identical or empty trees is empty', () => {
  const empty = {
    added: [],
    removed: [],
    changed: [],
    summary: { added: 0, removed: 0, changed: { major: 0, minor: 0, patch: 0, prerelease: 0 } },
  };
  assert.deepEqual(diffTrees({}, {}), empty);
  assert.deepEqual(diffTrees(before, before), empty);
});

test('an added subtree lists every node it brings in', () => {
  const grown = {
    express: { version: '4.19.2', deps: { accepts: { version: '1.3.8' } } },
  };
  const report = diffTrees({}, grown);
  assert.deepEqual(report.added, [
    { path: 'express', name: 'express', version: '4.19.2' },
    { path: 'express > accepts', name: 'accepts', version: '1.3.8' },
  ]);
  assert.equal(report.summary.added, 2);

  const gone = diffTrees(grown, {});
  assert.deepEqual(gone.removed, [
    { path: 'express', name: 'express', version: '4.19.2' },
    { path: 'express > accepts', name: 'accepts', version: '1.3.8' },
  ]);
});

test('a changed parent still gets its children compared', () => {
  const b = {
    vite: {
      version: '5.0.0',
      deps: { rollup: { version: '4.9.0' }, esbuild: { version: '0.19.11' } },
    },
  };
  const a = {
    vite: {
      version: '5.1.0',
      deps: { rollup: { version: '4.9.0' }, esbuild: { version: '0.20.0' } },
    },
  };
  const report = diffTrees(b, a);
  assert.deepEqual(report.changed, [
    { path: 'vite', name: 'vite', from: '5.0.0', to: '5.1.0', level: 'minor', direction: 'upgrade' },
    { path: 'vite > esbuild', name: 'esbuild', from: '0.19.11', to: '0.20.0', level: 'minor', direction: 'upgrade' },
  ]);
  assert.deepEqual(report.summary.changed, { major: 0, minor: 2, patch: 0, prerelease: 0 });
});

test('a build-metadata-only difference is not a change, but children are still walked', () => {
  const b = { pkg: { version: '1.2.3+build.1' } };
  const a = { pkg: { version: '1.2.3+build.9', deps: { fresh: { version: '0.1.0' } } } };
  const report = diffTrees(b, a);
  assert.deepEqual(report.changed, []);
  assert.deepEqual(report.added, [{ path: 'pkg > fresh', name: 'fresh', version: '0.1.0' }]);
});

test('prerelease-to-release flips show up with level prerelease', () => {
  const report = diffTrees({ api: { version: '2.0.0-rc.1' } }, { api: { version: '2.0.0' } });
  assert.deepEqual(report.changed, [
    { path: 'api', name: 'api', from: '2.0.0-rc.1', to: '2.0.0', level: 'prerelease', direction: 'upgrade' },
  ]);
  assert.deepEqual(report.summary.changed, { major: 0, minor: 0, patch: 0, prerelease: 1 });
});

test('the same name at different paths is tracked per path', () => {
  const b = {
    lodash: { version: '4.17.20' },
    grunt: { version: '1.6.1', deps: { lodash: { version: '4.17.21' } } },
  };
  const a = {
    lodash: { version: '4.17.21' },
    grunt: { version: '1.6.1', deps: { lodash: { version: '4.17.21' } } },
  };
  const report = diffTrees(b, a);
  assert.deepEqual(report.changed, [
    { path: 'lodash', name: 'lodash', from: '4.17.20', to: '4.17.21', level: 'patch', direction: 'upgrade' },
  ]);
});

test('an empty deps object means the same as no deps key', () => {
  const report = diffTrees(
    { a: { version: '1.0.0', deps: {} } },
    { a: { version: '1.0.0' } },
  );
  assert.deepEqual(report, {
    added: [],
    removed: [],
    changed: [],
    summary: { added: 0, removed: 0, changed: { major: 0, minor: 0, patch: 0, prerelease: 0 } },
  });
});

// ---------------------------------------------------------------------------
// dedupeReport
// ---------------------------------------------------------------------------

test('dedupeReport lists packages resolved at more than one version', () => {
  const tree = {
    chalk: { version: '4.1.2' },
    ora: {
      version: '5.4.1',
      deps: {
        chalk: { version: '4.1.2' },
        'strip-ansi': { version: '6.0.1' },
      },
    },
    'log-update': {
      version: '6.0.0',
      deps: { 'strip-ansi': { version: '7.1.0' } },
    },
    'strip-ansi': { version: '7.1.0' },
  };
  // chalk appears twice but at ONE version, so it is not in the report.
  assert.deepEqual(dedupeReport(tree), [
    {
      name: 'strip-ansi',
      versions: [
        { version: '6.0.1', paths: ['ora > strip-ansi'] },
        { version: '7.1.0', paths: ['log-update > strip-ansi', 'strip-ansi'] },
      ],
    },
  ]);
});

test('dedupeReport sorts entries by name and versions by semver, prereleases first', () => {
  const tree = {
    a: { version: '1.0.0', deps: { debug: { version: '4.3.4' }, ms: { version: '2.1.3' } } },
    b: { version: '1.0.0', deps: { debug: { version: '4.4.0-beta.1' }, ms: { version: '2.0.0' } } },
    debug: { version: '4.4.0' },
  };
  assert.deepEqual(dedupeReport(tree), [
    {
      name: 'debug',
      versions: [
        { version: '4.3.4', paths: ['a > debug'] },
        { version: '4.4.0-beta.1', paths: ['b > debug'] },
        { version: '4.4.0', paths: ['debug'] },
      ],
    },
    {
      name: 'ms',
      versions: [
        { version: '2.0.0', paths: ['b > ms'] },
        { version: '2.1.3', paths: ['a > ms'] },
      ],
    },
  ]);
});

test('dedupeReport is empty when every package resolves to one version', () => {
  assert.deepEqual(dedupeReport({}), []);
  assert.deepEqual(
    dedupeReport({ x: { version: '1.0.0', deps: { y: { version: '2.0.0' } } } }),
    [],
  );
});
