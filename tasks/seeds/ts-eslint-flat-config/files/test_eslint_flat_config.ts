import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import config from './eslint.config.ts';
import { runCi } from './lint/ci.ts';
import {
  lintRepository,
  type FlatConfig,
  type ProjectContract,
  type RepositoryFile,
} from './lint/flat_runtime.ts';

const paths = [
  'packages/api/src/handler.ts',
  'packages/api/test/handler.test.ts',
  'packages/api/generated/schema.ts',
  'packages/web/src/view.ts',
  'packages/web/test/view.test.ts',
  'packages/web/generated/routes.ts',
  'packages/web/dist/bundle.ts',
] as const;

const files: RepositoryFile[] = paths.map((path) => ({
  path,
  source: readFileSync(new URL(path, import.meta.url), 'utf8'),
}));

const projects: ProjectContract[] = [
  {
    path: 'packages/api/tsconfig.json',
    includes: JSON.parse(
      readFileSync(new URL('./packages/api/tsconfig.json', import.meta.url), 'utf8'),
    ).include,
  },
  {
    path: 'packages/web/tsconfig.json',
    includes: JSON.parse(
      readFileSync(new URL('./packages/web/tsconfig.json', import.meta.url), 'utf8'),
    ).include,
  },
];

test('repository is clean under the migrated flat configuration', () => {
  assert.deepEqual(runCi(config, files, projects), {
    exitCode: 0,
    warnings: 0,
    errors: 0,
    diagnostics: [],
  });
});

test('each package uses its own type-aware project boundary', () => {
  const wrongProject: readonly FlatConfig[] = [
    { name: 'generated', ignores: ['**/generated/**', '**/dist/**'] },
    {
      name: 'one project for everything',
      files: ['packages/**/*.ts'],
      languageOptions: { parserOptions: { project: 'packages/api/tsconfig.json' } },
      rules: {
        '@typescript-eslint/no-floating-promises': 'error',
        '@typescript-eslint/no-explicit-any': 'error',
      },
    },
    ...config.filter((entry) => entry.name === 'tests'),
  ];
  const diagnostics = lintRepository(wrongProject, files, projects);
  assert.ok(diagnostics.some((item) =>
    item.file === 'packages/web/src/view.ts'
      && item.rule === 'typescript_project_boundary'
      && item.message.includes('packages/api/tsconfig.json')
  ));
});

test('generated and distribution ignores must be global', () => {
  const localOnly: readonly FlatConfig[] = config
    .filter((entry) => entry.ignores === undefined)
    .map((entry) => entry.name?.includes('api')
      ? { ...entry, ignores: ['**/generated/**', '**/dist/**'] }
      : entry);
  const diagnostics = lintRepository(localOnly, files, projects);
  for (const path of [
    'packages/api/generated/schema.ts',
    'packages/web/generated/routes.ts',
    'packages/web/dist/bundle.ts',
  ]) {
    assert.ok(
      diagnostics.some((item) => item.file === path),
      `${path} was accidentally hidden by a non-global ignore`,
    );
  }
});

test('test overrides retain project checks but allow documented test syntax', () => {
  const withoutTests = config.filter((entry) => entry.name !== 'tests');
  const result = runCi(withoutTests, files, projects);
  for (const path of ['packages/api/test/handler.test.ts', 'packages/web/test/view.test.ts']) {
    assert.ok(result.diagnostics.some((item) =>
      item.file === path && item.rule === '@typescript-eslint/no-explicit-any'
    ));
    assert.ok(result.diagnostics.some((item) => item.file === path && item.rule === 'no-console'));
    assert.ok(!result.diagnostics.some((item) =>
      item.file === path && item.rule === 'typescript_project_missing'
    ));
  }
});

test('zero-warning CI fails even when there are no lint errors', () => {
  const warningFile: RepositoryFile = {
    path: 'packages/api/src/diagnostic.ts',
    source: "export function report(): void { console.log('diagnostic'); }\n",
  };
  const result = runCi(config, [...files, warningFile], projects);
  assert.equal(result.errors, 0);
  assert.equal(result.warnings, 1);
  assert.equal(result.exitCode, 1);
  assert.deepEqual(result.diagnostics[0], {
    file: 'packages/api/src/diagnostic.ts',
    rule: 'no-console',
    severity: 'warning',
    message: 'Unexpected console statement',
  });
});

test('protected notes record cascade, ignores, projects, overrides, and CI behavior', () => {
  const notes = readFileSync(
    new URL('./contracts/eslint_9_flat_config.md', import.meta.url),
    'utf8',
  );
  for (const phrase of [
    '`eslint.config.ts`',
    'no `files` selector',
    "owning package's tsconfig",
    'Later matching objects override',
    'Generated and distribution trees',
    '`maxWarnings: 0`',
  ]) assert.ok(notes.includes(phrase), phrase);
});
