import {
  lintRepository,
  type Diagnostic,
  type FlatConfig,
  type ProjectContract,
  type RepositoryFile,
} from './flat_runtime.ts';

export interface CiResult {
  readonly exitCode: number;
  readonly warnings: number;
  readonly errors: number;
  readonly diagnostics: readonly Diagnostic[];
}

export function runCi(
  config: readonly FlatConfig[],
  files: readonly RepositoryFile[],
  projects: readonly ProjectContract[],
): CiResult {
  const diagnostics = lintRepository(config, files, projects);
  const warnings = diagnostics.filter((item) => item.severity === 'warning').length;
  const errors = diagnostics.filter((item) => item.severity === 'error').length;
  return {
    exitCode: errors > 0 || warnings > 0 ? 1 : 0,
    warnings,
    errors,
    diagnostics,
  };
}
