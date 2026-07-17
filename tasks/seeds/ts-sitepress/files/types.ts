/** Shared shapes for the sitepress build pipeline. */

export type MetaValue = string | number | boolean | string[];

export interface SiteConfig {
  /** Site title, used by the layout and the feed channel. */
  title: string;
  /** Canonical origin for absolute urls (no trailing slash). */
  baseUrl: string;
  /** true: about.md -> about/index.html; false: about.md -> about.html */
  prettyUrls: boolean;
  /** Maximum number of items in feed.xml. */
  feedLimit: number;
}

export interface Page {
  /** Source path inside the content tree, e.g. "posts/first.md". */
  sourcePath: string;
  /** Output path in the file map, e.g. "posts/first/index.html". */
  outPath: string;
  /** Absolute canonical url derived from baseUrl + outPath. */
  url: string;
  title: string;
  /** ISO date "YYYY-MM-DD" or null for undated pages. */
  date: string | null;
  tags: string[];
  /** Full parsed front matter, including keys the core does not interpret. */
  meta: Record<string, MetaValue>;
  /** Rendered body html (markdown already converted, no chrome). */
  html: string;
  /** Short plain-text summary; filled in by the excerpt plugin. */
  excerpt: string;
}

export interface BuildContext {
  config: SiteConfig;
  /** Pages that made it through the filter phase, sorted by sourcePath. */
  pages: Page[];
}

export interface Plugin {
  name: string;
  /** Filter phase: return false to drop the page from the build. */
  includePage?(page: Page, ctx: BuildContext): boolean;
  /** Transform phase: return the (possibly replaced) page. */
  transform?(page: Page, ctx: BuildContext): Page;
  /** Render phase: return full page html, or null to pass. */
  render?(page: Page, ctx: BuildContext): string | null;
  /** Emit phase: extra output files keyed by path. */
  emit?(ctx: BuildContext): Record<string, string>;
}

export interface BuildOutput {
  /** path -> file contents; the deploy job writes this map verbatim. */
  files: Record<string, string>;
}
