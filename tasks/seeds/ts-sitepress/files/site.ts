/** buildSite: the whole tool as one pure call.
 *
 *   buildSite(rawConfig, sources)            -> { files }
 *   buildSite(rawConfig, sources, plugins)   -> built-ins + your plugins
 *
 * `sources` maps content paths to file contents; the returned file map is
 * what lands in object storage, byte for byte.
 */
import { loadConfig } from './config.ts';
import { scanContent } from './content.ts';
import { runPipeline } from './pipeline.ts';
import { excerptPlugin } from './plugins/excerpt.ts';
import { feedPlugin } from './plugins/feed.ts';
import { layoutPlugin } from './plugins/layout.ts';
import { sitemapPlugin } from './plugins/sitemap.ts';
import { PluginRegistry } from './registry.ts';
import { writeSite } from './writer.ts';
import type { BuildOutput, Plugin } from './types.ts';

export function defaultPlugins(): Plugin[] {
  return [excerptPlugin, layoutPlugin, sitemapPlugin, feedPlugin];
}

export function buildSite(
  rawConfig: unknown,
  sources: Record<string, string>,
  extraPlugins: Plugin[] = [],
): BuildOutput {
  const config = loadConfig(rawConfig);
  const { pages, assets } = scanContent(sources, config);

  const registry = new PluginRegistry();
  registry.registerAll(defaultPlugins());
  registry.registerAll(extraPlugins);

  const { rendered, extras } = runPipeline(config, pages, registry);
  return writeSite(rendered, assets, extras);
}
