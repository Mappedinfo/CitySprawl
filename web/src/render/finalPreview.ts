import type { BuildingFootprint, CityArtifact } from '../types/city';
import type { Viewport } from './viewport';
import { worldToScreen } from './viewport';

function drawGridOverlay(
  ctx: CanvasRenderingContext2D,
  grid: number[][],
  extent: number,
  viewport: Viewport,
  colorize: (v: number) => [number, number, number, number],
): void {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  if (!rows || !cols) return;
  const { width, height } = ctx.canvas;
  const cellWWorld = extent / Math.max(cols - 1, 1);
  const cellHWorld = extent / Math.max(rows - 1, 1);

  ctx.save();
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < cols; x += 1) {
      const v = grid[y][x];
      if (v <= 0.05) continue;
      const [r, g, b, a] = colorize(v);
      const p = worldToScreen((x / Math.max(cols - 1, 1)) * extent, (y / Math.max(rows - 1, 1)) * extent, extent, width, height, viewport);
      const p2 = worldToScreen(
        ((x + 1) / Math.max(cols - 1, 1)) * extent,
        ((y + 1) / Math.max(rows - 1, 1)) * extent,
        extent,
        width,
        height,
        viewport,
      );
      const left = Math.min(p.x, p2.x);
      const top = Math.min(p.y, p2.y);
      const w = Math.max(1, Math.abs(p2.x - p.x));
      const h = Math.max(1, Math.abs(p2.y - p.y));
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
      ctx.fillRect(left, top, w, h);
    }
  }
  ctx.restore();
}

export function drawGreenZones(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  greenZones: number[][],
  viewport: Viewport,
): void {
  drawGridOverlay(ctx, greenZones, artifact.terrain.extent_m, viewport, (v) => {
    const alpha = Math.min(0.34, 0.08 + v * 0.24);
    return [48, 210, 134, alpha];
  });
}

export function drawBuildingFootprints(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  footprints: BuildingFootprint[],
  viewport: Viewport,
): void {
  const { width, height } = ctx.canvas;
  const extent = artifact.terrain.extent_m;
  ctx.save();
  for (const bldg of footprints) {
    if (bldg.points.length < 3) continue;
    const h = Math.max(0.2, Math.min(1.5, bldg.height_hint));
    const fillA = Math.min(0.52, 0.18 + h * 0.28);
    const strokeA = Math.min(0.95, 0.55 + h * 0.25);
    ctx.beginPath();
    bldg.points.forEach((pt, i) => {
      const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (i === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.closePath();
    ctx.fillStyle = `rgba(190, 250, 255, ${fillA})`;
    ctx.fill();
    ctx.strokeStyle = `rgba(100, 238, 255, ${strokeA})`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.restore();
}
