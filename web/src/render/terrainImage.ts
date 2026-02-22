export function heightGridToImageData(heights: number[][]): ImageData | null {
  const h = heights.length;
  const w = heights[0]?.length ?? 0;
  if (!h || !w) return null;

  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const row of heights) {
    for (const value of row) {
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  const span = Math.max(1e-6, max - min);
  const data = new Uint8ClampedArray(w * h * 4);

  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const idx = (y * w + x) * 4;
      const t = (heights[y][x] - min) / span;
      // earthy palette
      const r = Math.round(20 + t * 120 + Math.max(0, (t - 0.7) * 80));
      const g = Math.round(35 + t * 110);
      const b = Math.round(30 + (1 - t) * 50);
      data[idx] = r;
      data[idx + 1] = g;
      data[idx + 2] = b;
      data[idx + 3] = 255;
    }
  }

  return new ImageData(data, w, h);
}
