import type { CityArtifact } from '../types/city';
import { worldToScreen, type Viewport } from './viewport';

export type LayerFlags = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  debugCandidates: boolean;
};

export function drawCity(
  ctx: CanvasRenderingContext2D,
  artifact: CityArtifact | null,
  viewport: Viewport,
  terrainBitmap: ImageBitmap | null,
  layers: LayerFlags,
): void {
  const { width, height } = ctx.canvas;
  ctx.clearRect(0, 0, width, height);

  // Solid fallback background (UI shell avoids gradients by design).
  ctx.fillStyle = 'rgba(6, 14, 24, 1)';
  ctx.fillRect(0, 0, width, height);

  if (!artifact) {
    ctx.fillStyle = 'rgba(188, 242, 255, 0.85)';
    ctx.font = '14px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.fillText('No artifact loaded.', 16, 24);
    return;
  }

  const extent = artifact.terrain.extent_m;
  if (layers.terrain && terrainBitmap) {
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
    ctx.save();
    for (const edge of artifact.roads.edges) {
      const u = nodeMap.get(edge.u);
      const v = nodeMap.get(edge.v);
      if (!u || !v) continue;
      const a = worldToScreen(u.x, u.y, extent, width, height, viewport);
      const b = worldToScreen(v.x, v.y, extent, width, height, viewport);
      ctx.strokeStyle = edge.road_class === 'arterial' ? 'rgba(234, 247, 255, 0.96)' : 'rgba(143, 226, 255, 0.72)';
      ctx.lineWidth = edge.road_class === 'arterial' ? 2.2 : 1.15;
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
