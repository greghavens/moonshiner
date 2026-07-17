// Skate-hire counter for the pavilion rink: size bins on the wall, paper
// hire slips behind the till, and the sharpening queue by the back bench.

export interface SizeBin {
  onShelf: number;
  out: number;
}

export interface HireSlip {
  tag: number;
  size: string;
  deposit: number;
  note?: string;
}

const BASE_DEPOSIT = 5;
const KIDS_DEPOSIT = 2;
const SHARPEN_FEE = 4;

export function isKidsSize(size: string): boolean {
  const n = Number.parseFloat(size);
  return Number.isFinite(n) && n <= 3;
}

export class HireCounter {
  private bins = new Map<string, SizeBin>();
  private slips: HireSlip[] = [];
  private sharpening: string[] = [];
  private nextTag = 100;

  constructor(stock: Record<string, number>) {
    const sizes = Object.keys(stock);
    for (const [size, count] of Object.entries(stock)) {
      this.bins.set(size, { onShelf: count, out: 0 });
    }
  }

  sizesOnShelf(): string[] {
    const sizes: string[] = [];
    for (const [size, bin] of this.bins.entries()) {
      if (bin.onShelf > 0) {
        sizes.push(size);
      }
    }
    return sizes.sort();
  }

  hireOut(size: string, note?: string): HireSlip | null {
    if (!this.bins.has(size)) {
      return null;
    }
    const bin = this.bins.get(size);
    if (bin.onShelf < 1) {
      return null;
    }
    bin.onShelf -= 1;
    bin.out += 1;
    const deposit = isKidsSize(size) ? KIDS_DEPOSIT : BASE_DEPOSIT;
    const slip: HireSlip = { tag: this.nextTag, size, deposit };
    this.nextTag += 1;
    if (note !== undefined) {
      slip.note = note;
    }
    this.slips.push(slip);
    return slip;
  }

  handBack(tag: number, needsSharpening: boolean): number | null {
    if (this.slips.every((s) => s.tag !== tag)) {
      return null;
    }
    const slip = this.slips.find((s) => s.tag === tag);
    const bin = this.bins.get(slip.size);
    if (bin === undefined) {
      return null;
    }
    bin.out -= 1;
    if (needsSharpening) {
      this.sharpening.push(slip.size);
    } else {
      bin.onShelf += 1;
    }
    this.slips = this.slips.filter((s) => s.tag !== tag);
    return slip.deposit;
  }

  sharpenDone(size: string, bench: string): boolean {
    const at = this.sharpening.indexOf(size);
    if (at < 0) {
      return false;
    }
    this.sharpening.splice(at, 1);
    const bin = this.bins.get(size);
    if (bin === undefined) {
      return false;
    }
    bin.onShelf += 1;
    return true;
  }

  sharpeningQueue(): string[] {
    return [...this.sharpening];
  }

  notedSlips(): string[] {
    return this.slips
      .filter((s) => s.note !== undefined)
      .map((s) => `#${s.tag}: ${s.note.trim()}`);
  }

  receiptLine(slip: HireSlip, wide: boolean): string {
    return `#${slip.tag} size ${slip.size} — deposit $${slip.deposit}`;
  }

  depositsHeld(): number {
    let held = 0;
    for (const slip of this.slips) {
      held += slip.deposit;
    }
    return held;
  }
}
