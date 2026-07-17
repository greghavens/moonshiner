/** Config validation. Strict on purpose: a typo'd key in a site config once
 * shipped a handbook with no nav, so unknown keys are hard errors. */
import type { SiteConfig } from './types.ts';

const DEFAULTS = {
  baseUrl: 'https://example.test',
  prettyUrls: true,
  feedLimit: 10,
};

const KNOWN_KEYS = new Set(['title', 'baseUrl', 'prettyUrls', 'feedLimit']);

function fail(message: string): never {
  throw new Error(`config: ${message}`);
}

export function loadConfig(raw: unknown): SiteConfig {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    fail('expected an object');
  }
  const input = raw as Record<string, unknown>;
  for (const key of Object.keys(input)) {
    if (!KNOWN_KEYS.has(key)) fail(`unknown config key: ${key}`);
  }

  const title = input.title;
  if (typeof title !== 'string' || title.trim() === '') {
    fail('title must be a non-empty string');
  }

  let baseUrl = DEFAULTS.baseUrl;
  if (input.baseUrl !== undefined) {
    if (typeof input.baseUrl !== 'string' || !/^https?:\/\//.test(input.baseUrl)) {
      fail('baseUrl must be an absolute http(s) url');
    }
    baseUrl = input.baseUrl.replace(/\/+$/, '');
  }

  let prettyUrls = DEFAULTS.prettyUrls;
  if (input.prettyUrls !== undefined) {
    if (typeof input.prettyUrls !== 'boolean') fail('prettyUrls must be a boolean');
    prettyUrls = input.prettyUrls;
  }

  let feedLimit = DEFAULTS.feedLimit;
  if (input.feedLimit !== undefined) {
    if (typeof input.feedLimit !== 'number' || !Number.isInteger(input.feedLimit) || input.feedLimit < 1) {
      fail('feedLimit must be a positive integer');
    }
    feedLimit = input.feedLimit;
  }

  return { title: title.trim(), baseUrl, prettyUrls, feedLimit };
}
