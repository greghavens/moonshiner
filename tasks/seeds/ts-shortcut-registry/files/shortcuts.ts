// Keyboard shortcut registry for the app shell. Combos are written
// "ctrl+shift+p": zero or more modifiers plus one key, case-insensitive.
// Combos are normalized (canonical modifier order, lowercased key) so that
// "shift+ctrl+P" and "ctrl+shift+p" are the same binding.

export interface KeyEvent {
  key: string;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  metaKey?: boolean;
}

export type Handler = () => void;

const MODIFIER_ORDER = ['ctrl', 'alt', 'shift', 'meta'];

export function normalizeCombo(combo: string): string {
  const parts = combo.trim().toLowerCase().split('+');
  const key = parts[parts.length - 1];
  if (!key) throw new Error(`combo has no key: "${combo}"`);
  const mods = parts.slice(0, -1);
  for (const mod of mods) {
    if (!MODIFIER_ORDER.includes(mod)) {
      throw new Error(`unknown modifier "${mod}" in "${combo}"`);
    }
  }
  const ordered = MODIFIER_ORDER.filter((m) => mods.includes(m));
  return [...ordered, key].join('+');
}

function comboFromEvent(event: KeyEvent): string {
  const mods: string[] = [];
  if (event.ctrlKey) mods.push('ctrl');
  if (event.altKey) mods.push('alt');
  if (event.shiftKey) mods.push('shift');
  if (event.metaKey) mods.push('meta');
  return [...mods, event.key.toLowerCase()].join('+');
}

export class ShortcutRegistry {
  private bindings = new Map<string, Handler>();

  register(combo: string, handler: Handler): void {
    const normalized = normalizeCombo(combo);
    if (this.bindings.has(normalized)) {
      throw new Error(`"${normalized}" is already bound`);
    }
    this.bindings.set(normalized, handler);
  }

  unregister(combo: string): boolean {
    return this.bindings.delete(normalizeCombo(combo));
  }

  /** Dispatch a key event; returns true when a handler fired. */
  handle(event: KeyEvent): boolean {
    const handler = this.bindings.get(comboFromEvent(event));
    if (!handler) return false;
    handler();
    return true;
  }

  list(): string[] {
    return [...this.bindings.keys()].sort();
  }
}
