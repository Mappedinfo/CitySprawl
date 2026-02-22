export type Viewport = {
  panX: number;
  panY: number;
  scale: number;
};

export function worldToScreen(
  x: number,
  y: number,
  extent: number,
  width: number,
  height: number,
  viewport: Viewport,
): { x: number; y: number } {
  const nx = (x / extent) * width;
  const ny = height - (y / extent) * height;
  return {
    x: nx * viewport.scale + viewport.panX,
    y: ny * viewport.scale + viewport.panY,
  };
}

export function screenToWorld(
  sx: number,
  sy: number,
  extent: number,
  width: number,
  height: number,
  viewport: Viewport,
): { x: number; y: number } {
  const nx = (sx - viewport.panX) / viewport.scale;
  const ny = (sy - viewport.panY) / viewport.scale;
  return {
    x: (nx / width) * extent,
    y: ((height - ny) / height) * extent,
  };
}

export function clampScale(scale: number): number {
  return Math.min(8, Math.max(0.3, scale));
}
