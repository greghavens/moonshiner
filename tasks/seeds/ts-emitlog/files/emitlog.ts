// emitlog.ts — linear workflow engine for the ops workflow DSL.
//
// Executes `do:` task lists strictly in order: `set:` evaluates a map into
// the context, `call:` invokes an injected handler. The expression language
// is the single-expression subset: a string that is exactly one
// `${ .path }` yields the raw value; everything else is a literal.
//
// Synchronous and deterministic: no timers, no I/O, handlers are the only
// side effects.

import { parse } from 'yaml';

export class LoadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LoadError';
  }
}

type Json = unknown;
type Args = Record<string, Json>;
type Handler = (args: Args) => Json;
type Scope = Record<string, Json>;

export interface RunOptions {
  input?: Json;
  handlers?: Record<string, Handler>;
}

export interface RunResult {
  context: Scope;
}

// ----------------------------------------------------------------- loading

interface Task {
  name: string;
  kind: 'set' | 'call';
  set?: Json;
  call?: string;
  with?: Json;
}

interface Workflow {
  namespace: string;
  name: string;
  tasks: Task[];
}

function isPlainObject(v: Json): v is Scope {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function loadTask(name: string, body: Json, where: string): Task {
  if (!isPlainObject(body)) {
    throw new LoadError(`${where}: task body must be a map`);
  }
  const typeKeys = (['set', 'call'] as const).filter((k) => k in body);
  if (typeKeys.length !== 1) {
    throw new LoadError(
      `${where}: task needs exactly one of set/call, got ${typeKeys.length}`);
  }
  const kind = typeKeys[0];
  const allowed = new Set<string>([kind]);
  if (kind === 'call') allowed.add('with');
  for (const key of Object.keys(body)) {
    if (!allowed.has(key)) {
      throw new LoadError(`${where}: unknown key "${key}" on a ${kind} task`);
    }
  }

  const task: Task = { name, kind };
  if (kind === 'set') {
    if (!isPlainObject(body['set'])) {
      throw new LoadError(`${where}: set must be a map`);
    }
    task.set = body['set'];
  } else {
    const call = body['call'];
    if (typeof call !== 'string' || call === '') {
      throw new LoadError(`${where}: call must be a non-empty string`);
    }
    task.call = call;
    task.with = 'with' in body ? body['with'] : {};
    if (!isPlainObject(task.with)) {
      throw new LoadError(`${where}: with must be a map`);
    }
  }
  return task;
}

function loadWorkflow(source: string): Workflow {
  let doc: Json;
  try {
    doc = parse(source);
  } catch (err) {
    throw new LoadError(`invalid YAML: ${(err as Error).message}`);
  }
  if (!isPlainObject(doc)) {
    throw new LoadError('workflow must be a YAML map');
  }
  const header = doc['document'];
  if (!isPlainObject(header)) {
    throw new LoadError('missing document header');
  }
  if (header['dsl'] !== '1.0') {
    throw new LoadError(`unsupported dsl version ${JSON.stringify(header['dsl'])}`);
  }
  for (const field of ['namespace', 'name']) {
    const v = header[field];
    if (typeof v !== 'string' || v === '') {
      throw new LoadError(`document.${field} must be a non-empty string`);
    }
  }
  for (const key of Object.keys(doc)) {
    if (key !== 'document' && key !== 'do') {
      throw new LoadError(`unknown top-level key "${key}"`);
    }
  }

  const list = doc['do'];
  if (!Array.isArray(list) || list.length === 0) {
    throw new LoadError('do must be a non-empty list');
  }
  const seen = new Set<string>();
  const tasks: Task[] = [];
  for (const entry of list) {
    if (!isPlainObject(entry) || Object.keys(entry).length !== 1) {
      throw new LoadError('each do entry must be a single-key map');
    }
    const name = Object.keys(entry)[0];
    if (name === 'input') {
      throw new LoadError('task name "input" is reserved');
    }
    if (seen.has(name)) {
      throw new LoadError(`duplicate task name "${name}"`);
    }
    seen.add(name);
    tasks.push(loadTask(name, entry[name], `do/${name}`));
  }
  return {
    namespace: header['namespace'] as string,
    name: header['name'] as string,
    tasks,
  };
}

// -------------------------------------------------------------- expressions

const SINGLE_EXPR_RE = /^\$\{\s*([^{}]+?)\s*\}$/;
const SEGMENT_RE = /^(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\])/;

function parsePath(path: string): Array<string | number> {
  if (!path.startsWith('.')) {
    throw new LoadError(`bad expression path ${JSON.stringify(path)}`);
  }
  if (path === '.') return [];
  let rest = path;
  const segments: Array<string | number> = [];
  while (rest !== '') {
    const m = SEGMENT_RE.exec(rest);
    if (!m) {
      throw new LoadError(`bad expression path ${JSON.stringify(path)}`);
    }
    segments.push(m[1] !== undefined ? m[1] : Number(m[2]));
    rest = rest.slice(m[0].length);
  }
  return segments;
}

function resolve(context: Scope, path: string): Json {
  let current: Json = context;
  for (const seg of parsePath(path)) {
    if (typeof seg === 'number') {
      if (!Array.isArray(current) || seg >= current.length) return undefined;
      current = current[seg];
    } else {
      if (!isPlainObject(current) || !(seg in current)) return undefined;
      current = current[seg];
    }
  }
  return current;
}

function evaluate(value: Json, context: Scope): Json {
  if (typeof value === 'string') {
    const m = SINGLE_EXPR_RE.exec(value);
    if (m) return resolve(context, m[1]);
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((v) => evaluate(v, context));
  }
  if (isPlainObject(value)) {
    const out: Scope = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = evaluate(v, context);
    }
    return out;
  }
  return value;
}

// -------------------------------------------------------------------- entry

export function runWorkflow(source: string,
                            options: RunOptions = {}): RunResult {
  const wf = loadWorkflow(source);
  const context: Scope = { input: options.input ?? {} };
  for (const task of wf.tasks) {
    if (task.kind === 'set') {
      context[task.name] = evaluate(task.set!, context);
    } else {
      const handler = (options.handlers ?? {})[task.call!];
      if (typeof handler !== 'function') {
        throw new Error(`unknown handler "${task.call}"`);
      }
      context[task.name] = handler(evaluate(task.with!, context) as Args);
    }
  }
  return { context };
}
