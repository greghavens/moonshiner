// Message catalog for UI strings. Locales are BCP-47-ish tags ("de-AT");
// lookup falls back from the full tag to its language part to the default
// locale. Values support {name} interpolation from a params object.

export interface CatalogOptions {
  defaultLocale?: string;
}

export class MessageCatalog {
  private messages = new Map<string, Map<string, string>>();
  private defaultLocale: string;

  constructor(options: CatalogOptions = {}) {
    this.defaultLocale = options.defaultLocale ?? 'en';
  }

  add(locale: string, key: string, message: string): void {
    if (!locale || !key) throw new Error('locale and key are required');
    let table = this.messages.get(locale);
    if (!table) {
      table = new Map();
      this.messages.set(locale, table);
    }
    table.set(key, message);
  }

  addAll(locale: string, entries: Record<string, string>): void {
    for (const [key, message] of Object.entries(entries)) {
      this.add(locale, key, message);
    }
  }

  has(locale: string, key: string): boolean {
    return this.lookup(locale, key) !== undefined;
  }

  // Full tag, then language part, then default locale, without repeats.
  private localeChain(locale: string): string[] {
    const chain = [locale];
    const language = locale.split('-')[0];
    if (language !== locale) chain.push(language);
    if (!chain.includes(this.defaultLocale)) chain.push(this.defaultLocale);
    return chain;
  }

  private lookup(locale: string, key: string): string | undefined {
    for (const tag of this.localeChain(locale)) {
      const message = this.messages.get(tag)?.get(key);
      if (message !== undefined) return message;
    }
    return undefined;
  }

  private interpolate(template: string, params: Record<string, unknown>): string {
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in params ? String(params[name]) : match,
    );
  }

  t(locale: string, key: string, params: Record<string, unknown> = {}): string {
    const message = this.lookup(locale, key);
    if (message === undefined) return key;
    return this.interpolate(message, params);
  }
}
