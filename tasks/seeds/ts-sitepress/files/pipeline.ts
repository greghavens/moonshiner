/** The build pipeline: filter -> transform -> render -> emit.
 *
 * Filters run first so transforms and renderers only ever see pages that
 * are actually part of the build; emitters run last over the final page
 * set and contribute extra files (sitemap, feed, ...).
 */
import type { PluginRegistry } from './registry.ts';
import type { BuildContext, Page, Plugin, SiteConfig } from './types.ts';

export interface PipelineResult {
  /** outPath -> full page html */
  rendered: Record<string, string>;
  /** extra files from emitters, path -> contents */
  extras: Record<string, string>;
}

function included(page: Page, ctx: BuildContext, filters: Plugin[]): boolean {
  for (const plugin of filters) {
    if (plugin.includePage!(page, ctx) === false) return false;
  }
  return true;
}

export function runPipeline(
  config: SiteConfig,
  pages: Page[],
  registry: PluginRegistry,
): PipelineResult {
  const probe: BuildContext = { config, pages: [...pages] };
  const filters = registry.filters();
  const kept = pages.filter((page) => included(page, probe, filters));

  const ctx: BuildContext = { config, pages: kept };
  for (const plugin of registry.transforms()) {
    ctx.pages = ctx.pages.map((page) => plugin.transform!(page, ctx));
  }

  const rendered: Record<string, string> = {};
  for (const page of ctx.pages) {
    let html: string | null = null;
    for (const plugin of registry.renderers()) {
      html = plugin.render!(page, ctx);
      if (html !== null) break;
    }
    if (html === null) {
      throw new Error(`no renderer produced output for ${page.sourcePath}`);
    }
    if (rendered[page.outPath] !== undefined) {
      throw new Error(`output collision at ${page.outPath}`);
    }
    rendered[page.outPath] = html;
  }

  const extras: Record<string, string> = {};
  for (const plugin of registry.emitters()) {
    const emitted = plugin.emit!(ctx);
    for (const path of Object.keys(emitted)) {
      if (extras[path] !== undefined) {
        throw new Error(`plugin ${plugin.name} emitted duplicate file: ${path}`);
      }
      extras[path] = emitted[path];
    }
  }

  return { rendered, extras };
}
