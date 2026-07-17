// Breadcrumb trail builder for the docs site header. Turns a URL path into
// a list of crumbs, labelling each one from an href-keyed label map and
// falling back to humanizing the path segment ("getting-started" ->
// "Getting Started").

export interface Crumb {
  label: string;
  href: string;
  current: boolean;
}

export interface BreadcrumbOptions {
  /** Labels keyed by full href, e.g. { '/docs/guides': 'Guides' }. */
  labels?: Record<string, string>;
  homeLabel?: string;
}

function humanize(segment: string): string {
  return segment
    .split(/[-_]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

function cleanPath(path: string): string {
  if (!path.startsWith('/')) {
    throw new Error(`breadcrumb path must start with "/": "${path}"`);
  }
  const withoutQuery = path.split(/[?#]/)[0];
  return withoutQuery !== '/' && withoutQuery.endsWith('/')
    ? withoutQuery.slice(0, -1)
    : withoutQuery;
}

export function buildBreadcrumbs(path: string, options: BreadcrumbOptions = {}): Crumb[] {
  const cleaned = cleanPath(path);
  const labels = options.labels ?? {};
  const crumbs: Crumb[] = [
    { label: options.homeLabel ?? 'Home', href: '/', current: cleaned === '/' },
  ];

  if (cleaned === '/') return crumbs;

  const segments = cleaned.slice(1).split('/');
  let href = '';
  for (let i = 0; i < segments.length; i++) {
    href += `/${segments[i]}`;
    crumbs.push({
      label: labels[href] ?? humanize(segments[i]),
      href,
      current: i === segments.length - 1,
    });
  }
  return crumbs;
}
