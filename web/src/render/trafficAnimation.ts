import type { CityArtifact, TrafficEdgeFlow } from '../types/city';
import type { Viewport } from './viewport';
import { roadStrokeWidthPx } from './cityRenderer';
import { worldToScreen } from './viewport';

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function trafficColor(t: number): string {
  const c = Math.max(0, Math.min(1, t));
  const r = Math.round(lerp(28, 255, c));
  const g = Math.round(lerp(180, 90, c));
  const b = Math.round(lerp(245, 48, c));
  return `rgba(${r}, ${g}, ${b}, 0.95)`;
}

function polylineLength(points: Array<{ x: number; y: number }>): number {
  let len = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    len += Math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y);
  }
  return len;
}

function pointAlongPolyline(points: Array<{ x: number; y: number }>, t: number): { x: number; y: number } {
  if (points.length <= 1) return points[0] ?? { x: 0, y: 0 };
  const total = polylineLength(points);
  if (total <= 1e-6) return points[0];
  let target = Math.max(0, Math.min(1, t)) * total;
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const seg = Math.hypot(b.x - a.x, b.y - a.y);
    if (target <= seg || i === points.length - 2) {
      const u = seg <= 1e-6 ? 0 : target / seg;
      return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u };
    }
    target -= seg;
  }
  return points[points.length - 1];
}

export function drawTrafficFlows(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  flows: TrafficEdgeFlow[],
  viewport: Viewport,
  timeMs: number,
  reducedMotion: boolean,
  cssWidth: number,
  cssHeight: number,
): void {
  const nodeMap = new Map(artifact.roads.nodes.map((n) => [n.id, n]));
  const edgeMap = new Map(artifact.roads.edges.map((e) => [e.id, e]));
  const width = cssWidth;
  const height = cssHeight;
  const extent = artifact.terrain.extent_m;

  ctx.save();
  for (const flow of flows) {
    const edge = edgeMap.get(flow.edge_id);
    if (!edge) continue;
    const u = nodeMap.get(edge.u);
    const v = nodeMap.get(edge.v);
    if (!u || !v) continue;
    const path = edge.path_points && edge.path_points.length >= 2 ? edge.path_points : [{ x: u.x, y: u.y }, { x: v.x, y: v.y }];
    const congestion = Math.max(0, Math.min(2, flow.congestion_ratio));
    const t = Math.min(1, congestion);
    ctx.strokeStyle = trafficColor(t);
    ctx.lineWidth = Math.max(0.9, Math.min(6, roadStrokeWidthPx(edge, extent, width, viewport.scale) * (edge.road_class === 'arterial' ? 0.65 : 0.55)));
    ctx.globalAlpha = 0.35 + Math.min(0.55, congestion * 0.25);
    ctx.beginPath();
    path.forEach((pt, idx) => {
      const s = worldToScreen(pt.x, pt.y, extent, width, height, viewport);
      if (idx === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.stroke();

    if (!reducedMotion && flow.flow > 0) {
      const phase = ((timeMs * (0.00008 + t * 0.00012)) % 1 + 1) % 1;
      const wp = pointAlongPolyline(path, phase);
      const s = worldToScreen(wp.x, wp.y, extent, width, height, viewport);
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = trafficColor(Math.min(1, t + 0.12));
      ctx.beginPath();
      const pulseR = Math.max(1.2, Math.min(4.5, ctx.lineWidth * 0.75));
      ctx.arc(s.x, s.y, pulseR, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.restore();
}
