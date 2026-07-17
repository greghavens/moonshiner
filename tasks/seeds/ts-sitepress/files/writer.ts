/** Assembles the final file map. Pure: no filesystem here, ever — the
 * deploy job owns IO. Collisions are hard errors because whichever file
 * silently won used to depend on plugin order. */
import type { BuildOutput } from './types.ts';

export function writeSite(
  rendered: Record<string, string>,
  assets: Record<string, string>,
  extras: Record<string, string>,
): BuildOutput {
  const files: Record<string, string> = {};
  const put = (path: string, contents: string, origin: string) => {
    if (path === '' || path.startsWith('/') || path.includes('..')) {
      throw new Error(`${origin} produced an invalid output path: ${path}`);
    }
    if (files[path] !== undefined) {
      throw new Error(`output collision at ${path}`);
    }
    files[path] = contents;
  };

  for (const path of Object.keys(rendered)) put(path, rendered[path], 'renderer');
  for (const path of Object.keys(assets)) put(path, assets[path], 'asset copy');
  for (const path of Object.keys(extras)) put(path, extras[path], 'emitter');

  const sorted: Record<string, string> = {};
  for (const path of Object.keys(files).sort()) sorted[path] = files[path];
  return { files: sorted };
}
