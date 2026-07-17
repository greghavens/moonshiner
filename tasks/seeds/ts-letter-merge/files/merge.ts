export type MergeVars = Record<string, string>;

/**
 * Offer letters are authored as templates with {{placeholders}}; HR fills
 * them in per candidate. The same placeholder routinely appears many times
 * in one letter (name in the greeting, the signature block, the equity
 * rider, ...).
 */
export function fillTemplate(template: string, vars: MergeVars): string {
  let out = template;
  for (const [key, value] of Object.entries(vars)) {
    out = out.replace(`{{${key}}}`, value);
  }
  return out;
}

/** Placeholders the template needs that vars does not provide, sorted. */
export function missingPlaceholders(template: string, vars: MergeVars): string[] {
  const missing = new Set<string>();
  for (const match of template.matchAll(/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g)) {
    if (!(match[1] in vars)) {
      missing.add(match[1]);
    }
  }
  return [...missing].sort();
}

/**
 * Before a filled letter can be shared as a sample (recruiting decks,
 * training docs), every occurrence of the candidate's personal details —
 * name, email, comp figures like "$185,000" — must be masked.
 */
export function redact(text: string, terms: string[], mask = '█'): string {
  let out = text;
  for (const term of terms) {
    if (term === '') {
      continue;
    }
    out = out.replace(new RegExp(term, 'g'), mask.repeat(term.length));
  }
  return out;
}

/** True once no listed term survives anywhere in the text. */
export function isFullyRedacted(text: string, terms: string[]): boolean {
  return terms.every((term) => term === '' || !text.includes(term));
}
