import type { LayerToggles } from '../render/stageRenderer';
import { UNIFIED_STAGE_DEFS, canonicalizePhaseId, type CanonicalStageId } from '../timeline/unifiedStages';

export type LayerKey = keyof LayerToggles;
export type LayerGroupId = 'field' | 'surface' | 'line' | 'point';

export type LayerLegendKind =
  | 'terrain'
  | 'contours'
  | 'analysis'
  | 'rivers'
  | 'polygon'
  | 'buildings'
  | 'green'
  | 'majorRoads'
  | 'localRoads'
  | 'pedPath'
  | 'candidate'
  | 'traffic'
  | 'resources'
  | 'labels';

export type LayerLegendSpec = {
  kind: LayerLegendKind;
};

export type LayerItemDef = {
  key: LayerKey;
  indent?: 0 | 1;
};

export type LayerGroupDef = {
  id: LayerGroupId;
  label: string;
  items: LayerItemDef[];
};

export type LayerUiState = {
  isGenerationPhase: boolean;
  reachedLayerKeys: LayerKey[];
  activeLayerKeys: LayerKey[];
  activeGroupIds: LayerGroupId[];
};

export const LAYER_LABELS: Record<LayerKey, string> = {
  terrain: 'Terrain',
  rivers: 'Rivers',
  majorRoads: 'Major Roads',
  localRoads: 'Minor Local Roads',
  contours: 'Contours',
  blocks: 'Blocks',
  parcels: 'Parcels',
  pedestrianPaths: 'Ped Paths',
  debugCandidates: 'Candidate Edges',
  labels: 'Labels',
  analysis: 'Analysis Heatmaps',
  resources: 'Resource Sites',
  traffic: 'Traffic Heat',
  buildings: 'Buildings',
  greenZones: 'Green Zones',
};

export const LAYER_LEGEND_SPECS: Record<LayerKey, LayerLegendSpec> = {
  terrain: { kind: 'terrain' },
  rivers: { kind: 'rivers' },
  majorRoads: { kind: 'majorRoads' },
  localRoads: { kind: 'localRoads' },
  contours: { kind: 'contours' },
  blocks: { kind: 'polygon' },
  parcels: { kind: 'polygon' },
  pedestrianPaths: { kind: 'pedPath' },
  debugCandidates: { kind: 'candidate' },
  labels: { kind: 'labels' },
  analysis: { kind: 'analysis' },
  resources: { kind: 'resources' },
  traffic: { kind: 'traffic' },
  buildings: { kind: 'buildings' },
  greenZones: { kind: 'green' },
};

export const LAYER_GROUPS: LayerGroupDef[] = [
  {
    id: 'field',
    label: 'Field / 场',
    items: [{ key: 'terrain' }, { key: 'contours' }, { key: 'analysis' }],
  },
  {
    id: 'surface',
    label: 'Surface / 面',
    items: [{ key: 'rivers' }, { key: 'blocks' }, { key: 'parcels' }, { key: 'buildings' }, { key: 'greenZones' }],
  },
  {
    id: 'line',
    label: 'Line / 线',
    items: [
      { key: 'majorRoads' },
      { key: 'localRoads' },
      { key: 'pedestrianPaths' },
      { key: 'debugCandidates' },
      { key: 'traffic' },
    ],
  },
  {
    id: 'point',
    label: 'Point / 点',
    items: [{ key: 'resources' }, { key: 'labels' }],
  },
];

export const ALL_LAYER_KEYS: LayerKey[] = LAYER_GROUPS.flatMap((group) => group.items.map((item) => item.key));

export const LAYER_GROUP_ID_BY_KEY: Record<LayerKey, LayerGroupId> = (() => {
  const out = {} as Record<LayerKey, LayerGroupId>;
  for (const group of LAYER_GROUPS) {
    for (const item of group.items) out[item.key] = group.id;
  }
  return out;
})();

export const CANONICAL_STAGE_TO_UI_LAYER_KEYS: Record<CanonicalStageId, LayerKey[]> = {
  start: ['terrain'],
  terrain: ['terrain', 'contours'],
  rivers: ['rivers'],
  hubs: ['labels'],
  roads_arterial: ['majorRoads', 'debugCandidates'],
  roads_collector: ['majorRoads', 'debugCandidates'],
  roads_local: ['majorRoads', 'localRoads', 'debugCandidates'],
  artifact: [],
  analysis: ['analysis', 'resources'],
  traffic: ['traffic', 'majorRoads', 'localRoads'],
  buildings: ['buildings', 'greenZones'],
  parcels: ['blocks', 'parcels', 'pedestrianPaths'],
  stages: [],
  done: [],
};

function pushUnique<T>(arr: T[], value: T): void {
  if (!arr.includes(value)) arr.push(value);
}

function resolveCanonicalStageForLayerUi(params: {
  phase?: string | null;
  status?: string | null;
  progress?: number | null;
  isGenerationPhase: boolean;
}): CanonicalStageId | null {
  const phaseLike = String(params.phase || params.status || '').trim();
  const canonical = canonicalizePhaseId(phaseLike);
  const known = UNIFIED_STAGE_DEFS.find((s) => s.id === canonical);
  if (known) return known.id;

  const progress = Number.isFinite(params.progress) ? Number(params.progress) : NaN;
  if (Number.isFinite(progress)) {
    let idx = 0;
    for (let i = 0; i < UNIFIED_STAGE_DEFS.length; i += 1) {
      if (progress >= (UNIFIED_STAGE_DEFS[i]?.anchor ?? 0)) idx = i;
      else break;
    }
    return UNIFIED_STAGE_DEFS[idx]?.id ?? 'start';
  }

  if (params.isGenerationPhase) return 'start';
  return null;
}

export function computeLayerUiStateFromGenerationPhase(params: {
  isGenerationPhase: boolean;
  phase?: string | null;
  status?: string | null;
  progress?: number | null;
}): LayerUiState {
  if (!params.isGenerationPhase) {
    return {
      isGenerationPhase: false,
      reachedLayerKeys: [...ALL_LAYER_KEYS],
      activeLayerKeys: [],
      activeGroupIds: [],
    };
  }

  const stageId = resolveCanonicalStageForLayerUi(params);
  const stageIdx = stageId ? UNIFIED_STAGE_DEFS.findIndex((s) => s.id === stageId) : -1;
  const reachedLayerKeys: LayerKey[] = [];
  const activeLayerKeys: LayerKey[] = [];

  for (let i = 0; i <= stageIdx; i += 1) {
    const sid = UNIFIED_STAGE_DEFS[i]?.id;
    if (!sid) continue;
    for (const key of CANONICAL_STAGE_TO_UI_LAYER_KEYS[sid]) pushUnique(reachedLayerKeys, key);
  }
  if (stageId) {
    for (const key of CANONICAL_STAGE_TO_UI_LAYER_KEYS[stageId]) pushUnique(activeLayerKeys, key);
  }

  const activeGroupIds: LayerGroupId[] = [];
  for (const key of activeLayerKeys) {
    const groupId = LAYER_GROUP_ID_BY_KEY[key];
    if (groupId) pushUnique(activeGroupIds, groupId);
  }

  return {
    isGenerationPhase: true,
    reachedLayerKeys,
    activeLayerKeys,
    activeGroupIds,
  };
}

