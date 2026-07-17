// shapes.ts — the scene model for the whiteboard canvas.
//
// A scene is an array of shapes. The renderer asks for bounds, the
// selection tool for hit areas, the sidebar for descriptions, and scenes
// are persisted as JSON (see serializeScene/parseScene — the saved-file
// format is stable and there are years of boards on disk in it).

export type Box = { x: number; y: number; width: number; height: number };

const LABEL_CHAR_W = 8;
const LABEL_LINE_H = 16;

export abstract class Shape {
  abstract area(): number;
  abstract bounds(): Box;
  abstract moved(dx: number, dy: number): Shape;
}

export class Rect extends Shape {
  x: number;
  y: number;
  width: number;
  height: number;
  constructor(x: number, y: number, width: number, height: number) {
    super();
    this.x = x;
    this.y = y;
    this.width = width;
    this.height = height;
  }
  area(): number {
    return this.width * this.height;
  }
  bounds(): Box {
    return { x: this.x, y: this.y, width: this.width, height: this.height };
  }
  moved(dx: number, dy: number): Shape {
    return new Rect(this.x + dx, this.y + dy, this.width, this.height);
  }
}

export class Circle extends Shape {
  cx: number;
  cy: number;
  r: number;
  constructor(cx: number, cy: number, r: number) {
    super();
    this.cx = cx;
    this.cy = cy;
    this.r = r;
  }
  area(): number {
    return Math.PI * this.r * this.r;
  }
  bounds(): Box {
    return { x: this.cx - this.r, y: this.cy - this.r, width: 2 * this.r, height: 2 * this.r };
  }
  moved(dx: number, dy: number): Shape {
    return new Circle(this.cx + dx, this.cy + dy, this.r);
  }
}

export class Segment extends Shape {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  constructor(x1: number, y1: number, x2: number, y2: number) {
    super();
    this.x1 = x1;
    this.y1 = y1;
    this.x2 = x2;
    this.y2 = y2;
  }
  area(): number {
    return 0;
  }
  bounds(): Box {
    const x = Math.min(this.x1, this.x2);
    const y = Math.min(this.y1, this.y2);
    return { x, y, width: Math.abs(this.x2 - this.x1), height: Math.abs(this.y2 - this.y1) };
  }
  moved(dx: number, dy: number): Shape {
    return new Segment(this.x1 + dx, this.y1 + dy, this.x2 + dx, this.y2 + dy);
  }
}

export class Label extends Shape {
  x: number;
  y: number;
  text: string;
  constructor(x: number, y: number, text: string) {
    super();
    this.x = x;
    this.y = y;
    this.text = text;
  }
  area(): number {
    return 0;
  }
  bounds(): Box {
    return { x: this.x, y: this.y, width: this.text.length * LABEL_CHAR_W, height: LABEL_LINE_H };
  }
  moved(dx: number, dy: number): Shape {
    return new Label(this.x + dx, this.y + dy, this.text);
  }
}

export function describe(s: Shape): string {
  if (s instanceof Rect) {
    return `rect ${s.width}x${s.height} at (${s.x}, ${s.y})`;
  }
  if (s instanceof Circle) {
    return `circle r=${s.r} at (${s.cx}, ${s.cy})`;
  }
  if (s instanceof Segment) {
    const len = Math.sqrt((s.x2 - s.x1) ** 2 + (s.y2 - s.y1) ** 2);
    return `segment (${s.x1}, ${s.y1}) -> (${s.x2}, ${s.y2}) length ${len}`;
  }
  if (s instanceof Label) {
    return `label "${s.text}" at (${s.x}, ${s.y})`;
  }
  throw new Error('unknown shape');
}

export function totalInkArea(shapes: Shape[]): number {
  let total = 0;
  for (const s of shapes) {
    total += s.area();
  }
  return total;
}

export function serializeScene(shapes: Shape[]): string {
  const plain = shapes.map((s) => {
    if (s instanceof Rect) {
      return { kind: 'rect', x: s.x, y: s.y, width: s.width, height: s.height };
    }
    if (s instanceof Circle) {
      return { kind: 'circle', cx: s.cx, cy: s.cy, r: s.r };
    }
    if (s instanceof Segment) {
      return { kind: 'segment', x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2 };
    }
    if (s instanceof Label) {
      return { kind: 'label', x: s.x, y: s.y, text: s.text };
    }
    throw new Error('unknown shape');
  });
  return JSON.stringify(plain);
}

export function parseScene(json: string): Shape[] {
  const raw = JSON.parse(json);
  if (!Array.isArray(raw)) {
    throw new Error('scene must be an array');
  }
  return raw.map((o: any) => {
    switch (o.kind) {
      case 'rect':
        return new Rect(o.x, o.y, o.width, o.height);
      case 'circle':
        return new Circle(o.cx, o.cy, o.r);
      case 'segment':
        return new Segment(o.x1, o.y1, o.x2, o.y2);
      case 'label':
        return new Label(o.x, o.y, o.text);
      default:
        throw new Error(`unknown shape kind: ${o.kind}`);
    }
  });
}
