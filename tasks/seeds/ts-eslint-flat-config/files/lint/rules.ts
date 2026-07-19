import type { RuleSeverity } from './flat_runtime.ts';

export const strictTypeCheckedRules: Readonly<Record<string, RuleSeverity>> = {
  '@typescript-eslint/no-floating-promises': 'error',
  '@typescript-eslint/no-explicit-any': 'error',
  'no-console': 'warn',
};
