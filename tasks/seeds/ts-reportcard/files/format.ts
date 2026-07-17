// Shared formatting helpers for the finance report views.

/** Whole-dollar display with thousands separators, e.g. 12500 -> "$12,500". */
export function money(amount: number): string {
  const digits = Math.trunc(Math.abs(amount)).toString();
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${amount < 0 ? "-" : ""}$${grouped}`;
}

/** Minimal HTML escaping for strings we splice into markup ourselves. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Turn *emphasis* spans into <strong> tags; footnotes use this. */
export function emphasize(text: string): string {
  return text.replace(/\*([^*]+)\*/g, "<strong>$1</strong>");
}

/** Display form of a vendor name as it came from the import feed. */
export function vendorLabel(name: string): string {
  return escapeHtml(name.trim());
}
