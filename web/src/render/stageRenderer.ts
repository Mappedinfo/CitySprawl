import type { CityArtifact, LandBlock, ParcelLot, PedestrianPath, ResourceSite, StageArtifact } from '../types/city';
import { drawCity, type LayerFlags } from './cityRenderer';
import { drawBuildingFootprints, drawGreenZones } from './finalPreview';
import { drawTrafficFlows } from './trafficAnimation';
import { worldToScreen, type Viewport } from './viewport';

export type LayerToggles = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  contours: boolean;
  blocks: boolean;
  parcels: boolean;
  pedestrianPaths: boolean;
  debugCandidates: boolean;
  labels: boolean;
  analysis: boolean;
  resources: boolean;
  traffic: boolean;
  buildings: boolean;
  greenZones: boolean;
};

type Params = {
  ctx: CanvasRenderingContext2D;
  artifact: CityArtifact | null;
  stage: StageArtifact | null;
  viewport: Viewport;
  terrainBitmap: ImageBitmap | null;
  layers: LayerToggles;
  nowMs: number;
  reducedMotion: boolean;
  transparentBackground?: boolean;
};

function hasStageLayer(stage: StageArtifact | null, key: string): boolean {
  if (!stage) return false;
  return stage.visible_layers.includes(key);
}

function heatColor(kind: 'suitability' | 'flood' | 'population', value: number): [number, number, number, number] {
  const v = Math.max(0, Math.min(1, value));
  if (kind === 'suitability') return [26, 244, 180, 0.1 + v * 0.35];
  if (kind === 'flood') return [255, 86, 90, 0.08 + v * 0.28];
  return [255, 216, 84, 0.08 + v * 0.3];
}

function drawHeatGrid(
  ctx: CanvasRenderingContext2D,
  grid: number[][],
  artifact: CityArtifact,
  viewport: Viewport,
  kind: 'suitability' | 'flood' | 'population',
): void {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  if (!rows || !cols) return;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;

  ctx.save();
  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < cols; x += 1) {
      const v = grid[y][x];
      if (v <= 0.04) continue;
      const [r, g, b, a] = heatColor(kind, v);
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
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
      ctx.fillRect(left, top, Math.max(1, Math.abs(p2.x - p.x)), Math.max(1, Math.abs(p2.y - p.y)));
    }
  }
  ctx.restore();
}

function resourceColor(kind: string): string {
  if (kind === 'water') return 'rgba(80, 180, 255, 0.95)';
  if (kind === 'agri') return 'rgba(122, 247, 102, 0.95)';
  if (kind === 'ore') return 'rgba(255, 170, 66, 0.95)';
  return 'rgba(144, 255, 210, 0.95)';
}

function drawResources(ctx: CanvasRenderingContext2D, artifact: CityArtifact, sites: ResourceSite[], viewport: Viewport): void {
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;
  ctx.save();
  for (const site of sites) {
    const s = worldToScreen(site.x, site.y, extent, width, height, viewport);
    const r = Math.max(2, Math.min(6, 2 + site.quality * 4));
    ctx.strokeStyle = resourceColor(site.kind);
    ctx.fillStyle = 'rgba(8, 14, 22, 0.65)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(s.x, s.y, r + 2, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(s.x, s.y, r * 0.65, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function drawContours(ctx: CanvasRenderingContext2D, artifact: CityArtifact, stage: StageArtifact, viewport: Viewport): void {
  const contours = stage.layers.contour_lines ?? artifact.terrain.contours ?? [];
  if (!contours.length) return;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;
  ctx.save();
  for (const contour of contours) {
    if (!contour.points?.length || contour.points.length < 2) continue;
    const e = Math.max(0, Math.min(1, contour.elevation_norm ?? 0.5));
    ctx.strokeStyle = `rgba(126, 220, 255, ${0.08 + e * 0.16})`;
    ctx.lineWidth = 0.5 + e * 0.4;
    ctx.beginPath();
    contour.points.forEach((pt, i) => {
      const p = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  }
  ctx.restore();
}

function parcelColor(kind: string): string {
  switch (kind) {
    case 'commercial_candidate':
      return 'rgba(255, 196, 89, 0.16)';
    case 'industrial_candidate':
      return 'rgba(255, 126, 94, 0.18)';
    case 'green_candidate':
      return 'rgba(92, 240, 160, 0.14)';
    case 'public_facility_candidate':
      return 'rgba(170, 184, 255, 0.16)';
    default:
      return 'rgba(118, 220, 255, 0.12)';
  }
}

function drawPolygonList(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  polygons: Array<{ points: { x: number; y: number }[] }>,
  viewport: Viewport,
  fillStyle: ((index: number) => string) | string,
  strokeStyle: string,
  lineWidth = 0.8,
): void {
  if (!polygons.length) return;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;
  ctx.save();
  ctx.lineWidth = lineWidth;
  ctx.strokeStyle = strokeStyle;
  polygons.forEach((poly, idx) => {
    if (!poly.points?.length || poly.points.length < 3) return;
    ctx.beginPath();
    poly.points.forEach((pt, i) => {
      const p = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.fillStyle = typeof fillStyle === 'string' ? fillStyle : fillStyle(idx);
    ctx.fill();
    ctx.stroke();
  });
  ctx.restore();
}

function drawParcelLots(ctx: CanvasRenderingContext2D, artifact: CityArtifact, parcels: ParcelLot[], viewport: Viewport): void {
  drawPolygonList(
    ctx,
    artifact,
    parcels,
    viewport,
    (idx) => parcelColor(parcels[idx]?.parcel_class ?? 'residential_candidate'),
    'rgba(140, 205, 235, 0.22)',
    0.5,
  );
}

function drawLandBlocks(ctx: CanvasRenderingContext2D, artifact: CityArtifact, blocks: LandBlock[], viewport: Viewport): void {
  drawPolygonList(ctx, artifact, blocks, viewport, 'rgba(0,0,0,0)', 'rgba(226, 242, 255, 0.28)', 0.9);
}

function drawPedestrianPaths(ctx: CanvasRenderingContext2D, artifact: CityArtifact, paths: PedestrianPath[], viewport: Viewport): void {
  if (!paths.length) return;
  const extent = artifact.terrain.extent_m;
  const { width, height } = ctx.canvas;
  const worldScale = (width / Math.max(extent, 1)) * viewport.scale;
  ctx.save();
  ctx.strokeStyle = 'rgba(116, 226, 255, 0.58)';
  ctx.lineCap = 'round';
  for (const path of paths) {
    if (!path.points?.length || path.points.length < 2) continue;
    ctx.lineWidth = Math.max(0.6, ((path.width_m ?? 3) * worldScale) / 9);
    ctx.beginPath();
    path.points.forEach((pt, i) => {
      const p = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  }
  ctx.restore();
}

export function drawStageScene({
  ctx,
  artifact,
  stage,
  viewport,
  terrainBitmap,
  layers,
  nowMs,
  reducedMotion,
  transparentBackground,
}: Params): void {
  if (!artifact) {
    drawCity(ctx, artifact, viewport, terrainBitmap, {
      terrain: false,
      rivers: false,
      roads: false,
      debugCandidates: false,
    });
    return;
  }

  const baseLayers: LayerFlags = {
    terrain: layers.terrain && (!stage || hasStageLayer(stage, 'terrain')),
    rivers: layers.rivers && (!stage || hasStageLayer(stage, 'rivers') || hasStageLayer(stage, 'river_areas')),
    roads: layers.roads && (!stage || hasStageLayer(stage, 'roads')),
    debugCandidates: layers.debugCandidates && stage?.stage_id === 'infrastructure',
    transparentBackground,
  };

  drawCity(ctx, artifact, viewport, terrainBitmap, baseLayers);

  if (stage && layers.contours && hasStageLayer(stage, 'contours')) {
    drawContours(ctx, artifact, stage, viewport);
  }

  if (stage && layers.analysis && hasStageLayer(stage, 'analysis_heatmaps')) {
    const analysis = stage.layers;
    if (analysis.suitability_preview) drawHeatGrid(ctx, analysis.suitability_preview, artifact, viewport, 'suitability');
    if (analysis.flood_risk_preview) drawHeatGrid(ctx, analysis.flood_risk_preview, artifact, viewport, 'flood');
    if (analysis.population_potential_preview) drawHeatGrid(ctx, analysis.population_potential_preview, artifact, viewport, 'population');
  }

  if (stage && layers.greenZones && hasStageLayer(stage, 'green_zones') && stage.layers.green_zones_preview) {
    drawGreenZones(ctx, artifact, stage.layers.green_zones_preview, viewport);
  }

  if (stage && layers.blocks && hasStageLayer(stage, 'blocks') && stage.layers.land_blocks) {
    drawLandBlocks(ctx, artifact, stage.layers.land_blocks, viewport);
  }

  if (stage && layers.parcels && hasStageLayer(stage, 'parcels') && stage.layers.parcel_lots) {
    drawParcelLots(ctx, artifact, stage.layers.parcel_lots, viewport);
  }

  if (stage && layers.pedestrianPaths && hasStageLayer(stage, 'pedestrian_paths') && stage.layers.pedestrian_paths) {
    drawPedestrianPaths(ctx, artifact, stage.layers.pedestrian_paths, viewport);
  }

  if (stage && layers.buildings && hasStageLayer(stage, 'buildings') && stage.layers.building_footprints) {
    drawBuildingFootprints(ctx, artifact, stage.layers.building_footprints, viewport);
  }

  if (stage && layers.resources && hasStageLayer(stage, 'resources') && stage.layers.resource_sites) {
    drawResources(ctx, artifact, stage.layers.resource_sites, viewport);
  }

  if (stage && layers.traffic && hasStageLayer(stage, 'traffic_heat') && stage.layers.traffic_edge_flows) {
    drawTrafficFlows(ctx, artifact, stage.layers.traffic_edge_flows, viewport, nowMs, reducedMotion);
  }
}
