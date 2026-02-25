import { useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchGenerateJobResult,
  fetchGenerateJobStatus,
  fetchHealth,
  fetchPresets,
  generateCity,
  generateCityStaged,
  generateCityV2,
  loadStagedJson,
  startGenerateCityV2Async,
} from './api/client';
import { useStreamingGeneration, type IncrementalState } from './hooks/useStreamingGeneration';
import { drawStageScene, drawStreamingTraces, type LayerToggles } from './render/stageRenderer';
import { heightGridToImageData } from './render/terrainImage';
import { clampScale, screenToWorld, worldToScreen, type Viewport } from './render/viewport';
import { TerrainScene } from './render3d/TerrainScene';
import { composeFallbackStagedResponse } from './timeline/stageComposer';
import { UNIFIED_STAGE_DEFS, canonicalizePhaseId, normalizeStagesForUi } from './timeline/unifiedStages';
import { useTimelinePlayer } from './timeline/useTimelinePlayer';
import type {
  CityArtifact,
  GenerateConfig,
  GenerateJobLog,
  GenerateJobStatusResponse,
  HubRecord,
  PresetsResponse,
  StageArtifact,
  StagedCityResponse,
} from './types/city';
import { Controls } from './ui/Controls';
import { CaptionsOverlay } from './ui/CaptionsOverlay';
import { MetricsPanel } from './ui/MetricsPanel';
import { NorthArrow } from './ui/NorthArrow';
import { ScaleBar } from './ui/ScaleBar';
import { StageInspector } from './ui/StageInspector';

const defaultConfig: GenerateConfig = {
  seed: 42,
  extent_m: 10000,
  grid_resolution: 256,
  quality: { profile: 'balanced', time_budget_ms: 15000 },
  terrain: { noise_octaves: 5, relief_strength: 1 },
  hydrology: {
    enable: true,
    accum_threshold: 0.015,
    min_river_length_m: 1000,
    primary_branch_count_max: 4,
    centerline_smooth_iters: 2,
    width_taper_strength: 0.35,
    bank_irregularity: 0.08,
  },
  hubs: { t1_count: 1, t2_count: 4, t3_count: 20, min_distance_m: 600 },
  roads: {
    k_neighbors: 4,
    loop_budget: 3,
    branch_steps: 2,
    slope_penalty: 2,
    river_cross_penalty: 300,
    style: 'mixed_organic',
    collector_spacing_m: 420,
    local_spacing_m: 130,
    collector_jitter: 0.16,
    local_jitter: 0.22,
    river_setback_m: 18,
    minor_bridge_budget: 4,
    max_local_block_area_m2: 180000,
  },
  parcels: {
    enable: true,
    residential_target_area_m2: 1800,
    mixed_target_area_m2: 2600,
    min_frontage_m: 10,
    min_depth_m: 12,
  },
  naming: { provider: 'mock' },
};

const TIMELINE_TOTAL_MS = UNIFIED_STAGE_DEFS[UNIFIED_STAGE_DEFS.length - 1]?.timestampMs ?? 20_000;
const USE_THREE_TERRAIN = true;

type StageSource = 'none' | 'v2' | 'staged' | 'fallback' | 'json';

type LayerState = LayerToggles;
type GenerationProgress = {
  jobId: string;
  status: string;
  progress: number;
  phase: string;
  message: string;
  logs: GenerateJobLog[];
  updatedAt?: string;
};

type BackendStepDescriptor = {
  id: string;
  title: string;
  titleZh: string;
  anchor: number;
};

type BackendStepState = BackendStepDescriptor & {
  status: 'pending' | 'active' | 'done';
  localProgress: number;
};

const BACKEND_PROGRESS_STEPS: BackendStepDescriptor[] = UNIFIED_STAGE_DEFS.map((stage) => ({
  id: stage.id,
  title: stage.title,
  titleZh: stage.titleZh,
  anchor: stage.anchor,
}));

// Default zoom multiplier for initial view - shows only part of the scene like a game
const DEFAULT_ZOOM_MULTIPLIER = 2.2;

function fitViewportToArtifact(
  artifact: CityArtifact,
  cssWidth: number,
  cssHeight: number,
): Viewport {
  const extent = artifact.terrain.extent_m;
  const clampPoint = (x: number, y: number) => ({
    x: Math.max(0, Math.min(extent, x)),
    y: Math.max(0, Math.min(extent, y)),
  });
  const pts: Array<{ x: number; y: number }> = [];
  const fallbackPts: Array<{ x: number; y: number }> = [];
  const nodeById = new Map(artifact.roads.nodes.map((n) => [n.id, n] as const));

  for (const hub of artifact.hubs) {
    pts.push(clampPoint(hub.x, hub.y));
  }

  for (const edge of artifact.roads.edges) {
    if (edge.path_points && edge.path_points.length >= 2) {
      for (const p of edge.path_points) pts.push(clampPoint(p.x, p.y));
      continue;
    }
    const u = nodeById.get(edge.u);
    const v = nodeById.get(edge.v);
    if (u) pts.push(clampPoint(u.x, u.y));
    if (v) pts.push(clampPoint(v.x, v.y));
  }

  // Use river centerlines for framing instead of buffered river polygons; polygons can extend
  // beyond the study boundary and distort zoom.
  for (const river of artifact.rivers ?? []) {
    for (const p of river.points) pts.push(clampPoint(p.x, p.y));
  }

  // Fallback points if the city graph is empty.
  for (const river of artifact.river_areas ?? []) {
    for (const p of river.points) fallbackPts.push(clampPoint(p.x, p.y));
  }
  for (const ped of artifact.pedestrian_paths ?? []) {
    for (const p of ped.points) fallbackPts.push(clampPoint(p.x, p.y));
  }
  if (!pts.length) pts.push(...fallbackPts);

  if (!pts.length || cssWidth <= 0 || cssHeight <= 0) {
    return { panX: 0, panY: 0, scale: DEFAULT_ZOOM_MULTIPLIER };
  }

  // Find bounding box of city content
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of pts) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }

  // Center of city content in world coordinates
  const cx = (minX + maxX) * 0.5;
  const cy = (minY + maxY) * 0.5;

  // Use a zoomed-in scale (game-like view showing only part of the scene)
  const scale = clampScale(DEFAULT_ZOOM_MULTIPLIER);

  // Compute fit parameters for coordinate conversion
  const fitScale = Math.min(cssWidth, cssHeight) / extent;
  const offsetX = (cssWidth - extent * fitScale) / 2;
  const offsetY = (cssHeight - extent * fitScale) / 2;

  // Convert world center to base screen position (before viewport transform)
  const baseScreenX = cx * fitScale + offsetX;
  const baseScreenY = (extent - cy) * fitScale + offsetY;

  // Calculate pan to center the city content on screen
  const panX = cssWidth * 0.5 - baseScreenX * scale;
  const panY = cssHeight * 0.5 - baseScreenY * scale;

  return { panX, panY, scale };
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function canonicalBackendPhase(phase: string): string {
  return canonicalizePhaseId(phase);
}

function buildBackendStepStates(progress: GenerationProgress | null): BackendStepState[] {
  if (!progress) {
    return BACKEND_PROGRESS_STEPS.map((step, idx) => ({
      ...step,
      status: idx === 0 ? 'active' : 'pending',
      localProgress: idx === 0 ? 0.02 : 0,
    }));
  }

  const globalProgress = clamp01(progress.progress || 0);
  const phaseId = canonicalBackendPhase(progress.phase || progress.status || '');
  const phaseIndex = BACKEND_PROGRESS_STEPS.findIndex((step) => step.id === phaseId);
  const inferredIndex =
    phaseIndex >= 0
      ? phaseIndex
      : BACKEND_PROGRESS_STEPS.reduce((acc, step, idx) => (globalProgress >= step.anchor ? idx : acc), 0);
  const currentIdx = Math.max(0, inferredIndex);

  return BACKEND_PROGRESS_STEPS.map((step, idx) => {
    const nextAnchor = BACKEND_PROGRESS_STEPS[idx + 1]?.anchor ?? 1.0;
    let localProgress = 0;
    if (idx < currentIdx) {
      localProgress = 1;
    } else if (idx === currentIdx) {
      if (nextAnchor <= step.anchor) {
        localProgress = globalProgress >= step.anchor ? 1 : 0;
      } else {
        localProgress = clamp01((globalProgress - step.anchor) / (nextAnchor - step.anchor));
      }
      if (globalProgress > step.anchor || progress.status === 'streaming' || progress.status === 'running') {
        localProgress = Math.max(localProgress, 0.06);
      }
    }

    const done =
      idx < currentIdx ||
      (idx === currentIdx &&
        ((progress.status === 'completed' && globalProgress >= 0.999) || (phaseId === 'done' && globalProgress >= step.anchor)));
    const status: BackendStepState['status'] = done ? 'done' : idx === currentIdx ? 'active' : 'pending';
    return {
      ...step,
      status,
      localProgress: status === 'done' ? 1 : localProgress,
    };
  });
}

function stageLocalProgress(stages: StageArtifact[], idx: number, currentTimeMs: number, totalMs: number): number {
  const start = stages[idx]?.timestamp_ms ?? 0;
  if (idx === stages.length - 1) {
    if (currentTimeMs <= start) return currentTimeMs >= totalMs ? 1 : 0;
    if (currentTimeMs >= totalMs) return 1;
    return clamp01((currentTimeMs - start) / Math.max(totalMs - start, 1));
  }
  const end = idx < stages.length - 1 ? (stages[idx + 1]?.timestamp_ms ?? totalMs) : totalMs;
  if (currentTimeMs <= start) return 0;
  if (currentTimeMs >= end) return 1;
  return clamp01((currentTimeMs - start) / Math.max(end - start, 1));
}

export default function App() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const [response, setResponse] = useState<StagedCityResponse | null>(null);
  const [config, setConfig] = useState<GenerateConfig>(defaultConfig);
  const [presets, setPresets] = useState<PresetsResponse>({ default: defaultConfig });
  const [selectedPreset, setSelectedPreset] = useState('default');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generationProgress, setGenerationProgress] = useState<GenerationProgress | null>(null);
  const [stagedJsonPath, setStagedJsonPath] = useState('');
  const [health, setHealth] = useState<string>('checking');
  const [selectedHubId, setSelectedHubId] = useState<string | null>(null);
  const [viewport, setViewport] = useState<Viewport>({ panX: 0, panY: 0, scale: 1 });
  const [stageSize, setStageSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [source, setSource] = useState<StageSource>('none');
  const [reducedMotion, setReducedMotion] = useState(false);
  const [layers, setLayers] = useState<LayerState>({
    terrain: true,
    rivers: true,
    roads: true,
    majorRoads: true,
    localRoads: true,
    contours: true,
    blocks: true,
    parcels: true,
    pedestrianPaths: true,
    debugCandidates: false,
    labels: true,
    analysis: true,
    resources: true,
    traffic: true,
    buildings: true,
    greenZones: true,
  });
  const [terrainBitmap, setTerrainBitmap] = useState<ImageBitmap | null>(null);
  const generateRunRef = useRef(0);

  // Streaming generation hook
  const streaming = useStreamingGeneration();
  const isStreaming = streaming.status === 'streaming' || streaming.status === 'connecting';
  // Ref to avoid restarting the animation loop on every streaming state change
  const streamingStateRef = useRef(streaming.state);
  streamingStateRef.current = streaming.state;

  // Sync streaming progress to generationProgress UI
  useEffect(() => {
    if (!isStreaming) return;
    const { phase, progress, message } = streaming.progress;
    if (!phase && progress === 0) return; // no progress yet
    setGenerationProgress((prev) => {
      if (!prev || prev.jobId !== 'streaming') return prev;
      return {
        ...prev,
        progress,
        phase,
        message,
        status: 'streaming',
      };
    });
  }, [isStreaming, streaming.progress]);

  const artifact: CityArtifact | null = response?.final_artifact ?? null;
  const stages: StageArtifact[] = useMemo(() => normalizeStagesForUi(response?.stages ?? []), [response?.stages]);
  const hasStages = stages.length > 0;

  const timeline = useTimelinePlayer(stages, TIMELINE_TOTAL_MS);
  const currentStage = stages[timeline.currentStageIndex] ?? null;
  const stageShowsTerrain = !currentStage || currentStage.visible_layers.includes('terrain');
  const backendStepStates = useMemo(() => buildBackendStepStates(generationProgress), [generationProgress]);
  const sprawlProgressMode = hasStages && !loading && generationProgress?.status !== 'failed' ? 'progress+stages' : 'progress-only';
  const canStageClick = sprawlProgressMode === 'progress+stages';
  const stageProgressInPrimaryBar = canStageClick && hasStages;
  const displayGenerationProgress: GenerationProgress = generationProgress ?? {
    jobId: 'idle',
    status: 'idle',
    progress: 0,
    phase: 'idle',
    message: 'Click Start to begin generation / 点击启动开始生成',
    logs: [],
  };
  const progressPhaseLabel = stageProgressInPrimaryBar
    ? `replay:${currentStage?.stage_id ?? 'terrain'}`
    : canonicalizePhaseId(displayGenerationProgress.phase || 'waiting');
  const progressMessageLabel = stageProgressInPrimaryBar
    ? `${timeline.playing ? '展示回放中' : '展示已暂停'}，点击下方阶段可切换展示内容`
    : (displayGenerationProgress.message || 'Waiting for backend...');

  const selectedHub = useMemo<HubRecord | null>(() => {
    if (!artifact || !selectedHubId) return null;
    return artifact.hubs.find((h) => h.id === selectedHubId) ?? null;
  }, [artifact, selectedHubId]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);

  useEffect(() => {
    fetchHealth()
      .then((res) => setHealth(res.status))
      .catch(() => setHealth('offline'));
    fetchPresets()
      .then((data) => {
        setPresets(data);
        if (data.default) {
          setConfig(data.default);
          setSelectedPreset('default');
        }
      })
      .catch(() => {
        // Keep local defaults if backend is unavailable.
      });
  }, []);

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

  const redraw = () => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const rect = wrapper?.getBoundingClientRect();
    const cssWidth = rect?.width ?? canvas.clientWidth ?? 1;
    const cssHeight = rect?.height ?? canvas.clientHeight ?? 1;
    drawStageScene({
      ctx,
      artifact,
      stage: currentStage,
      viewport,
      terrainBitmap,
      layers: USE_THREE_TERRAIN ? { ...layers, terrain: false } : layers,
      nowMs: timeline.currentTimeMs,
      reducedMotion,
      transparentBackground: USE_THREE_TERRAIN,
      cssWidth,
      cssHeight,
    });
  };

  useEffect(() => {
    redraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact, currentStage, viewport, terrainBitmap, layers, reducedMotion]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrapper || !canvas) return;

    const resize = () => {
      const rect = wrapper.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      setStageSize((prev) => {
        if (Math.abs(prev.width - rect.width) < 0.5 && Math.abs(prev.height - rect.height) < 0.5) return prev;
        return { width: rect.width, height: rect.height };
      });
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawStageScene({
        ctx,
        artifact,
        stage: currentStage,
        viewport,
        terrainBitmap,
        layers: USE_THREE_TERRAIN ? { ...layers, terrain: false } : layers,
        nowMs: timeline.currentTimeMs,
        reducedMotion,
        transparentBackground: USE_THREE_TERRAIN,
        cssWidth: rect.width,
        cssHeight: rect.height,
      });

      // Draw streaming traces overlay if streaming is active
      if (
        isStreaming
        && (
          streaming.state.partialTraces.size > 0
          || streaming.state.completedTraces.length > 0
          || streaming.state.polylineEdges.size > 0
          || streaming.state.nodes.size > 0
          || streaming.state.rivers.length > 0
        )
      ) {
        const extent = artifact?.terrain.extent_m ?? config.extent_m;
        drawStreamingTraces(
          ctx,
          streaming.state,
          extent,
          viewport,
          rect.width,
          rect.height,
          Date.now(),
        );
      }
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrapper);
    return () => ro.disconnect();
  }, [artifact, currentStage, viewport, terrainBitmap, layers, timeline.currentTimeMs, reducedMotion, isStreaming, streaming.state, config.extent_m]);

  // Animation loop for streaming visualization
  useEffect(() => {
    if (!isStreaming) return;

    let animationId: number;
    const animate = () => {
      const canvas = canvasRef.current;
      const wrapper = wrapperRef.current;
      if (!canvas || !wrapper) {
        animationId = requestAnimationFrame(animate);
        return;
      }

      const rect = wrapper.getBoundingClientRect();
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        animationId = requestAnimationFrame(animate);
        return;
      }

      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Redraw base scene
      drawStageScene({
        ctx,
        artifact,
        stage: currentStage,
        viewport,
        terrainBitmap,
        layers: USE_THREE_TERRAIN ? { ...layers, terrain: false } : layers,
        nowMs: Date.now(),
        reducedMotion,
        transparentBackground: USE_THREE_TERRAIN,
        cssWidth: rect.width,
        cssHeight: rect.height,
      });

      // Draw streaming traces overlay (read from ref to avoid dep-array thrashing)
      const sState = streamingStateRef.current;
      if (
        sState.partialTraces.size > 0
        || sState.completedTraces.length > 0
        || sState.polylineEdges.size > 0
        || sState.nodes.size > 0
        || sState.rivers.length > 0
      ) {
        const extent = artifact?.terrain.extent_m ?? config.extent_m;
        drawStreamingTraces(
          ctx,
          sState,
          extent,
          viewport,
          rect.width,
          rect.height,
          Date.now(),
        );
      }

      animationId = requestAnimationFrame(animate);
    };

    animationId = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animationId);
  }, [isStreaming, artifact, currentStage, viewport, terrainBitmap, layers, reducedMotion, config.extent_m]);

  const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

  const pollAsyncV2Job = async (
    jobId: string,
    runId: number,
  ): Promise<StagedCityResponse> => {
    let sinceSeq = 0;
    let mergedLogs: GenerateJobLog[] = [];
    for (;;) {
      const status: GenerateJobStatusResponse = await fetchGenerateJobStatus(jobId, sinceSeq);
      sinceSeq = status.last_log_seq ?? sinceSeq;
      if (status.logs?.length) {
        mergedLogs = [...mergedLogs, ...status.logs].slice(-120);
      }
      if (generateRunRef.current !== runId) {
        throw new Error('Generation cancelled by a newer request');
      }
      setGenerationProgress({
        jobId,
        status: status.status,
        progress: Math.max(0, Math.min(1, status.progress ?? 0)),
        phase: status.phase ?? '',
        message: status.message ?? '',
        logs: mergedLogs,
        updatedAt: status.updated_at,
      });
      if (status.status === 'completed' && status.result_ready) {
        const result = await fetchGenerateJobResult(jobId);
        return result;
      }
      if (status.status === 'failed') {
        throw new Error(status.error || status.message || 'Async generation failed');
      }
      await sleep(700);
    }
  };

  const onGenerate = async () => {
    const runId = generateRunRef.current + 1;
    generateRunRef.current = runId;
    setLoading(true);
    setError(null);
    setSelectedHubId(null);
    setGenerationProgress(null);
    streaming.reset();
    try {
      let nextResponse: StagedCityResponse | null = null;

      // Try streaming generation first
      try {
        setGenerationProgress({
          jobId: 'streaming',
          status: 'streaming',
          progress: 0,
          phase: 'connecting',
          message: 'Starting real-time streaming generation...',
          logs: [],
        });

        const streamResult = await streaming.start(config);
        if (streamResult && generateRunRef.current === runId) {
          nextResponse = streamResult;
          setResponse(streamResult);
          setSource('v2');
          setGenerationProgress({
            jobId: 'streaming',
            status: 'completed',
            progress: 1,
            phase: 'done',
            message: 'Streaming generation complete',
            logs: [],
          });
        }
      } catch (streamErr) {
        // Streaming failed, fall back to async polling
        if (generateRunRef.current !== runId) throw streamErr;
        console.warn('Streaming generation unavailable, falling back to async polling:', streamErr);
        streaming.reset();

        try {
          const asyncStart = await startGenerateCityV2Async(config);
          setGenerationProgress({
            jobId: asyncStart.job_id,
            status: asyncStart.status ?? 'queued',
            progress: 0,
            phase: 'queued',
            message: 'Queued in backend',
            logs: [],
          });
          const v2Async = await pollAsyncV2Job(asyncStart.job_id, runId);
          nextResponse = v2Async;
          setResponse(v2Async);
          setSource('v2');
        } catch (asyncErr) {
          // If async progress endpoints are unavailable, fall back to existing synchronous v2 path.
          if (generateRunRef.current !== runId) throw asyncErr;
          setGenerationProgress((prev) =>
            prev
              ? {
                  ...prev,
                  status: 'fallback',
                  message: 'Async progress unavailable, falling back to synchronous V2',
                  logs: [
                    ...prev.logs,
                    {
                      seq: ((prev.logs.length ? prev.logs[prev.logs.length - 1].seq : 0) ?? 0) + 1,
                      ts: new Date().toISOString(),
                      phase: 'fallback',
                      progress: prev.progress,
                      message: `Async progress unavailable: ${asyncErr instanceof Error ? asyncErr.message : String(asyncErr)}`,
                    },
                  ].slice(-120),
                }
              : null,
          );

          try {
            const v2 = await generateCityV2(config);
            nextResponse = v2;
            setResponse(v2);
            setSource('v2');
          } catch (v2Err) {
            try {
              const staged = await generateCityStaged(config);
              staged.final_artifact.metrics.degraded_mode = true;
              nextResponse = staged;
              setResponse(staged);
              setSource('staged');
              setError(`V2 API unavailable. Using v1 staged endpoint. ${v2Err instanceof Error ? v2Err.message : ''}`.trim());
            } catch (stagedErr) {
              const legacyArtifact = await generateCity(config);
              legacyArtifact.metrics.degraded_mode = true;
              const fallback = composeFallbackStagedResponse(legacyArtifact);
              fallback.final_artifact.metrics.degraded_mode = true;
              nextResponse = fallback;
              setResponse(fallback);
              setSource('fallback');
              setError(
                `V2 + staged API unavailable. Using legacy fallback timeline. ${
                  stagedErr instanceof Error ? stagedErr.message : ''
                }`.trim(),
              );
            }
          }
        }
      }

      const rect = wrapperRef.current?.getBoundingClientRect();
      if (nextResponse?.final_artifact && rect) {
        setViewport(fitViewportToArtifact(nextResponse.final_artifact, rect.width, rect.height));
      } else {
        setViewport({ panX: 0, panY: 0, scale: 1 });
      }
      timeline.reset(true);
    } catch (e) {
      if (generateRunRef.current !== runId) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (generateRunRef.current === runId) {
        setGenerationProgress((prev) =>
          prev
            ? {
                ...prev,
                status: prev.status === 'failed' || prev.status === 'completed' ? prev.status : 'idle',
                progress: prev.progress >= 1 ? prev.progress : prev.progress,
              }
            : prev,
        );
      }
      setLoading(false);
    }
  };

  const onLoadStagedJson = async () => {
    const path = stagedJsonPath.trim();
    if (!path) return;

    const runId = generateRunRef.current + 1;
    generateRunRef.current = runId;

    setLoading(true);
    setError(null);
    setSelectedHubId(null);
    streaming.reset();
    setGenerationProgress({
      jobId: 'json-load',
      status: 'running',
      progress: 0.05,
      phase: 'load_json',
      message: 'Loading staged JSON from backend file...',
      logs: [],
    });

    try {
      const loaded = await loadStagedJson(path);
      if (generateRunRef.current !== runId) return;

      setResponse(loaded);
      setSource('json');
      setGenerationProgress({
        jobId: 'json-load',
        status: 'completed',
        progress: 1,
        phase: 'done',
        message: 'Loaded staged JSON. Replaying growth stages from file.',
        logs: [],
      });

      const rect = wrapperRef.current?.getBoundingClientRect();
      if (rect) {
        setViewport(fitViewportToArtifact(loaded.final_artifact, rect.width, rect.height));
      } else {
        setViewport({ panX: 0, panY: 0, scale: 1 });
      }
      timeline.reset(true);
    } catch (e) {
      if (generateRunRef.current !== runId) return;
      setGenerationProgress({
        jobId: 'json-load',
        status: 'failed',
        progress: 0,
        phase: 'failed',
        message: 'Failed to load staged JSON',
        logs: [],
      });
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (generateRunRef.current === runId) setLoading(false);
    }
  };

  const onExport = () => {
    if (!response) return;
    const blob = new Blob([JSON.stringify(response, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `citygen-staged-seed-${response.final_artifact.meta.seed}.json`;
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

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { active: true, x: e.clientX, y: e.clientY };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag?.active) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    dragRef.current = { active: true, x: e.clientX, y: e.clientY };
    setViewport((prev) => ({ ...prev, panX: prev.panX + dx, panY: prev.panY + dy }));
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current) dragRef.current.active = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const handleWheel = (e: WheelEvent) => {
    e.preventDefault();
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
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

  // Attach wheel event with passive: false to allow preventDefault
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    wrapper.addEventListener('wheel', handleWheel, { passive: false });
    return () => {
      wrapper.removeEventListener('wheel', handleWheel);
    };
  }, []);

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
  const stageShowsHubs = !currentStage || currentStage.visible_layers.includes('hubs');
  const stageShowsLabels = !currentStage || currentStage.visible_layers.includes('labels');

  return (
    <div className="app-shell">
      <Controls
        config={config}
        presets={presets}
        selectedPreset={selectedPreset}
        onPresetChange={onPresetChange}
        onConfigChange={setConfig}
        onExport={onExport}
        stagedJsonPath={stagedJsonPath}
        onStagedJsonPathChange={setStagedJsonPath}
        onLoadStagedJson={onLoadStagedJson}
        loading={loading}
        layers={layers}
        onLayerToggle={onLayerToggle}
      />

      <main className="canvas-stage-wrap">
        <div className="stage-header hud-panel">
          <div className="status-pill">API: {health}</div>
          <div className={`status-pill mode-pill mode-${source}`}>Mode: {source}</div>
          {currentStage ? (
            <div className="stage-pill">
              <span>{currentStage.title_zh}</span>
              <small>{currentStage.title}</small>
            </div>
          ) : null}
          {error ? <div className="error-banner">{error}</div> : null}
        </div>

        <div className={`progress-panel hud-panel ${canStageClick ? 'progress-panel-has-stages' : ''}`}>
            <div className="progress-topline">
              <span className="progress-title">Sprawl Progress</span>
              <span className={`progress-status status-${displayGenerationProgress.status}`}>
                {displayGenerationProgress.status}
              </span>
              <span className="progress-percent">{Math.round((displayGenerationProgress.progress || 0) * 100)}%</span>
              {stageProgressInPrimaryBar ? (
                <span className={`timeline-auto-pill ${timeline.playing ? 'is-playing' : ''}`}>
                  {timeline.playing ? 'AUTO' : 'PAUSED'}
                </span>
              ) : null}
            </div>
            {stageProgressInPrimaryBar ? (
              <div className="timeline-step-rail" role="tablist" aria-label="Sprawl stage preview">
                {stages.map((stage, idx) => {
                  const localProgress = stageLocalProgress(stages, idx, timeline.currentTimeMs, timeline.totalMs);
                  const isActive = idx === timeline.currentStageIndex;
                  const isDone = idx < timeline.currentStageIndex || (idx === timeline.currentStageIndex && localProgress >= 0.999);
                  const isReached = idx <= timeline.currentStageIndex;
                  return (
                    <button
                      key={`${stage.stage_id}-${idx}`}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      className={[
                        'timeline-step-button',
                        isActive ? 'is-active' : '',
                        isDone ? 'is-done' : '',
                        isReached ? 'is-reached' : 'is-pending',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      onClick={() => timeline.selectStage(idx)}
                      title={`${idx + 1}. ${stage.title_zh} (${stage.title})`}
                    >
                      <div className="timeline-step-bead-row" aria-hidden="true">
                        <span className="timeline-step-bead" />
                        <span className="timeline-step-mini-track">
                          <span className="timeline-step-mini-fill" style={{ width: `${Math.round(localProgress * 100)}%` }} />
                        </span>
                      </div>
                      <div className="timeline-step-meta">
                        <span className="timeline-step-index">{idx + 1}</span>
                        <span className="timeline-step-label">{stage.title_zh}</span>
                        <span className="timeline-step-sub">{stage.title}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="backend-step-strip" aria-label="backend step progress">
                {backendStepStates.map((step) => (
                  step.id === 'start' ? (
                    <button
                      key={step.id}
                      type="button"
                      className={`backend-step-item backend-step-button is-${step.status} ${loading ? '' : 'is-clickable'}`}
                      title={loading ? `${step.titleZh} / ${step.title}` : `${step.titleZh} / ${step.title} (Click to generate)`}
                      onClick={onGenerate}
                      disabled={loading}
                    >
                      <div className="backend-step-bead-row" aria-hidden="true">
                        <span className="backend-step-bead" />
                        <span className="backend-step-mini-track">
                          <span className="backend-step-mini-fill" style={{ width: `${Math.round(step.localProgress * 100)}%` }} />
                        </span>
                      </div>
                      <div className="backend-step-labels">
                        <span className="backend-step-label-zh">{step.titleZh}</span>
                        <span className="backend-step-label-en">{step.title}</span>
                      </div>
                    </button>
                  ) : (
                    <div
                      key={step.id}
                      className={`backend-step-item is-${step.status}`}
                      title={`${step.titleZh} / ${step.title}`}
                    >
                      <div className="backend-step-bead-row" aria-hidden="true">
                        <span className="backend-step-bead" />
                        <span className="backend-step-mini-track">
                          <span className="backend-step-mini-fill" style={{ width: `${Math.round(step.localProgress * 100)}%` }} />
                        </span>
                      </div>
                      <div className="backend-step-labels">
                        <span className="backend-step-label-zh">{step.titleZh}</span>
                        <span className="backend-step-label-en">{step.title}</span>
                      </div>
                    </div>
                  )
                ))}
              </div>
            )}
            <div className="progress-message">
              <code>{progressPhaseLabel}</code>
              <span>{progressMessageLabel}</span>
            </div>
            {displayGenerationProgress.logs.length ? (
              <div className="progress-log-list">
                {displayGenerationProgress.logs.slice(-6).map((log) => (
                  <div key={`${log.seq}-${log.ts}`} className="progress-log-item">
                    <span className="progress-log-phase">{log.phase}</span>
                    <span className="progress-log-text">{log.message}</span>
                  </div>
                ))}
              </div>
            ) : null}

          </div>

        <div
          ref={wrapperRef}
          className="canvas-stage hud-frame"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <TerrainScene artifact={artifact} viewport={viewport} visible={USE_THREE_TERRAIN && layers.terrain && stageShowsTerrain} />
          <canvas ref={canvasRef} />

          {artifact ? (
            <svg className="overlay" onClick={handleOverlayClick}>
              {stageShowsHubs
                ? artifact.hubs.map((hub) => {
                    const rect = wrapperRef.current?.getBoundingClientRect();
                    if (!rect) return null;
                    const s = worldToScreen(hub.x, hub.y, extent, rect.width, rect.height, viewport);
                    const r = hub.tier === 1 ? 6 : hub.tier === 2 ? 4.5 : 3.5;
                    const isSelected = hub.id === selectedHubId;
                    return (
                      <g key={hub.id} transform={`translate(${s.x}, ${s.y})`}>
                        <circle
                          r={r + 3}
                          fill="rgba(0,0,0,0)"
                          stroke={hub.tier === 1 ? 'rgba(255, 200, 90, 0.55)' : 'rgba(92, 238, 255, 0.35)'}
                          strokeWidth={1}
                        />
                        <circle
                          r={r}
                          fill={hub.tier === 1 ? 'rgba(255, 196, 88, 0.96)' : hub.tier === 2 ? 'rgba(255, 124, 64, 0.9)' : 'rgba(92, 238, 255, 0.9)'}
                          stroke={isSelected ? 'rgba(255,255,255,0.95)' : 'rgba(10,18,28,0.95)'}
                          strokeWidth={isSelected ? 2 : 1}
                        />
                        {layers.labels && stageShowsLabels ? (
                          <text x={r + 5} y={-r - 2} className="hub-label">
                            {hub.name ?? hub.id}
                          </text>
                        ) : null}
                      </g>
                    );
                  })
                : null}
            </svg>
          ) : null}

          <CaptionsOverlay stage={currentStage} />
          {artifact ? <ScaleBar extent={extent} viewport={viewport} cssWidth={stageSize.width || (wrapperRef.current?.clientWidth ?? 0)} /> : null}
          {artifact ? <NorthArrow /> : null}
        </div>

      </main>

      <div className="side-stack">
        <StageInspector stage={currentStage} source={source} />
        <MetricsPanel artifact={artifact} selectedHub={selectedHub} />
      </div>
    </div>
  );
}
