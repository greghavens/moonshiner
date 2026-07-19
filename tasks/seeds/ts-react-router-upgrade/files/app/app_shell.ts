import type { MemoryDataRouter } from '../router/router_v7.ts';

export function renderedLayouts(router: MemoryDataRouter): string[] {
  return router.matches().flatMap((match) => match.element === undefined ? [] : [match.element]);
}

