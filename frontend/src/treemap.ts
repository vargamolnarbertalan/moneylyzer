// Squarified treemap (Bruls, Huizing, van Wijk), ported from Direction B of the design mock.
export interface TreemapInput {
  id: string;
  weight: number;
}

export interface Tile {
  id: string;
  weight: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Item {
  id: string;
  weight: number;
  a: number; // area in px^2
}

/** Lay out items (largest first) in a W x H rectangle; each tile's area is proportional to its weight. */
export function squarify(input: readonly TreemapInput[], W: number, H: number): Tile[] {
  const valid = input.filter((i) => Number.isFinite(i.weight) && i.weight > 0).sort((a, b) => b.weight - a.weight);
  const sum = valid.reduce((s, i) => s + i.weight, 0);
  if (!valid.length || sum <= 0 || W <= 0 || H <= 0) return [];

  const items: Item[] = valid.map((i) => ({ id: i.id, weight: i.weight, a: (i.weight / sum) * W * H }));
  const out: Tile[] = [];
  let rect = { x: 0, y: 0, w: W, h: H };
  let row: Item[] = [];

  const worst = (r: Item[], side: number): number => {
    const s = r.reduce((a, b) => a + b.a, 0);
    const mx = Math.max(...r.map((i) => i.a));
    const mn = Math.min(...r.map((i) => i.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };

  const layout = (r: Item[]): void => {
    const s = r.reduce((a, b) => a + b.a, 0);
    if (rect.w >= rect.h) {
      // lay the row out as a column on the left
      const w = s / rect.h;
      let y = rect.y;
      for (const i of r) {
        const h = i.a / w;
        out.push({ id: i.id, weight: i.weight, x: rect.x, y, w, h });
        y += h;
      }
      rect = { x: rect.x + w, y: rect.y, w: rect.w - w, h: rect.h };
    } else {
      // lay the row out along the top
      const h = s / rect.w;
      let x = rect.x;
      for (const i of r) {
        const w = i.a / h;
        out.push({ id: i.id, weight: i.weight, x, y: rect.y, w, h });
        x += w;
      }
      rect = { x: rect.x, y: rect.y + h, w: rect.w, h: rect.h - h };
    }
  };

  for (const it of items) {
    const side = Math.min(rect.w, rect.h);
    if (!row.length || worst([...row, it], side) <= worst(row, side)) row.push(it);
    else {
      layout(row);
      row = [it];
    }
  }
  if (row.length) layout(row);
  return out;
}
