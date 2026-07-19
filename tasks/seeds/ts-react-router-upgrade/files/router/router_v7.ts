export interface RedirectResult {
  readonly kind: 'redirect';
  readonly to: string;
  readonly replace: boolean;
}

export function redirect(
  to: string,
  options: { replace?: boolean } = {},
): RedirectResult {
  return { kind: 'redirect', to, replace: options.replace === true };
}

export interface RouteObject {
  readonly id: string;
  readonly path?: string;
  readonly index?: boolean;
  readonly element?: string;
  readonly loader?: () => RedirectResult | null;
  readonly children?: readonly RouteObject[];
}

export interface RouteMatch {
  readonly id: string;
  readonly element?: string;
  readonly params: Readonly<Record<string, string>>;
}

export interface NavigationOutcome {
  readonly status: 'committed' | 'blocked' | 'reset';
  readonly location: string;
  readonly blocker?: string;
}

export interface BlockerTransition {
  readonly currentLocation: string;
  readonly nextLocation: string;
}

export type BlockerPredicate = (transition: BlockerTransition) => boolean;

export interface BlockerHandle {
  readonly state: 'unblocked' | 'blocked';
  readonly location: string | null;
  proceed(): NavigationOutcome;
  reset(): NavigationOutcome;
}

export class RouterContractError extends Error {}

interface PendingNavigation {
  to: string;
  replace: boolean;
  blocker: string;
}

function normalized(path: string): string {
  if (!path.startsWith('/')) throw new TypeError(`absolute route required: ${path}`);
  if (path.length > 1 && path.endsWith('/')) return path.slice(0, -1);
  return path;
}

function validateRoutes(routes: readonly RouteObject[]): void {
  for (const route of routes) {
    const value = route as RouteObject & Record<string, unknown>;
    for (const removed of ['redirect', 'component', 'render', 'prompt']) {
      if (Object.prototype.hasOwnProperty.call(value, removed)) {
        throw new RouterContractError(`route ${route.id} uses removed ${removed} API`);
      }
    }
    if (route.index === true && route.path !== undefined) {
      throw new RouterContractError(`index route ${route.id} cannot declare a path`);
    }
    if (route.children !== undefined) validateRoutes(route.children);
  }
}

function matchBranch(
  routes: readonly RouteObject[],
  segments: readonly string[],
  offset: number,
  inherited: Readonly<Record<string, string>>,
): RouteMatch[] | null {
  for (const route of routes) {
    const params: Record<string, string> = { ...inherited };
    let nextOffset = offset;
    if (route.index === true) {
      if (offset !== segments.length) continue;
    } else if (route.path === '/') {
      if (offset !== 0) continue;
    } else if (route.path !== undefined) {
      if (offset >= segments.length) continue;
      const actual = segments[offset];
      if (route.path.startsWith(':')) {
        params[route.path.slice(1)] = decodeURIComponent(actual);
      } else if (route.path !== actual) {
        continue;
      }
      nextOffset++;
    }

    const here: RouteMatch = { id: route.id, element: route.element, params };
    if (route.children !== undefined) {
      const child = matchBranch(route.children, segments, nextOffset, params);
      if (child !== null) return [here, ...child];
    }
    if (nextOffset === segments.length && route.index !== true) return [here];
    if (route.index === true) return [here];
  }
  return null;
}

function terminalRoute(routes: readonly RouteObject[], matches: readonly RouteMatch[]): RouteObject {
  let candidates = routes;
  let found: RouteObject | undefined;
  for (const match of matches) {
    found = candidates.find((route) => route.id === match.id);
    if (found === undefined) throw new Error(`route ${match.id} disappeared`);
    candidates = found.children ?? [];
  }
  if (found === undefined) throw new Error('empty route match');
  return found;
}

export class MemoryDataRouter {
  private readonly routes: readonly RouteObject[];
  private readonly entriesValue: string[];
  private readonly blockers = new Map<string, BlockerPredicate>();
  private pending: PendingNavigation | null = null;
  private index: number;

  constructor(routes: readonly RouteObject[], initialEntries: readonly string[] = ['/']) {
    validateRoutes(routes);
    if (initialEntries.length === 0) throw new TypeError('initialEntries cannot be empty');
    this.routes = routes;
    this.entriesValue = initialEntries.map(normalized);
    this.index = this.entriesValue.length - 1;
    this.resolve(this.location);
  }

  get location(): string {
    return this.entriesValue[this.index];
  }

  get entries(): readonly string[] {
    return [...this.entriesValue];
  }

  matches(): readonly RouteMatch[] {
    return this.resolve(this.location).matches;
  }

  navigate(to: string, options: { replace?: boolean } = {}): NavigationOutcome {
    const next = normalized(to);
    if (this.pending !== null) throw new Error('a navigation is already blocked');
    for (const [key, predicate] of this.blockers) {
      if (predicate({ currentLocation: this.location, nextLocation: next })) {
        this.pending = { to: next, replace: options.replace === true, blocker: key };
        return { status: 'blocked', location: this.location, blocker: key };
      }
    }
    return this.commit(next, options.replace === true);
  }

  registerBlocker(key: string, predicate: BlockerPredicate): () => void {
    if (this.blockers.has(key)) throw new Error(`blocker ${key} already registered`);
    this.blockers.set(key, predicate);
    return () => {
      this.blockers.delete(key);
      if (this.pending?.blocker === key) this.pending = null;
    };
  }

  getBlocker(key: string): BlockerHandle {
    const pending = this.pending?.blocker === key ? this.pending : null;
    return {
      state: pending === null ? 'unblocked' : 'blocked',
      location: pending?.to ?? null,
      proceed: () => {
        if (this.pending?.blocker !== key) {
          return { status: 'committed', location: this.location };
        }
        const accepted = this.pending;
        this.pending = null;
        return this.commit(accepted.to, accepted.replace);
      },
      reset: () => {
        if (this.pending?.blocker === key) this.pending = null;
        return { status: 'reset', location: this.location };
      },
    };
  }

  private commit(requested: string, replaceEntry: boolean): NavigationOutcome {
    let destination = requested;
    let replace = replaceEntry;
    for (let redirects = 0; redirects < 5; redirects++) {
      const resolution = this.resolve(destination);
      if (resolution.redirect === null) break;
      destination = normalized(resolution.redirect.to);
      replace = replace || resolution.redirect.replace;
      if (redirects === 4) throw new Error('redirect loop');
    }
    this.resolve(destination);
    if (replace) {
      this.entriesValue[this.index] = destination;
    } else if (destination !== this.location) {
      this.entriesValue.splice(this.index + 1);
      this.entriesValue.push(destination);
      this.index++;
    }
    return { status: 'committed', location: destination };
  }

  private resolve(path: string): { matches: RouteMatch[]; redirect: RedirectResult | null } {
    const segments = path === '/' ? [] : path.slice(1).split('/');
    const matches = matchBranch(this.routes, segments, 0, {});
    if (matches === null) throw new Error(`no route for ${path}`);
    const loaderResult = terminalRoute(this.routes, matches).loader?.() ?? null;
    return { matches, redirect: loaderResult };
  }
}

