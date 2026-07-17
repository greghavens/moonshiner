/** Plugin registry. Registration order is execution order; names must be
 * unique so a site config can't accidentally load a plugin twice. */
import type { Plugin } from './types.ts';

export class PluginRegistry {
  plugins: Plugin[] = [];

  register(plugin: Plugin): void {
    if (!plugin.name || typeof plugin.name !== 'string') {
      throw new Error('plugin is missing a name');
    }
    if (this.plugins.some((p) => p.name === plugin.name)) {
      throw new Error(`duplicate plugin name: ${plugin.name}`);
    }
    this.plugins.push(plugin);
  }

  registerAll(plugins: Plugin[]): void {
    for (const plugin of plugins) this.register(plugin);
  }

  filters(): Plugin[] {
    return this.plugins.filter((p) => p.includePage !== undefined);
  }

  transforms(): Plugin[] {
    return this.plugins.filter((p) => p.transform !== undefined);
  }

  renderers(): Plugin[] {
    return this.plugins.filter((p) => p.render !== undefined);
  }

  emitters(): Plugin[] {
    return this.plugins.filter((p) => p.emit !== undefined);
  }
}
