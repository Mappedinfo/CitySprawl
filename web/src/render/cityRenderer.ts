import type { CityArtifact } from '../types/city';
import { worldToScreen, type Viewport } from './viewport';

export type LayerFlags = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  debugCandidates: boolean;
  transparentBackground?: boolean;
};

function terrainClassColor(cls: number): [number, number, number, number] {
  switch (cls) {
    case 0:
      return [28, 63, 78, 0.95]; // floodplain
    case 1:
      return [21, 33, 52, 0.96]; // plain
    case 2:
      return [34, 51, 77, 0.96]; // rolling hill
    case 3:
      return [42, 62, 92, 0.96]; // high hill
    case 4:
      return [30, 36, 48, 0.98]; // mountain
    case 5:
      return [56, 70, 92, 0.98]; // ridge
    default:
      return [24, 38, 58, 0.95];
  }
}

function drawTerrainClassified(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  viewport: Viewport,
): void {
  const classes = artifact.terrain.terrain_class_preview;
  if (!classes?.length || !classes[0]?.length) return;
  const hillshade = artifact.terrain.hillshade_preview;
  const rows = classes.length;
  const cols = classes[0].length;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;

  ctx.save();
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < cols; x += 1) {
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
      const [r, g, b, a] = terrainClassColor(classes[y][x] ?? 1);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
      ctx.fillRect(left, top, w, h);
      const shade = hillshade?.[y]?.[x];
      if (typeof shade === 'number') {
        const alpha = 0.06 + (1 - Math.max(0, Math.min(1, shade))) * 0.24;
        ctx.fillStyle = `rgba(220, 235, 255, ${alpha})`;
        ctx.fillRect(left, top, w, h);
      }
    }
  }
  ctx.restore();
}

function drawRiverAreas(ctx: CanvasRenderingContext2D, artifact: CityArtifact, viewport: Viewport): void {
  const riverAreas = artifact.river_areas ?? [];
  if (!riverAreas.length) return;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;
  ctx.save();
  for (const area of riverAreas) {
    if (!area.points?.length) continue;
    ctx.beginPath();
    area.points.forEach((pt, i) => {
      const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (i === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.closePath();
    ctx.fillStyle = area.is_main_stem ? 'rgba(16, 112, 162, 0.78)' : 'rgba(16, 112, 162, 0.55)';
    ctx.strokeStyle = 'rgba(80, 214, 255, 0.6)';
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

export function drawCity(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact | null,
  viewport: Viewport,
  terrainBitmap: ImageBitmap | null,
  layers: LayerFlags,
): void {
  const { width, height } = ctx.canvas;
  ctx.clearRect(0, 0, width, height);
  if (!layers.transparentBackground) {
    // Solid fallback background (UI shell avoids gradients by design).
    ctx.fillStyle = 'rgba(6, 14, 24, 1)';
    ctx.fillRect(0, 0, width, height);
  }

  if (!artifact) {
    ctx.fillStyle = 'rgba(188, 242, 255, 0.85)';
    ctx.font = '14px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.fillText('No artifact loaded.', 16, 24);
    return;
  }

  const extent = artifact.terrain.extent_m;
  if (layers.terrain && terrainBitmap) {
    if (artifact.terrain.terrain_class_preview?.length) {
      drawTerrainClassified(ctx, artifact, viewport);
    } else {
      ctx.save();
      const sx = viewport.panX;
      const sy = viewport.panY;
      const sw = width * viewport.scale;
      const sh = height * viewport.scale;
      ctx.globalAlpha = 0.9;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(terrainBitmap, sx, sy, sw, sh);
      ctx.restore();
    }
  } else if (layers.terrain && artifact.terrain.terrain_class_preview?.length) {
    drawTerrainClassified(ctx, artifact, viewport);
  }

  if (layers.debugCandidates) {
    ctx.save();
    ctx.strokeStyle = 'rgba(110, 200, 255, 0.14)';
    ctx.lineWidth = Math.max(0.6, 0.8 * viewport.scale);
    for (const seg of artifact.debug_layers.candidate_edges) {
      const a = worldToScreen(seg.a.x, seg.a.y, extent, width, height, viewport);
      const b = worldToScreen(seg.b.x, seg.b.y, extent, width, height, viewport);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.restore();
  }

  if (layers.rivers) {
    drawRiverAreas(ctx, artifact, viewport);
    ctx.save();
    ctx.strokeStyle = 'rgba(72, 190, 255, 0.92)';
    ctx.lineCap = 'round';
    for (const river of artifact.rivers) {
      if (river.points.length < 2) continue;
      const widthPx = Math.max(1, Math.min(5, 0.8 + Math.log10(1 + river.flow)));
      ctx.lineWidth = widthPx;
      ctx.beginPath();
      river.points.forEach((pt, i) => {
        const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.stroke();
    }
    ctx.restore();
  }

  if (layers.roads) {
    const nodeMap = new Map(artifact.roads.nodes.map((n) => [n.id, n]));
    const edges = [...artifact.roads.edges].sort((a, b) => {
      if ((a.render_order ?? 1) !== (b.render_order ?? 1)) return (a.render_order ?? 1) - (b.render_order ?? 1);
      return (b.width_m ?? 0) - (a.width_m ?? 0);
    });
    ctx.save();
    for (const edge of edges) {
      const u = nodeMap.get(edge.u);
      const v = nodeMap.get(edge.v);
      if (!u || !v) continue;
      const a = worldToScreen(u.x, u.y, extent, width, height, viewport);
      const b = worldToScreen(v.x, v.y, extent, width, height, viewport);
      if (edge.road_class === 'arterial') {
        ctx.strokeStyle = 'rgba(236, 246, 255, 0.98)';
      } else if (edge.road_class === 'pedestrian') {
        ctx.strokeStyle = 'rgba(122, 220, 255, 0.68)';
      } else {
        ctx.strokeStyle = 'rgba(143, 226, 255, 0.72)';
      }
      const worldScale = (width / Math.max(extent, 1)) * viewport.scale;
      const widthPx = Math.max(
        edge.road_class === 'arterial' ? 1.6 : edge.road_class === 'pedestrian' ? 0.7 : 0.95,
        ((edge.width_m ?? (edge.road_class === 'arterial' ? 18 : edge.road_class === 'pedestrian' ? 3 : 8)) * worldScale) / 7.5,
      );
      ctx.lineWidth = widthPx;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      if (edge.river_crossings > 0) {
        ctx.fillStyle = 'rgba(255, 196, 78, 0.95)';
        ctx.beginPath();
        ctx.arc((a.x + b.x) / 2, (a.y + b.y) / 2, 2.1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }
}
