# sitepress

Plugin-based static-site tool behind the team handbook and the release-notes
microsites. Plain TypeScript run directly under node (type stripping) — no
npm dependencies, no bundler. The build is a pure function: sources in,
file map out; the deploy job writes the map to object storage.

## Layout

    types.ts            shared interfaces (Page, Plugin, SiteConfig, ...)
    config.ts           config validation + defaults
    frontmatter.ts      `---` front-matter block parser
    markdown.ts         the markdown subset we render
    content.ts          source scan: .md -> Page, everything else -> asset
    paths.ts            slugs, output paths, canonical urls
    html.ts             escaping helpers (html + xml)
    registry.ts         plugin registry (ordering, duplicate detection)
    pipeline.ts         the build pipeline: filter -> transform -> render -> emit
    writer.ts           assembles the final file map, collision checks
    site.ts             buildSite(): the one call the deploy job makes
    plugins/layout.ts   renderer: page chrome, nav
    plugins/excerpt.ts  transform: derives page excerpts
    plugins/sitemap.ts  emitter: sitemap.xml
    plugins/feed.ts     emitter: feed.xml

## Plugins

A plugin is a named object with any of four hooks: `includePage` (filter),
`transform`, `render`, `emit`. `buildSite(config, sources, extraPlugins)`
registers the built-ins first, then yours.

## Tests

    node --test test_sitepress.ts
