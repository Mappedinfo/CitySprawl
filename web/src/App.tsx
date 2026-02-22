import { useEffect, useMemo, useRef, useState } from 'react';

import { fetchHealth, fetchPresets, generateCity } from './api/client';
import { drawCity } from './render/cityRenderer';
import { heightGridToImageData } from './render/terrainImage';
import { clampScale, screenToWorld, worldToScreen, type Viewport } from './render/viewport';
import type { CityArtifact, GenerateConfig, HubRecord, PresetsResponse } from './types/city';
import { Controls } from './ui/Controls';
import { MetricsPanel } from './ui/MetricsPanel';

const defaultConfig: GenerateConfig = {
  seed: 42,
  extent_m: 2048,
  grid_resolution: 256,
  terrain: { noise_octaves: 5, relief_strength: 1 },
  hydrology: { enable: true, accum_threshold: 0.015, min_river_length_m: 120 },
  hubs: { t1_count: 1, t2_count: 4, t3_count: 20, min_distance_m: 120 },
  roads: { k_neighbors: 4, loop_budget: 3, branch_steps: 2, slope_penalty: 2, river_cross_penalty: 300 },
  naming: { provider: 'mock' },
};

type LayerState = {
  terrain: boolean;
  rivers: boolean;
  roads: boolean;
  debugCandidates: boolean;
  labels: boolean;
};

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [artifact, setArtifact] = useState<CityArtifact | null>(null);
  const [config, setConfig] = useState<GenerateConfig>(defaultConfig);
  const [presets, setPresets] = useState<PresetsResponse>({ default: defaultConfig });
  const [selectedPreset, setSelectedPreset] = useState('default');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<string>('checking');
  const [selectedHubId, setSelectedHubId] = useState<string | null>(null);
  const [viewport, setViewport] = useState<Viewport>({ panX: 0, panY: 0, scale: 1 });
  const [layers, setLayers] = useState<LayerState>({
    terrain: true,
    rivers: true,
    roads: true,
    debugCandidates: false,
    labels: true,
  });
  const [terrainBitmap, setTerrainBitmap] = useState<ImageBitmap | null>(null);

  const selectedHub = useMemo<HubRecord | null>(() => {
    if (!artifact || !selectedHubId) return null;
    return artifact.hubs.find((h) => h.id === selectedHubId) ?? null;
  }, [artifact, selectedHubId]);

  useEffect(() => {
    fetchHealth()
      .then((res) => setHealth(res.status))
      .catch(() => setHealth('offline'));
    fetchPresets()
      .then((data) => {
        setPresets(data);
        if (data.default) setConfig(data.default);
      })
      .catch(() => {
        // Keep local default if backend unavailable.
      });
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    drawCity(ctx, artifact, viewport, terrainBitmap, layers);
  }, [artifact, viewport, terrainBitmap, layers]);

  useEffect(() => {
    let cancelled = false;
    async function makeBitmap() {
      if (!artifact) {
        setTerrainBitmap(null);
        return;
      }
      const imageData = heightGridToImageData(artifact.terrain.heights);
      if (!imageData) {
        setTerrainBitmap(null);
        return;
      }
      try {
        const bitmap = await createImageBitmap(imageData);
        if (!cancelled) setTerrainBitmap(bitmap);
      } catch {
        if (!cancelled) setTerrainBitmap(null);
      }
    }
    void makeBitmap();
    return () => {
      cancelled = true;
    };
  }, [artifact]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrapper || !canvas) return;
    const resize = () => {
      const rect = wrapper.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawCity(ctx, artifact, viewport, terrainBitmap, layers);
      }
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrapper);
    return () => ro.disconnect();
  }, [artifact, viewport, terrainBitmap, layers]);

  const onGenerate = async () => {
    setLoading(true);
    setError(null);
    setSelectedHubId(null);
    try {
      const next = await generateCity(config);
      setArtifact(next);
      setViewport({ panX: 0, panY: 0, scale: 1 });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void onGenerate();
    // Intentionally only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onExport = () => {
    if (!artifact) return;
    const blob = new Blob([JSON.stringify(artifact, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `citygen-seed-${artifact.meta.seed}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onPresetChange = (name: string) => {
    setSelectedPreset(name);
    const preset = presets[name];
    if (preset) setConfig(preset);
  };

  const onLayerToggle = (key: keyof LayerState) => {
    setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const dragRef = useRef<{ active: boolean; x: number; y: number } | null>(null);

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
    dragRef.current = { active: true, x: e.clientX, y: e.clientY };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (!drag?.active) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    dragRef.current = { active: true, x: e.clientX, y: e.clientY };
    setViewport((prev) => ({ ...prev, panX: prev.panX + dx, panY: prev.panY + dy }));
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current) dragRef.current.active = false;
    (e.currentTarget as HTMLCanvasElement).releasePointerCapture(e.pointerId);
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    if (!canvas || !wrapper) return;
    const rect = wrapper.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = e.deltaY > 0 ? 0.9 : 1.1;
    setViewport((prev) => {
      const nextScale = clampScale(prev.scale * factor);
      const scaleRatio = nextScale / prev.scale;
      return {
        scale: nextScale,
        panX: px - (px - prev.panX) * scaleRatio,
        panY: py - (py - prev.panY) * scaleRatio,
      };
    });
  };

  const handleOverlayClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!artifact || !wrapperRef.current) return;
    const rect = wrapperRef.current.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const world = screenToWorld(sx, sy, artifact.terrain.extent_m, rect.width, rect.height, viewport);

    let best: HubRecord | null = null;
    let bestDist = Infinity;
    for (const hub of artifact.hubs) {
      const dx = hub.x - world.x;
      const dy = hub.y - world.y;
      const d = Math.hypot(dx, dy);
      if (d < bestDist) {
        bestDist = d;
        best = hub;
      }
    }
    const thresholdWorld = (artifact.terrain.extent_m / rect.width) * 14 / Math.max(viewport.scale, 0.5);
    setSelectedHubId(best && bestDist <= thresholdWorld ? best.id : null);
  };

  const extent = artifact?.terrain.extent_m ?? config.extent_m;

  return (
    <div className="app-shell">
      <Controls
        config={config}
        presets={presets}
        selectedPreset={selectedPreset}
        onPresetChange={onPresetChange}
        onConfigChange={setConfig}
        onGenerate={onGenerate}
        onExport={onExport}
        loading={loading}
        layers={layers}
        onLayerToggle={onLayerToggle}
      />

      <main className="canvas-stage-wrap">
        <div className="stage-header">
          <div className="status-pill">API: {health}</div>
          {error ? <div className="error-banner">{error}</div> : null}
        </div>
        <div ref={wrapperRef} className="canvas-stage">
          <canvas
            ref={canvasRef}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            onWheel={handleWheel}
          />
          {artifact ? (
            <svg className="overlay" onClick={handleOverlayClick}>
              {artifact.hubs.map((hub) => {
                const rect = wrapperRef.current?.getBoundingClientRect();
                if (!rect) return null;
                const s = worldToScreen(hub.x, hub.y, extent, rect.width, rect.height, viewport);
                const r = hub.tier === 1 ? 6 : hub.tier === 2 ? 4.5 : 3.5;
                const isSelected = hub.id === selectedHubId;
                return (
                  <g key={hub.id} transform={`translate(${s.x}, ${s.y})`}>
                    <circle
                      r={r}
                      fill={hub.tier === 1 ? '#f2c14e' : hub.tier === 2 ? '#de7d3a' : '#2d6a8e'}
                      stroke={isSelected ? '#111' : '#ffffff'}
                      strokeWidth={isSelected ? 2 : 1}
                    />
                    {layers.labels ? (
                      <text x={r + 4} y={-r - 2} className="hub-label">
                        {hub.name ?? hub.id}
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </svg>
          ) : null}
        </div>
      </main>

      <MetricsPanel artifact={artifact} selectedHub={selectedHub} />
    </div>
  );
}
