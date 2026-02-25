import type { CityArtifact, RiverLine, RoadEdgeRecord } from '../types/city';
import { worldToScreen, type Viewport } from './viewport';

export type LayerFlags = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  debugCandidates: boolean;
  showMajorRoads?: boolean;
  showLocalRoads?: boolean;
  showPedestrianRoads?: boolean;
  transparentBackground?: boolean;
  cssWidth?: number;
  cssHeight?: number;
  preserveCanvas?: boolean;
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function worldMetersToPixels(meters: number, extent: number, cssWidth: number, scale: number): number {
  if (!Number.isFinite(meters) || meters <= 0 || extent <= 0 || cssWidth <= 0) return 0;
  return (meters / extent) * cssWidth * scale;
}

export function roadStrokeWidthPx(edge: Pick<RoadEdgeRecord, 'road_class' | 'width_m'>, extent: number, cssWidth: number, viewportScale: number): number {
  const baseMeters =
    edge.width_m ??
    (edge.road_class === 'arterial'
      ? 18
      : edge.road_class === 'collector'
        ? 11
        : edge.road_class === 'pedestrian'
          ? 3
          : edge.road_class === 'service'
            ? 4
            : 8);
  const worldPx = worldMetersToPixels(baseMeters, extent, cssWidth, viewportScale);
  if (edge.road_class === 'arterial') return clamp(worldPx * 0.6, 1.2, 10);
  if (edge.road_class === 'collector') return clamp(worldPx * 0.62, 1.0, 8);
  if (edge.road_class === 'pedestrian') return clamp(worldPx * 0.7, 0.5, 3);
  if (edge.road_class === 'service') return clamp(worldPx * 0.6, 0.55, 3.4);
  return clamp(worldPx * 0.62, 0.8, 6);
}

export function riverCenterlineWidthPx(river: Pick<RiverLine, 'flow'>, hasRiverAreas: boolean): number {
  const base = 0.75 + Math.log10(1 + Math.max(0, river.flow)) * 0.9;
  const adjusted = hasRiverAreas ? base * 0.7 : base;
  return clamp(adjusted, 0.8, 4);
}

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
  cssWidth: number,
  cssHeight: number,
): void {
  const classes = artifact.terrain.terrain_class_preview;
  if (!classes?.length || !classes[0]?.length) return;
  const hillshade = artifact.terrain.hillshade_preview;
  const rows = classes.length;
  const cols = classes[0].length;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;

  ctx.save();
  for (let y = 0; y < rows - 1; y += 1) {
    for (let x = 0; x < cols - 1; x += 1) {
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

function drawRiverAreas(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  const riverAreas = artifact.river_areas ?? [];
  if (!riverAreas.length) return;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;
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
    ctx.fillStyle = area.is_main_stem ? 'rgba(20, 126, 186, 0.66)' : 'rgba(18, 112, 168, 0.44)';
    ctx.strokeStyle = area.is_main_stem ? 'rgba(108, 232, 255, 0.52)' : 'rgba(84, 206, 255, 0.38)';
    ctx.lineWidth = area.is_main_stem ? 1.1 : 0.8;
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
  const width = layers.cssWidth ?? ctx.canvas.clientWidth ?? ctx.canvas.width;
  const height = layers.cssHeight ?? ctx.canvas.clientHeight ?? ctx.canvas.height;
  if (!layers.preserveCanvas) {
    ctx.clearRect(0, 0, width, height);
    if (!layers.transparentBackground) {
      // Solid fallback background (UI shell avoids gradients by design).
      ctx.fillStyle = 'rgba(6, 14, 24, 1)';
      ctx.fillRect(0, 0, width, height);
    }
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
      drawTerrainClassified(ctx, artifact, viewport, width, height);
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
    drawTerrainClassified(ctx, artifact, viewport, width, height);
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
    drawRiverAreas(ctx, artifact, viewport, width, height);
    const hasRiverAreas = (artifact.river_areas?.length ?? 0) > 0;
    ctx.save();
    ctx.strokeStyle = 'rgba(96, 224, 255, 0.72)';
    ctx.lineCap = 'round';
    for (const river of artifact.rivers) {
      if (river.points.length < 2) continue;
      ctx.lineWidth = riverCenterlineWidthPx(river, hasRiverAreas);
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
    const showMajorRoads = layers.showMajorRoads ?? true;
    const showLocalRoads = layers.showLocalRoads ?? true;
    const showPedRoads = layers.showPedestrianRoads ?? true;
    const lodHideLocal = viewport.scale < 1.35;
    const edges = [...artifact.roads.edges].sort((a, b) => {
      if ((a.render_order ?? 1) !== (b.render_order ?? 1)) return (a.render_order ?? 1) - (b.render_order ?? 1);
      return (b.width_m ?? 0) - (a.width_m ?? 0);
    });
    ctx.save();
    for (const edge of edges) {
      const isMajor = edge.road_class === 'arterial' || edge.road_class === 'collector';
      const isPed = edge.road_class === 'pedestrian';
      const isLocal = !isMajor && !isPed;
      if (isMajor && !showMajorRoads) continue;
      if (isPed && !showPedRoads) continue;
      if (isLocal && (!showLocalRoads || lodHideLocal)) continue;
      const u = nodeMap.get(edge.u);
      const v = nodeMap.get(edge.v);
      if (!u || !v) continue;
      const path = edge.path_points && edge.path_points.length >= 2 ? edge.path_points : null;
      if (edge.road_class === 'arterial') {
        ctx.strokeStyle = 'rgba(236, 246, 255, 0.98)';
        ctx.setLineDash([]);
      } else if (edge.road_class === 'collector') {
        ctx.strokeStyle = 'rgba(194, 238, 255, 0.88)';
        ctx.setLineDash([]);
      } else if (edge.road_class === 'pedestrian') {
        ctx.strokeStyle = 'rgba(122, 220, 255, 0.68)';
        ctx.setLineDash([3, 4]);
      } else if (edge.road_class === 'service') {
        ctx.strokeStyle = 'rgba(126, 198, 228, 0.58)';
        ctx.setLineDash([]);
      } else {
        // Local roads (motorized)
        ctx.strokeStyle = 'rgba(136, 214, 245, 0.62)';
        ctx.setLineDash([]);
      }
      ctx.lineWidth = roadStrokeWidthPx(edge, extent, width, viewport.scale);
      ctx.beginPath();
      if (path) {
        path.forEach((pt, idx) => {
          const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
          if (idx === 0) ctx.moveTo(s.x, s.y);
          else ctx.lineTo(s.x, s.y);
        });
      } else {
        const a = worldToScreen(u.x, u.y, extent, width, height, viewport);
        const b = worldToScreen(v.x, v.y, extent, width, height, viewport);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
      }
      ctx.stroke();
      if (edge.river_crossings > 0) {
        let mx: number;
        let my: number;
        if (path && path.length >= 2) {
          const mid = path[Math.floor(path.length / 2)];
          const s = worldToScreen(mid.x, mid.y, extent, width, height, viewport);
          mx = s.x;
          my = s.y;
        } else {
          const a = worldToScreen(u.x, u.y, extent, width, height, viewport);
          const b = worldToScreen(v.x, v.y, extent, width, height, viewport);
          mx = (a.x + b.x) / 2;
          my = (a.y + b.y) / 2;
        }
        ctx.fillStyle = 'rgba(255, 196, 78, 0.95)';
        ctx.beginPath();
        ctx.arc(mx, my, 2.1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();

    // Independent pedestrian paths (from artifact.pedestrian_paths)
    const pedPaths = artifact.pedestrian_paths;
    if ((layers.showPedestrianRoads ?? true) && pedPaths?.length) {
      ctx.save();
      ctx.strokeStyle = 'rgba(102, 230, 180, 0.65)';
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.setLineDash([4 * viewport.scale, 5 * viewport.scale]);
      for (const path of pedPaths) {
        if (!path.points || path.points.length < 2) continue;
        ctx.lineWidth = clamp(worldMetersToPixels(path.width_m || 3.0, extent, width, viewport.scale), 0.5, 3.5);
        ctx.beginPath();
        path.points.forEach((pt, idx) => {
          const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
          if (idx === 0) ctx.moveTo(s.x, s.y);
          else ctx.lineTo(s.x, s.y);
        });
        ctx.stroke();
      }
      ctx.restore();
    }
  }
}
