export type RuleSeverity = 'off' | 'warn' | 'error';

export interface FlatConfig {
  readonly name?: string;
  readonly files?: readonly string[];
  readonly ignores?: readonly string[];
  readonly languageOptions?: {
    readonly parserOptions?: {
      readonly project?: string;
      readonly tsconfigRootDir?: string;
    };
  };
  readonly rules?: Readonly<Record<string, RuleSeverity>>;
}

export interface RepositoryFile {
  readonly path: string;
  readonly source: string;
}

export interface ProjectContract {
  readonly path: string;
  readonly includes: readonly string[];
}

export interface Diagnostic {
  readonly file: string;
  readonly rule: string;
  readonly severity: 'warning' | 'error';
  readonly message: string;
}

interface ResolvedConfig {
  project?: string;
  readonly rules: Record<string, RuleSeverity>;
}

function normalized(value: string): string {
  return value.replaceAll('\\', '/').replace(/^\.\//, '');
}

function globMatches(patternValue: string, pathValue: string): boolean {
  const pattern = normalized(patternValue);
  const path = normalized(pathValue);
  let expression = '^';
  for (let index = 0; index < pattern.length;) {
    if (pattern.startsWith('**/', index)) {
      expression += '(?:.*/)?';
      index += 3;
    } else if (pattern.startsWith('**', index)) {
      expression += '.*';
      index += 2;
    } else if (pattern[index] === '*') {
      expression += '[^/]*';
      index += 1;
    } else {
      expression += pattern[index].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      index += 1;
    }
  }
  return new RegExp(`${expression}$`).test(path);
}

function isGlobalIgnore(entry: FlatConfig): boolean {
  return entry.ignores !== undefined
    && entry.files === undefined
    && entry.languageOptions === undefined
    && entry.rules === undefined;
}

function globallyIgnored(config: readonly FlatConfig[], path: string): boolean {
  return config.some((entry) =>
    isGlobalIgnore(entry) && entry.ignores!.some((pattern) => globMatches(pattern, path))
  );
}

function resolveConfig(config: readonly FlatConfig[], path: string): ResolvedConfig | null {
  const resolved: ResolvedConfig = { rules: {} };
  let matched = false;
  for (const entry of config) {
    if (isGlobalIgnore(entry)) continue;
    const filesMatch = entry.files?.some((pattern) => globMatches(pattern, path)) ?? false;
    if (!filesMatch) continue;
    if (entry.ignores?.some((pattern) => globMatches(pattern, path))) continue;
    matched = true;
    const project = entry.languageOptions?.parserOptions?.project;
    if (project !== undefined) resolved.project = normalized(project);
    Object.assign(resolved.rules, entry.rules ?? {});
  }
  return matched ? resolved : null;
}

function owningProject(path: string, projects: readonly ProjectContract[]): string | undefined {
  for (const project of projects) {
    const base = normalized(project.path).replace(/\/[^/]+$/, '');
    if (project.includes.some((include) => globMatches(`${base}/${include}`, path))) {
      return normalized(project.path);
    }
  }
  return undefined;
}

function diagnostic(
  file: string,
  rule: string,
  severity: RuleSeverity,
  message: string,
): Diagnostic | null {
  if (severity === 'off') return null;
  return {
    file,
    rule,
    severity: severity === 'warn' ? 'warning' : 'error',
    message,
  };
}

export function lintRepository(
  config: readonly FlatConfig[],
  files: readonly RepositoryFile[],
  projects: readonly ProjectContract[],
): Diagnostic[] {
  const output: Diagnostic[] = [];
  for (const file of files) {
    const path = normalized(file.path);
    if (!path.endsWith('.ts') || globallyIgnored(config, path)) continue;
    const resolved = resolveConfig(config, path);
    if (resolved === null) {
      output.push({
        file: path,
        rule: 'flat_config_missing',
        severity: 'error',
        message: 'TypeScript file has no matching flat configuration',
      });
      continue;
    }
    const owner = owningProject(path, projects);
    if (resolved.project === undefined) {
      output.push({
        file: path,
        rule: 'typescript_project_missing',
        severity: 'error',
        message: 'type-aware linting requires parserOptions.project',
      });
      continue;
    }
    if (owner === undefined || resolved.project !== owner) {
      output.push({
        file: path,
        rule: 'typescript_project_boundary',
        severity: 'error',
        message: `project ${resolved.project} does not own ${path}`,
      });
      continue;
    }

    const rules = resolved.rules;
    if (/\bPromise\.resolve\([^;]*\);/.test(file.source)
        && !/\b(?:await|void)\s+Promise\.resolve/.test(file.source)) {
      const found = diagnostic(
        path,
        '@typescript-eslint/no-floating-promises',
        rules['@typescript-eslint/no-floating-promises'] ?? 'off',
        'Promises must be awaited, returned, or explicitly ignored',
      );
      if (found) output.push(found);
    }
    if (/:\s*any\b/.test(file.source)) {
      const found = diagnostic(
        path,
        '@typescript-eslint/no-explicit-any',
        rules['@typescript-eslint/no-explicit-any'] ?? 'off',
        'Unexpected any',
      );
      if (found) output.push(found);
    }
    if (/\bconsole\./.test(file.source)) {
      const found = diagnostic(
        path,
        'no-console',
        rules['no-console'] ?? 'off',
        'Unexpected console statement',
      );
      if (found) output.push(found);
    }
  }
  return output;
}
