import type { CityArtifact, TrafficEdgeFlow } from '../types/city';
import type { Viewport } from './viewport';
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

export function drawTrafficFlows(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact,
  flows: TrafficEdgeFlow[],
  viewport: Viewport,
  timeMs: number,
  reducedMotion: boolean,
): void {
  const nodeMap = new Map(artifact.roads.nodes.map((n) => [n.id, n]));
  const edgeMap = new Map(artifact.roads.edges.map((e) => [e.id, e]));
  const { width, height } = ctx.canvas;
  const extent = artifact.terrain.extent_m;

  ctx.save();
  for (const flow of flows) {
    const edge = edgeMap.get(flow.edge_id);
    if (!edge) continue;
    const u = nodeMap.get(edge.u);
    const v = nodeMap.get(edge.v);
    if (!u || !v) continue;
    const a = worldToScreen(u.x, u.y, extent, width, height, viewport);
    const b = worldToScreen(v.x, v.y, extent, width, height, viewport);
    const congestion = Math.max(0, Math.min(2, flow.congestion_ratio));
    const t = Math.min(1, congestion);
    ctx.strokeStyle = trafficColor(t);
    ctx.lineWidth = edge.road_class === 'arterial' ? 2.8 : 1.8;
    ctx.globalAlpha = 0.35 + Math.min(0.55, congestion * 0.25);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();

    if (!reducedMotion && flow.flow > 0) {
      const phase = ((timeMs * (0.00008 + t * 0.00012)) % 1 + 1) % 1;
      const px = a.x + (b.x - a.x) * phase;
      const py = a.y + (b.y - a.y) * phase;
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = trafficColor(Math.min(1, t + 0.12));
      ctx.beginPath();
      ctx.arc(px, py, edge.road_class === 'arterial' ? 2.4 : 1.6, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.restore();
}
