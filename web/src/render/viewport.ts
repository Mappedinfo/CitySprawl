export type Viewport = {
  panX: number;
  panY: number;
  scale: number;
};

/**
 * Compute a uniform fit scale and center offsets to map a square world extent
 * into a potentially non-square screen, maintaining 1:1 aspect ratio and
 * centering the content.
 */
function computeFit(extent: number, width: number, height: number): { fitScale: number; offsetX: number; offsetY: number } {
  const fitScale = Math.min(width, height) / extent;
  const offsetX = (width - extent * fitScale) / 2;
  const offsetY = (height - extent * fitScale) / 2;
  return { fitScale, offsetX, offsetY };
}

export function worldToScreen(
  x: number,
  y: number,
  extent: number,
  width: number,
  height: number,
  viewport: Viewport,
): { x: number; y: number } {
  const { fitScale, offsetX, offsetY } = computeFit(extent, width, height);
  // Map world (0..extent, 0..extent) to screen with uniform scale, centered.
  // World Y=0 is bottom, screen Y=0 is top, so flip Y.
  const nx = x * fitScale + offsetX;
  const ny = (extent - y) * fitScale + offsetY;
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
  const { fitScale, offsetX, offsetY } = computeFit(extent, width, height);
  // Reverse the viewport transform
  const nx = (sx - viewport.panX) / viewport.scale;
  const ny = (sy - viewport.panY) / viewport.scale;
  // Reverse the fit transform
  return {
    x: (nx - offsetX) / fitScale,
    y: extent - (ny - offsetY) / fitScale,
  };
}

export function clampScale(scale: number): number {
  return Math.min(8, Math.max(0.3, scale));
}
