import type { FlatConfig } from './lint/flat_runtime.ts';
import { strictTypeCheckedRules } from './lint/rules.ts';

const config: readonly FlatConfig[] = [
  {
    name: 'typescript packages',
    files: ['packages/**/*.ts'],
    ignores: ['**/generated/**', '**/dist/**'],
    languageOptions: {
      parserOptions: {
        project: './tsconfig.json',
        tsconfigRootDir: '.',
      },
    },
    rules: strictTypeCheckedRules,
  },
  {
    name: 'tests',
    files: ['**/*.test.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      'no-console': 'off',
    },
  },
];

export default config;
