import type { CityArtifact, ResourceSite, StageArtifact } from '../types/city';
import { drawCity, type LayerFlags } from './cityRenderer';
import { drawBuildingFootprints, drawGreenZones } from './finalPreview';
import { drawTrafficFlows } from './trafficAnimation';
import { worldToScreen, type Viewport } from './viewport';

export type LayerToggles = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
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

export function drawStageScene({ ctx, artifact, stage, viewport, terrainBitmap, layers, nowMs, reducedMotion }: Params): void {
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
    rivers: layers.rivers && (!stage || hasStageLayer(stage, 'rivers')),
    roads: layers.roads && (!stage || hasStageLayer(stage, 'roads')),
    debugCandidates: layers.debugCandidates && stage?.stage_id === 'infrastructure',
  };

  drawCity(ctx, artifact, viewport, terrainBitmap, baseLayers);

  if (stage && layers.analysis && hasStageLayer(stage, 'analysis_heatmaps')) {
    const analysis = stage.layers;
    if (analysis.suitability_preview) drawHeatGrid(ctx, analysis.suitability_preview, artifact, viewport, 'suitability');
    if (analysis.flood_risk_preview) drawHeatGrid(ctx, analysis.flood_risk_preview, artifact, viewport, 'flood');
    if (analysis.population_potential_preview) drawHeatGrid(ctx, analysis.population_potential_preview, artifact, viewport, 'population');
  }

  if (stage && layers.greenZones && hasStageLayer(stage, 'green_zones') && stage.layers.green_zones_preview) {
    drawGreenZones(ctx, artifact, stage.layers.green_zones_preview, viewport);
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
