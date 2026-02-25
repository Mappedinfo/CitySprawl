import type { StageArtifact } from '../types/city';

export type CanonicalStageId =
  | 'start'
  | 'terrain'
  | 'rivers'
  | 'hubs'
  | 'roads_arterial'
  | 'roads_collector'
  | 'roads_local'
  | 'artifact'
  | 'analysis'
  | 'traffic'
  | 'buildings'
  | 'parcels'
  | 'stages'
  | 'done';

export type UnifiedStageDef = {
  id: CanonicalStageId;
  title: string;
  titleZh: string;
  subtitle: string;
  subtitleZh: string;
  anchor: number;
  timestampMs: number;
  visibleLayers: string[];
};

export const UNIFIED_STAGE_DEFS: UnifiedStageDef[] = [
  {
    id: 'start',
    title: 'Start',
    titleZh: '启动',
    subtitle: 'Initializing generation pipeline and base scene',
    subtitleZh: '初始化生成流程与基础场景',
    anchor: 0.0,
    timestampMs: 0,
    visibleLayers: ['terrain'],
  },
  {
    id: 'terrain',
    title: 'Terrain',
    titleZh: '地形',
    subtitle: 'Generating terrain elevation and contour baseline',
    subtitleZh: '生成地形高程与等高线基底',
    anchor: 0.02,
    timestampMs: 400,
    visibleLayers: ['terrain', 'contours'],
  },
  {
    id: 'rivers',
    title: 'Rivers',
    titleZh: '河流',
    subtitle: 'Selecting river courses and shaping river areas',
    subtitleZh: '选择河道并构建河流区域',
    anchor: 0.16,
    timestampMs: 3200,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours'],
  },
  {
    id: 'hubs',
    title: 'Hubs',
    titleZh: '中心点',
    subtitle: 'Placing urban hubs and hierarchy centers',
    subtitleZh: '布设城市中心点与层级中心',
    anchor: 0.28,
    timestampMs: 5600,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'hubs', 'labels'],
  },
  {
    id: 'roads_arterial',
    title: 'Arterial Roads',
    titleZh: '主干道',
    subtitle: 'Generating arterial backbone from hub network',
    subtitleZh: '基于中心点网络生成主干道骨架',
    anchor: 0.36,
    timestampMs: 7200,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels'],
  },
  {
    id: 'roads_collector',
    title: 'Collector Roads',
    titleZh: '次干道',
    subtitle: 'Growing collector network from arterial seeds',
    subtitleZh: '从主干道种子点发散生成次干道网络',
    anchor: 0.46,
    timestampMs: 9200,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels'],
  },
  {
    id: 'roads_local',
    title: 'Local Roads',
    titleZh: '本地道路',
    subtitle: 'Filling blocks with local street network',
    subtitleZh: '在街区内填充本地道路网络',
    anchor: 0.56,
    timestampMs: 11200,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels'],
  },
  {
    id: 'artifact',
    title: 'Artifact',
    titleZh: '骨架封装',
    subtitle: 'Packaging core city artifact and previews',
    subtitleZh: '封装城市骨架产物与预览数据',
    anchor: 0.68,
    timestampMs: 13_600,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels'],
  },
  {
    id: 'analysis',
    title: 'Analysis',
    titleZh: '分析',
    subtitle: 'Computing suitability, flood risk and resources',
    subtitleZh: '计算宜居性、洪涝风险与资源分布',
    anchor: 0.72,
    timestampMs: 14_400,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'analysis_heatmaps', 'resources'],
  },
  {
    id: 'traffic',
    title: 'Traffic',
    titleZh: '交通模拟',
    subtitle: 'Assigning OD flows and congestion preview',
    subtitleZh: 'OD流量分配与拥堵预览',
    anchor: 0.78,
    timestampMs: 15_600,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'traffic_heat'],
  },
  {
    id: 'buildings',
    title: 'Buildings',
    titleZh: '建筑预览',
    subtitle: 'Generating building footprints and green zones',
    subtitleZh: '生成建筑轮廓与绿地区域预览',
    anchor: 0.82,
    timestampMs: 16_400,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels', 'buildings', 'green_zones'],
  },
  {
    id: 'parcels',
    title: 'Parcels',
    titleZh: '地块/宗地',
    subtitle: 'Extracting blocks, parcels and pedestrian paths',
    subtitleZh: '提取街区、宗地与步行路径',
    anchor: 0.9,
    timestampMs: 18_000,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels', 'pedestrian_paths', 'blocks', 'parcels'],
  },
  {
    id: 'stages',
    title: 'Stages',
    titleZh: '阶段组装',
    subtitle: 'Assembling timeline stages and composite preview',
    subtitleZh: '组装阶段快照与合成预览',
    anchor: 0.96,
    timestampMs: 19_200,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels', 'pedestrian_paths', 'blocks', 'parcels', 'buildings', 'green_zones'],
  },
  {
    id: 'done',
    title: 'Done',
    titleZh: '完成',
    subtitle: 'Generation complete with final city preview',
    subtitleZh: '生成完成并输出最终城市预览',
    anchor: 1.0,
    timestampMs: 20_000,
    visibleLayers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels', 'pedestrian_paths', 'blocks', 'parcels', 'buildings', 'green_zones'],
  },
];

export const UNIFIED_STAGE_IDS = UNIFIED_STAGE_DEFS.map((stage) => stage.id);
const UNIFIED_STAGE_DEF_BY_ID = new Map(UNIFIED_STAGE_DEFS.map((stage) => [stage.id, stage] as const));

export const PHASE_ALIASES: Record<string, CanonicalStageId> = {
  connecting: 'start',
  queued: 'start',
  running: 'start',
  completed: 'done',
  complete: 'done',
  failed: 'done',
  terrain_visuals: 'artifact',
  naming: 'artifact',
  core_complete: 'artifact',
  analysis_complete: 'parcels',
  // Map backend road phase names and legacy 'roads' stage to new sub-stages
  roads: 'roads_arterial',
  'roads.candidate_graph': 'roads_arterial',
  'roads.backbone': 'roads_arterial',
  'roads.branches': 'roads_arterial',
  'roads.snap': 'roads_arterial',
  'roads.route_initial': 'roads_arterial',
  'roads_arterial.intersections': 'roads_arterial',
  'roads_collector.generation': 'roads_collector',
  'roads_collector.intersections': 'roads_collector',
  'roads_collector.freeze': 'roads_collector',
  'roads_local.generation': 'roads_local',
  'roads_local.intersections': 'roads_local',
  'roads.syntax': 'roads_local',
  'roads.route_final': 'roads_local',
  'roads.street_runs': 'roads_local',
  'roads.done': 'roads_local',
};

type LegacyStageId = 'terrain' | 'analysis' | 'infrastructure' | 'traffic' | 'final_preview';

const LEGACY_STAGE_EXPANSION: Record<CanonicalStageId, LegacyStageId> = {
  start: 'terrain',
  terrain: 'terrain',
  rivers: 'terrain',
  hubs: 'infrastructure',
  roads_arterial: 'infrastructure',
  roads_collector: 'infrastructure',
  roads_local: 'infrastructure',
  artifact: 'infrastructure',
  analysis: 'analysis',
  traffic: 'traffic',
  buildings: 'final_preview',
  parcels: 'final_preview',
  stages: 'final_preview',
  done: 'final_preview',
};

function cloneStageForUi(source: StageArtifact, def: UnifiedStageDef): StageArtifact {
  return {
    ...source,
    stage_id: def.id,
    title: def.title,
    title_zh: def.titleZh,
    subtitle: def.subtitle,
    subtitle_zh: def.subtitleZh,
    timestamp_ms: def.timestampMs,
    visible_layers: [...def.visibleLayers],
    caption: { text: def.subtitle, text_zh: def.subtitleZh },
    metrics: { ...source.metrics },
    layers: { ...source.layers },
  };
}

function isLegacyFiveStageSet(stageIds: string[]): boolean {
  if (stageIds.length !== 5) return false;
  const set = new Set(stageIds);
  return ['terrain', 'analysis', 'infrastructure', 'traffic', 'final_preview'].every((id) => set.has(id));
}

export function canonicalizePhaseId(phase: string): string {
  const normalized = (phase || '').trim().toLowerCase();
  return PHASE_ALIASES[normalized] ?? normalized;
}

export function getInitialPreviewStageTimestampMs(phaseLike: string | null | undefined): number {
  const canonical = canonicalizePhaseId(String(phaseLike ?? ''));
  const def = UNIFIED_STAGE_DEF_BY_ID.get(canonical as CanonicalStageId) ?? UNIFIED_STAGE_DEF_BY_ID.get('done');
  return def?.timestampMs ?? (UNIFIED_STAGE_DEFS[UNIFIED_STAGE_DEFS.length - 1]?.timestampMs ?? 20_000);
}

export function normalizeStagesForUi(stages: StageArtifact[]): StageArtifact[] {
  if (!stages.length) return stages;
  const stageIds = stages.map((s) => String(s.stage_id));

  if (isLegacyFiveStageSet(stageIds)) {
    const byLegacyId = new Map(stages.map((s) => [s.stage_id as LegacyStageId, s] as const));
    return UNIFIED_STAGE_DEFS.map((def) => {
      const source = byLegacyId.get(LEGACY_STAGE_EXPANSION[def.id]);
      return source ? cloneStageForUi(source, def) : ({
        stage_id: def.id,
        title: def.title,
        title_zh: def.titleZh,
        subtitle: def.subtitle,
        subtitle_zh: def.subtitleZh,
        timestamp_ms: def.timestampMs,
        visible_layers: [...def.visibleLayers],
        metrics: {},
        caption: { text: def.subtitle, text_zh: def.subtitleZh },
        layers: {},
      } as StageArtifact);
    });
  }

  const byCanonicalId = new Map<string, StageArtifact>();
  const unknown: StageArtifact[] = [];
  for (const stage of stages) {
    const canonical = canonicalizePhaseId(stage.stage_id);
    const def = UNIFIED_STAGE_DEF_BY_ID.get(canonical as CanonicalStageId);
    if (def) {
      byCanonicalId.set(def.id, cloneStageForUi({ ...stage, stage_id: def.id }, def));
    } else {
      unknown.push(stage);
    }
  }

  const orderedCanonical = UNIFIED_STAGE_DEFS
    .filter((def) => byCanonicalId.has(def.id))
    .map((def) => byCanonicalId.get(def.id)!)
    .filter(Boolean);

  if (!orderedCanonical.length) return stages;
  return [...orderedCanonical, ...unknown];
}
