/** Escaping helpers shared by the renderer, the markdown converter and the
 * xml emitters. Keep these the single source of truth: hand-rolled escaping
 * in a plugin is how we once shipped a feed that no reader could parse. */

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, '&quot;');
}

export function escapeXml(text: string): string {
  return escapeAttr(text).replace(/'/g, '&apos;');
}
