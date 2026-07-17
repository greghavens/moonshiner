// Signage fleet: the panel classes behind the lobby and platform displays,
// plus the per-panel display config the scheduler patches at runtime.

export interface DisplayConfig {
  brightness?: number;
  refreshSecs?: number;
  caption?: string;
}

export function buildConfig(brightness?: number, caption?: string): DisplayConfig {
  return { brightness: brightness, caption: caption };
}

export class Panel {
  readonly id: string;
  protected cfg: DisplayConfig = {};

  constructor(id: string) {
    this.id = id;
  }

  configKeys(): string[] {
    return Object.keys(this.cfg).sort();
  }

  setting(key: keyof DisplayConfig): number | string | undefined {
    return this.cfg[key];
  }

  applyPatch(patch: DisplayConfig): void {
    this.cfg.brightness = patch.brightness;
    if (patch.refreshSecs !== undefined) {
      this.cfg.refreshSecs = patch.refreshSecs;
    }
    if (patch.caption !== undefined) {
      this.cfg.caption = patch.caption;
    }
  }

  clearCaption(): void {
    this.cfg.caption = undefined;
  }

  describe(): string {
    return `${this.id}: bare panel`;
  }

  powerDraw(): number {
    return 20;
  }

  wakeMessage(): string {
    return `${this.id} online`;
  }
}

export class LedBoard extends Panel {
  describe(): string {
    return `${this.id}: LED board`;
  }

  powerDraw(): number {
    const brightness = this.setting("brightness");
    const level = typeof brightness === "number" ? brightness : 50;
    return 40 + Math.round(level / 10);
  }
}

export class TickerBoard extends LedBoard {
  describe(): string {
    return `${this.id}: LED ticker`;
  }

  wakeMessage(): string {
    return `${this.id} online (scroll test ok)`;
  }
}

export class EInkPanel extends Panel {
  describe(): string {
    return `${this.id}: e-ink panel`;
  }

  powerDraw(): number {
    return 3;
  }
}

export function fleetDraw(panels: Panel[]): number {
  let total = 0;
  for (const panel of panels) {
    total += panel.powerDraw();
  }
  return total;
}
