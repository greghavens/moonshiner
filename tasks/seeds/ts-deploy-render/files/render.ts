import { deepMerge } from './merge.ts';

export type ServiceConfig = {
  image?: string;
  replicas?: number;
  env?: Record<string, string>;
  ports?: number[];
  disabled?: boolean;
};

export type Manifest = {
  defaults?: ServiceConfig;
  services?: Record<string, ServiceConfig>;
  deployOrder?: string[];
};

export type RenderedService = {
  name: string;
  image: string;
  replicas: number;
  env: Record<string, string>;
  ports: number[];
};

/**
 * Merge manifest files (base first, overlays after) and render the final
 * service list. When the merged manifest has a deployOrder, exactly those
 * services deploy, in that order; otherwise every service deploys in
 * alphabetical order. Disabled services never render.
 */
export function renderManifest(files: Manifest[]): RenderedService[] {
  let merged: Manifest = {};
  for (const file of files) {
    merged = deepMerge(merged, file);
  }

  const services = merged.services ?? {};
  const defaults = merged.defaults ?? {};
  const rendered: RenderedService[] = [];
  const emitted = new Set<string>();

  const emit = (name: string) => {
    if (emitted.has(name)) return;
    emitted.add(name);
    const config = services[name];
    if (config === undefined) {
      throw new Error(`deployOrder references unknown service "${name}"`);
    }
    if (config.disabled) return;
    const resolved = deepMerge({ ...defaults }, config);
    rendered.push({
      name,
      image: resolved.image ?? 'scratch',
      replicas: resolved.replicas ?? 1,
      env: resolved.env ?? {},
      ports: resolved.ports ?? [],
    });
  };

  if (merged.deployOrder) {
    for (const name in merged.deployOrder) {
      emit(name);
    }
  } else {
    for (const name of Object.keys(services).sort()) {
      emit(name);
    }
  }
  return rendered;
}
