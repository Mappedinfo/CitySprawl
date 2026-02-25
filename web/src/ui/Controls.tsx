import type { GenerateConfig, PresetsResponse } from '../types/city';

type Props = {
  config: GenerateConfig;
  presets: PresetsResponse;
  selectedPreset: string;
  onPresetChange: (name: string) => void;
  onConfigChange: (next: GenerateConfig) => void;
  onExport: () => void;
  stagedJsonPath: string;
  onStagedJsonPathChange: (next: string) => void;
  onLoadStagedJson: () => void;
  loading: boolean;
  layers: {
    terrain: boolean;
    rivers: boolean;
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
  onLayerToggle: (key: keyof Props['layers']) => void;
};

type LayerKey = keyof Props['layers'];
type LayerGroupId = 'field' | 'surface' | 'line' | 'point';
type LayerItemDef = {
  key: LayerKey;
  indent?: 0 | 1;
};
type LayerGroupDef = {
  id: LayerGroupId;
  label: string;
  items: LayerItemDef[];
};

const LAYER_LABELS: Record<LayerKey, string> = {
  terrain: 'Terrain',
  rivers: 'Rivers',
  majorRoads: 'Major Roads',
  localRoads: 'Local Roads',
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

const LAYER_GROUPS: LayerGroupDef[] = [
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

const GROUPED_LAYER_KEYS: LayerKey[] = LAYER_GROUPS.flatMap((group) => group.items.map((item) => item.key));
let layerConfigMismatchWarned = false;

function warnIfLayerGroupCoverageMismatch(layers: Props['layers']): void {
  if (!import.meta.env.DEV || layerConfigMismatchWarned) return;

  const actualKeys = Object.keys(layers) as LayerKey[];
  const groupedSet = new Set<LayerKey>();
  const duplicateKeys: LayerKey[] = [];
  for (const key of GROUPED_LAYER_KEYS) {
    if (groupedSet.has(key)) duplicateKeys.push(key);
    groupedSet.add(key);
  }

  const missingKeys = actualKeys.filter((key) => !groupedSet.has(key));
  const extraKeys = GROUPED_LAYER_KEYS.filter((key) => !actualKeys.includes(key));
  if (!missingKeys.length && !extraKeys.length && !duplicateKeys.length) return;

  layerConfigMismatchWarned = true;
  console.warn('[Controls] Layer UI groups are out of sync with layer state.', {
    missingKeys,
    extraKeys,
    duplicateKeys,
    actualKeys,
    groupedKeys: GROUPED_LAYER_KEYS,
  });
}

export function Controls({
  config,
  presets,
  selectedPreset,
  onPresetChange,
  onConfigChange,
  onExport,
  stagedJsonPath,
  onStagedJsonPathChange,
  onLoadStagedJson,
  loading,
  layers,
  onLayerToggle,
}: Props) {
  warnIfLayerGroupCoverageMismatch(layers);

  return (
    <aside className="panel controls-panel">
      <div className="panel-header">
        <h1>GeoAI Urban Sandbox</h1>
        <p>MVP: terrain classes + river areas + road hierarchy + parcel blocks</p>
      </div>

      <div className="section">
        <label>Preset</label>
        <select value={selectedPreset} onChange={(e) => onPresetChange(e.target.value)}>
          {Object.keys(presets).map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      </div>

      <div className="grid2">
        <div>
          <label>Seed</label>
          <input
            type="number"
            value={config.seed}
            onChange={(e) => onConfigChange({ ...config, seed: Number(e.target.value) || 0 })}
          />
        </div>
        <div>
          <label>Grid</label>
          <input
            type="number"
            value={config.grid_resolution}
            min={32}
            max={512}
            step={32}
            onChange={(e) => onConfigChange({ ...config, grid_resolution: Number(e.target.value) || 256 })}
          />
        </div>
        <div>
          <label>T2 hubs</label>
          <input
            type="number"
            value={config.hubs.t2_count}
            min={0}
            max={32}
            onChange={(e) =>
              onConfigChange({ ...config, hubs: { ...config.hubs, t2_count: Number(e.target.value) || 0 } })
            }
          />
        </div>
        <div>
          <label>T3 hubs</label>
          <input
            type="number"
            value={config.hubs.t3_count}
            min={0}
            max={128}
            onChange={(e) =>
              onConfigChange({ ...config, hubs: { ...config.hubs, t3_count: Number(e.target.value) || 0 } })
            }
          />
        </div>
        <div>
          <label>Hydro threshold</label>
          <input
            type="number"
            value={config.hydrology.accum_threshold}
            step={0.001}
            min={0.001}
            max={0.2}
            onChange={(e) =>
              onConfigChange({
                ...config,
                hydrology: { ...config.hydrology, accum_threshold: Number(e.target.value) || 0.01 },
              })
            }
          />
        </div>
        <div>
          <label>Loop budget</label>
          <input
            type="number"
            value={config.roads.loop_budget}
            min={0}
            max={12}
            onChange={(e) =>
              onConfigChange({ ...config, roads: { ...config.roads, loop_budget: Number(e.target.value) || 0 } })
            }
          />
        </div>
        <div>
          <label>Quality</label>
          <select
            value={config.quality.profile}
            onChange={(e) => {
              const profile = e.target.value;
              if (profile === 'preview') {
                onConfigChange({
                  ...config,
                  quality: { ...config.quality, profile, time_budget_ms: 5000 },
                  hydrology: { ...config.hydrology, primary_branch_count_max: 3 },
                  roads: {
                    ...config.roads,
                    collector_spacing_m: 520,
                    local_spacing_m: 170,
                    minor_bridge_budget: 2,
                  },
                });
                return;
              }
              if (profile === 'hq') {
                onConfigChange({
                  ...config,
                  quality: { ...config.quality, profile, time_budget_ms: 60000 },
                  hydrology: { ...config.hydrology, primary_branch_count_max: 5 },
                  roads: {
                    ...config.roads,
                    collector_spacing_m: 320,
                    local_spacing_m: 95,
                    minor_bridge_budget: 8,
                  },
                });
                return;
              }
              onConfigChange({
                ...config,
                quality: { ...config.quality, profile, time_budget_ms: 15000 },
                hydrology: { ...config.hydrology, primary_branch_count_max: 4 },
                roads: {
                  ...config.roads,
                  collector_spacing_m: 420,
                  local_spacing_m: 130,
                  minor_bridge_budget: 4,
                },
              });
            }}
          >
            <option value="preview">preview</option>
            <option value="balanced">balanced</option>
            <option value="hq">hq</option>
          </select>
        </div>
        <div>
          <label>Road style</label>
          <select
            value={config.roads.style}
            onChange={(e) => onConfigChange({ ...config, roads: { ...config.roads, style: e.target.value } })}
          >
            <option value="mixed_organic">mixed_organic</option>
            <option value="grid">grid</option>
            <option value="organic">organic</option>
            <option value="skeleton">skeleton</option>
          </select>
        </div>
        <div>
          <label>Collector spacing</label>
          <input
            type="number"
            value={config.roads.collector_spacing_m}
            min={80}
            max={1000}
            step={10}
            onChange={(e) =>
              onConfigChange({ ...config, roads: { ...config.roads, collector_spacing_m: Number(e.target.value) || 420 } })
            }
          />
        </div>
        <div>
          <label>Local spacing</label>
          <input
            type="number"
            value={config.roads.local_spacing_m}
            min={30}
            max={400}
            step={5}
            onChange={(e) =>
              onConfigChange({ ...config, roads: { ...config.roads, local_spacing_m: Number(e.target.value) || 130 } })
            }
          />
        </div>
      </div>

      <div className="section">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={config.hydrology.enable}
            onChange={(e) =>
              onConfigChange({ ...config, hydrology: { ...config.hydrology, enable: e.target.checked } })
            }
          />
          <span>Enable hydrology</span>
        </label>
      </div>

      <div className="section">
        <div className="section-title">Layers</div>
        <div className="layer-groups">
          {LAYER_GROUPS.map((group) => (
            <div key={group.id} className="layer-group" data-layer-group={group.id}>
              <div className="layer-group-title">{group.label}</div>
              <div className="layer-group-body">
                {group.items.map(({ key, indent = 0 }) => (
                  <label
                    key={key}
                    className={`checkbox-row compact layer-item${indent ? ' is-child' : ''}`}
                    data-layer-key={key}
                  >
                    <input type="checkbox" checked={layers[key]} onChange={() => onLayerToggle(key)} />
                    <span className="layer-item-label">{LAYER_LABELS[key]}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="button-row">
        <button onClick={onExport}>Export JSON</button>
      </div>

      <div className="section">
        <label>Load staged JSON (backend file path)</label>
        <input
          type="text"
          value={stagedJsonPath}
          placeholder="/absolute/path/to/citygen-staged.json"
          onChange={(e) => onStagedJsonPathChange(e.target.value)}
        />
        <div className="button-row single-row">
          <button onClick={onLoadStagedJson} disabled={loading || !stagedJsonPath.trim()}>
            Load JSON (No Regenerate)
          </button>
        </div>
      </div>

      <div className="hint">Pan: drag canvas. Zoom: wheel. Click hub for attributes.</div>
    </aside>
  );
}
