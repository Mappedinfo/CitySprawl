import type { CityArtifact, LandBlock, ParcelLot, PedestrianPath, Point2D, Polygon2D, ResourceSite, StageArtifact } from '../types/city';
import { drawCity, type LayerFlags } from './cityRenderer';
import { drawBuildingFootprints, drawGreenZones } from './finalPreview';
import { drawTrafficFlows } from './trafficAnimation';
import { worldToScreen, type Viewport } from './viewport';

export type LayerToggles = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  majorRoads: boolean;
  localRoads: boolean;
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
  cssWidth: number;
  cssHeight: number;
};

function hasStageLayer(stage: StageArtifact | null, key: string): boolean {
  if (!stage) return false;
  return stage.visible_layers.includes(key);
}

function tracePolygonPath(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  polygon: Polygon2D,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): boolean {
  if (!polygon.points?.length || polygon.points.length < 3) return false;
  const extent = artifact.terrain.extent_m;
  ctx.beginPath();
  polygon.points.forEach((pt, idx) => {
    const s = worldToScreen(pt.x, pt.y, extent, cssWidth, cssHeight, viewport);
    if (idx === 0) ctx.moveTo(s.x, s.y);
    else ctx.lineTo(s.x, s.y);
  });
  ctx.closePath();
  return true;
}

function withPolygonClip(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  polygon: Polygon2D | null | undefined,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
  drawFn: () => void,
): void {
  if (!polygon || !polygon.points?.length) {
    drawFn();
    return;
  }
  ctx.save();
  if (tracePolygonPath(ctx, artifact, polygon, viewport, cssWidth, cssHeight)) {
    ctx.clip();
  }
  drawFn();
  ctx.restore();
}

function withStudyBoundaryClip(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
  drawFn: () => void,
): void {
  const extent = artifact.terrain.extent_m;
  const boundary: Polygon2D = {
    id: 'study-boundary',
    points: [
      { x: 0, y: 0 },
      { x: extent, y: 0 },
      { x: extent, y: extent },
      { x: 0, y: extent },
    ],
  };
  withPolygonClip(ctx, artifact, boundary, viewport, cssWidth, cssHeight, drawFn);
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
  cssWidth: number,
  cssHeight: number,
  kind: 'suitability' | 'flood' | 'population',
): void {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  if (!rows || !cols) return;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;

  ctx.save();
  for (let y = 0; y < rows - 1; y += 1) {
    for (let x = 0; x < cols - 1; x += 1) {
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

function drawResources(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  sites: ResourceSite[],
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;
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

function drawContours(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  stage: StageArtifact,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  const contours = stage.layers.contour_lines ?? artifact.terrain.contours ?? [];
  if (!contours.length) return;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;
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
  cssWidth: number,
  cssHeight: number,
  fillStyle: ((index: number) => string) | string,
  strokeStyle: string,
  lineWidth = 0.8,
): void {
  if (!polygons.length) return;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;
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

function drawParcelLots(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  parcels: ParcelLot[],
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  drawPolygonList(
    ctx,
    artifact,
    parcels,
    viewport,
    cssWidth,
    cssHeight,
    (idx) => parcelColor(parcels[idx]?.parcel_class ?? 'residential_candidate'),
    'rgba(140, 205, 235, 0.22)',
    0.5,
  );
}

function drawLandBlocks(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  blocks: LandBlock[],
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  drawPolygonList(ctx, artifact, blocks, viewport, cssWidth, cssHeight, 'rgba(0,0,0,0)', 'rgba(226, 242, 255, 0.28)', 0.9);
}

function drawLandBlockOutlines(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  blocks: LandBlock[],
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  drawPolygonList(
    ctx,
    artifact,
    blocks,
    viewport,
    cssWidth,
    cssHeight,
    'rgba(0,0,0,0)',
    'rgba(226, 242, 255, 0.58)',
    1.2,
  );
}

function drawPedestrianPaths(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  paths: PedestrianPath[],
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
): void {
  if (!paths.length) return;
  const extent = artifact.terrain.extent_m;
  const width = cssWidth;
  const height = cssHeight;
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
  cssWidth,
  cssHeight,
}: Params): void {
  if (!artifact) {
    drawCity(ctx, artifact, viewport, terrainBitmap, {
      terrain: false,
      rivers: false,
      roads: false,
      debugCandidates: false,
      showMajorRoads: layers.majorRoads,
      showLocalRoads: layers.localRoads,
      cssWidth,
      cssHeight,
    });
    return;
  }

  const baseLayers: LayerFlags = {
    terrain: layers.terrain && (!stage || hasStageLayer(stage, 'terrain')),
    rivers: layers.rivers && (!stage || hasStageLayer(stage, 'rivers') || hasStageLayer(stage, 'river_areas')),
    roads: layers.roads && (!stage || hasStageLayer(stage, 'roads')) && stage?.stage_id !== 'final_preview',
    debugCandidates: layers.debugCandidates && stage?.stage_id === 'infrastructure',
    showMajorRoads: layers.majorRoads,
    showLocalRoads: layers.localRoads,
    transparentBackground,
    cssWidth,
    cssHeight,
  };

  drawCity(ctx, artifact, viewport, terrainBitmap, baseLayers);
  const visualEnvelope = stage?.layers.visual_envelope ?? artifact.visual_envelope ?? null;

  withStudyBoundaryClip(ctx, artifact, viewport, cssWidth, cssHeight, () => {
    if (stage && layers.contours && hasStageLayer(stage, 'contours')) {
      drawContours(ctx, artifact, stage, viewport, cssWidth, cssHeight);
    }

    if (stage && layers.analysis && hasStageLayer(stage, 'analysis_heatmaps')) {
      const analysis = stage.layers;
      withPolygonClip(ctx, artifact, visualEnvelope, viewport, cssWidth, cssHeight, () => {
        if (analysis.suitability_preview) drawHeatGrid(ctx, analysis.suitability_preview, artifact, viewport, cssWidth, cssHeight, 'suitability');
        if (analysis.flood_risk_preview) drawHeatGrid(ctx, analysis.flood_risk_preview, artifact, viewport, cssWidth, cssHeight, 'flood');
        if (analysis.population_potential_preview) drawHeatGrid(ctx, analysis.population_potential_preview, artifact, viewport, cssWidth, cssHeight, 'population');
      });
    }

    if (stage && layers.greenZones && hasStageLayer(stage, 'green_zones') && stage.layers.green_zones_preview) {
      withPolygonClip(ctx, artifact, visualEnvelope, viewport, cssWidth, cssHeight, () => {
        drawGreenZones(ctx, artifact, stage.layers.green_zones_preview!, viewport, cssWidth, cssHeight);
      });
    }

    const showStageBlocks = Boolean(stage && layers.blocks && hasStageLayer(stage, 'blocks') && stage.layers.land_blocks);
    const showStageParcels = Boolean(stage && layers.parcels && hasStageLayer(stage, 'parcels') && stage.layers.parcel_lots);

    if (showStageBlocks && !showStageParcels) {
      drawLandBlocks(ctx, artifact, stage!.layers.land_blocks!, viewport, cssWidth, cssHeight);
    }

    if (showStageParcels) {
      drawParcelLots(ctx, artifact, stage!.layers.parcel_lots!, viewport, cssWidth, cssHeight);
    }

    if (showStageBlocks && showStageParcels) {
      drawLandBlockOutlines(ctx, artifact, stage!.layers.land_blocks!, viewport, cssWidth, cssHeight);
    }

    // In final preview, draw roads after parcels so they remain legible over fills.
    if (stage?.stage_id === 'final_preview' && layers.roads && hasStageLayer(stage, 'roads')) {
      drawCity(ctx, artifact, viewport, terrainBitmap, {
        terrain: false,
        rivers: false,
        roads: true,
        debugCandidates: false,
        showMajorRoads: layers.majorRoads,
        showLocalRoads: layers.localRoads,
        transparentBackground: true,
        preserveCanvas: true,
        cssWidth,
        cssHeight,
      });
    }

    if (stage && layers.pedestrianPaths && hasStageLayer(stage, 'pedestrian_paths') && stage.layers.pedestrian_paths) {
      drawPedestrianPaths(ctx, artifact, stage.layers.pedestrian_paths, viewport, cssWidth, cssHeight);
    }

    if (stage && layers.buildings && hasStageLayer(stage, 'buildings') && stage.layers.building_footprints) {
      drawBuildingFootprints(ctx, artifact, stage.layers.building_footprints, viewport, cssWidth, cssHeight);
    }

    if (stage && layers.resources && hasStageLayer(stage, 'resources') && stage.layers.resource_sites) {
      drawResources(ctx, artifact, stage.layers.resource_sites, viewport, cssWidth, cssHeight);
    }

    if (stage && layers.traffic && hasStageLayer(stage, 'traffic_heat') && stage.layers.traffic_edge_flows) {
      drawTrafficFlows(ctx, artifact, stage.layers.traffic_edge_flows, viewport, nowMs, reducedMotion, cssWidth, cssHeight);
    }
  });
}

export type StreamingTraceData = {
  partialTraces: Map<string, Point2D[]>;
  completedTraces: Array<{
    trace_id: string;
    points: Point2D[];
    road_class?: string;
    culdesac?: boolean;
  }>;
  nodes?: Map<string, { id: string; x: number; y: number; kind: string }>;
  edges?: Map<string, { id: string; u: string; v: string; road_class: string }>;
  polylineEdges?: Map<string, { id: string; u: string; v: string; roadClass: string; pathPoints: Point2D[] }>;
  rivers?: Array<{ river_id: string; centerline: Point2D[]; flow: number }>;
};

export function drawStreamingTraces(
  ctx: CanvasRenderingContext2D,
  data: StreamingTraceData,
  extent: number,
  viewport: Viewport,
  cssWidth: number,
  cssHeight: number,
  nowMs: number,
): void {
  ctx.save();

  // Draw completed traces (solid lines)
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  for (const trace of data.completedTraces) {
    if (trace.points.length < 2) continue;

    // Color based on road class
    if (trace.road_class === 'arterial') {
      ctx.strokeStyle = 'rgba(255, 200, 120, 0.9)';
      ctx.lineWidth = 3;
    } else if (trace.road_class === 'collector') {
      ctx.strokeStyle = 'rgba(200, 220, 255, 0.85)';
      ctx.lineWidth = 2;
    } else if (trace.road_class === 'local') {
      ctx.strokeStyle = 'rgba(120, 255, 180, 0.7)';
      ctx.lineWidth = 1.2;
    } else {
      ctx.strokeStyle = 'rgba(180, 200, 230, 0.75)';
      ctx.lineWidth = 1.5;
    }

    ctx.beginPath();
    trace.points.forEach((p, i) => {
      const s = worldToScreen(p.x, p.y, extent, cssWidth, cssHeight, viewport);
      if (i === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.stroke();

    // Draw culdesac indicator
    if (trace.culdesac && trace.points.length > 0) {
      const last = trace.points[trace.points.length - 1];
      const s = worldToScreen(last.x, last.y, extent, cssWidth, cssHeight, viewport);
      ctx.fillStyle = 'rgba(255, 180, 120, 0.8)';
      ctx.beginPath();
      ctx.arc(s.x, s.y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw finalized streamed polylines (covers grid_clip/supplement/rerouted roads that don't emit step-wise traces).
  if (data.polylineEdges && data.polylineEdges.size > 0) {
    for (const [, edge] of data.polylineEdges) {
      if (!edge.pathPoints || edge.pathPoints.length < 2) continue;
      if (edge.roadClass === 'arterial') {
        ctx.strokeStyle = 'rgba(236, 246, 255, 0.88)';
        ctx.lineWidth = 2.8;
      } else if (edge.roadClass === 'collector') {
        ctx.strokeStyle = 'rgba(194, 238, 255, 0.72)';
        ctx.lineWidth = 1.8;
      } else if (edge.roadClass === 'local') {
        ctx.strokeStyle = 'rgba(136, 214, 245, 0.45)';
        ctx.lineWidth = 1.1;
      } else {
        ctx.strokeStyle = 'rgba(180, 210, 240, 0.5)';
        ctx.lineWidth = 1.2;
      }
      ctx.beginPath();
      edge.pathPoints.forEach((p, i) => {
        const s = worldToScreen(p.x, p.y, extent, cssWidth, cssHeight, viewport);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.stroke();
    }
  }

  // Draw partial traces (growing, with animation)
  for (const [traceId, points] of data.partialTraces) {
    if (points.length < 2) continue;

    const isCollector = traceId.startsWith('collector-trace-');
    ctx.strokeStyle = isCollector
      ? 'rgba(100, 220, 255, 0.95)'
      : 'rgba(255, 220, 100, 0.95)';
    ctx.lineWidth = isCollector ? 3 : 2;

    ctx.beginPath();
    points.forEach((p, i) => {
      const s = worldToScreen(p.x, p.y, extent, cssWidth, cssHeight, viewport);
      if (i === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.stroke();

    // Draw pulsing head at the last point
    const last = points[points.length - 1];
    const s = worldToScreen(last.x, last.y, extent, cssWidth, cssHeight, viewport);
    const pulse = (nowMs % 800) / 800;
    const radius = 4 + pulse * (isCollector ? 10 : 6);
    const alpha = 1 - pulse;

    ctx.beginPath();
    ctx.arc(s.x, s.y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = isCollector
      ? `rgba(100, 220, 255, ${alpha})`
      : `rgba(255, 220, 100, ${alpha})`;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Solid center dot
    ctx.beginPath();
    ctx.arc(s.x, s.y, 3, 0, Math.PI * 2);
    ctx.fillStyle = isCollector
      ? 'rgba(180, 240, 255, 1)'
      : 'rgba(255, 240, 180, 1)';
    ctx.fill();
  }

  // Draw incremental nodes (if available)
  if (data.nodes && data.nodes.size > 0) {
    ctx.fillStyle = 'rgba(255, 200, 100, 0.8)';
    for (const [, node] of data.nodes) {
      const s = worldToScreen(node.x, node.y, extent, cssWidth, cssHeight, viewport);
      ctx.beginPath();
      ctx.arc(s.x, s.y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw incremental edges (if nodes are available for lookup)
  if ((!data.polylineEdges || data.polylineEdges.size === 0) && data.edges && data.edges.size > 0 && data.nodes && data.nodes.size > 0) {
    ctx.strokeStyle = 'rgba(200, 180, 255, 0.7)';
    ctx.lineWidth = 1.5;
    for (const [, edge] of data.edges) {
      const uNode = data.nodes.get(edge.u);
      const vNode = data.nodes.get(edge.v);
      if (!uNode || !vNode) continue;

      const s1 = worldToScreen(uNode.x, uNode.y, extent, cssWidth, cssHeight, viewport);
      const s2 = worldToScreen(vNode.x, vNode.y, extent, cssWidth, cssHeight, viewport);
      ctx.beginPath();
      ctx.moveTo(s1.x, s1.y);
      ctx.lineTo(s2.x, s2.y);
      ctx.stroke();
    }
  }

  // Draw river centerlines (if available)
  if (data.rivers && data.rivers.length > 0) {
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const river of data.rivers) {
      if (river.centerline.length < 2) continue;
      const width = Math.max(1.5, Math.min(4, 0.75 + Math.log10(1 + river.flow) * 0.9));
      ctx.strokeStyle = 'rgba(96, 224, 255, 0.8)';
      ctx.lineWidth = width;
      ctx.beginPath();
      river.centerline.forEach((p, i) => {
        const s = worldToScreen(p.x, p.y, extent, cssWidth, cssHeight, viewport);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.stroke();
    }
  }

  // Progress indicator overlay
  const totalPartial = data.partialTraces.size;
  const totalCompleted = data.completedTraces.length;
  if (totalPartial > 0 || totalCompleted > 0) {
    ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.font = '12px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`Roads: ${totalCompleted} done, ${totalPartial} active`, 10, cssHeight - 14);
  }

  ctx.restore();
}
