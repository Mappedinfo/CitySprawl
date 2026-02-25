import type { GenerateConfig, PresetsResponse } from '../types/city';
import {
  ALL_LAYER_KEYS,
  LAYER_GROUPS,
  LAYER_LABELS,
  LAYER_LEGEND_SPECS,
  type LayerGroupId,
  type LayerKey,
  type LayerUiState,
} from './layerCatalog';

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
  layerUiState?: LayerUiState;
  onLayerToggle: (key: keyof Props['layers']) => void;
};
let layerConfigMismatchWarned = false;

function warnIfLayerGroupCoverageMismatch(layers: Props['layers']): void {
  if (!import.meta.env.DEV || layerConfigMismatchWarned) return;

  const actualKeys = Object.keys(layers) as LayerKey[];
  const groupedSet = new Set<LayerKey>();
  const duplicateKeys: LayerKey[] = [];
  for (const key of ALL_LAYER_KEYS) {
    if (groupedSet.has(key)) duplicateKeys.push(key);
    groupedSet.add(key);
  }

  const missingKeys = actualKeys.filter((key) => !groupedSet.has(key));
  const extraKeys = ALL_LAYER_KEYS.filter((key) => !actualKeys.includes(key));
  if (!missingKeys.length && !extraKeys.length && !duplicateKeys.length) return;

  layerConfigMismatchWarned = true;
  console.warn('[Controls] Layer UI groups are out of sync with layer state.', {
    missingKeys,
    extraKeys,
    duplicateKeys,
    actualKeys,
    groupedKeys: ALL_LAYER_KEYS,
  });
}

function LegendGlyph({ layerKey }: { layerKey: LayerKey }) {
  const spec = LAYER_LEGEND_SPECS[layerKey];
  switch (spec.kind) {
    case 'terrain':
      return (
        <span className="legend-glyph legend-terrain" aria-hidden="true">
          <span className="legend-terrain-band band-a" />
          <span className="legend-terrain-band band-b" />
        </span>
      );
    case 'contours':
      return (
        <span className="legend-glyph legend-contours" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      );
    case 'analysis':
      return <span className="legend-glyph legend-analysis" aria-hidden="true" />;
    case 'rivers':
      return (
        <span className="legend-glyph legend-rivers" aria-hidden="true">
          <span className="legend-river-line" />
        </span>
      );
    case 'polygon':
      return (
        <span className={`legend-glyph legend-polygon legend-polygon-${layerKey}`} aria-hidden="true">
          <span className="legend-polygon-inner" />
        </span>
      );
    case 'buildings':
      return (
        <span className="legend-glyph legend-buildings" aria-hidden="true">
          <span className="legend-bldg b1" />
          <span className="legend-bldg b2" />
          <span className="legend-bldg b3" />
        </span>
      );
    case 'green':
      return (
        <span className="legend-glyph legend-green" aria-hidden="true">
          <span className="legend-green-leaf" />
        </span>
      );
    case 'majorRoads':
      return (
        <span className="legend-glyph legend-major-roads" aria-hidden="true">
          <span className="legend-road-major line-a" />
          <span className="legend-road-major line-b" />
        </span>
      );
    case 'localRoads':
      return (
        <span className="legend-glyph legend-local-roads" aria-hidden="true">
          <span className="legend-road-local" />
        </span>
      );
    case 'pedPath':
      return (
        <span className="legend-glyph legend-ped-path" aria-hidden="true">
          <span className="legend-ped-line" />
        </span>
      );
    case 'candidate':
      return (
        <span className="legend-glyph legend-candidate" aria-hidden="true">
          <span className="legend-candidate-line" />
        </span>
      );
    case 'traffic':
      return (
        <span className="legend-glyph legend-traffic" aria-hidden="true">
          <span className="legend-traffic-line" />
        </span>
      );
    case 'resources':
      return (
        <span className="legend-glyph legend-resources" aria-hidden="true">
          <span className="legend-dot dot-a" />
          <span className="legend-dot dot-b" />
          <span className="legend-dot dot-c" />
        </span>
      );
    case 'labels':
      return (
        <span className="legend-glyph legend-labels" aria-hidden="true">
          <span className="legend-label-dot" />
          <span className="legend-label-text">A</span>
        </span>
      );
    default:
      return <span className="legend-glyph" aria-hidden="true" />;
  }
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
  layerUiState,
  onLayerToggle,
}: Props) {
  warnIfLayerGroupCoverageMismatch(layers);
  const isGenerationPhase = Boolean(layerUiState?.isGenerationPhase);
  const reachedSet = new Set(layerUiState?.reachedLayerKeys ?? []);
  const activeSet = new Set(layerUiState?.activeLayerKeys ?? []);
  const activeGroupSet = new Set<LayerGroupId>(layerUiState?.activeGroupIds ?? []);

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
              <div className={`layer-group-title${activeGroupSet.has(group.id) ? ' is-active-group' : ''}`}>{group.label}</div>
              <div className="layer-group-body">
                {group.items.map(({ key, indent = 0 }) => (
                  (() => {
                    const isReached = !isGenerationPhase || reachedSet.has(key);
                    const isActiveGenerating = isGenerationPhase && activeSet.has(key);
                    const rowClass = [
                      'checkbox-row',
                      'compact',
                      'layer-item',
                      indent ? 'is-child' : '',
                      isGenerationPhase && isReached ? 'is-reached' : '',
                      isGenerationPhase && !isReached ? 'is-unreached' : '',
                      isActiveGenerating ? 'is-active-generating' : '',
                    ]
                      .filter(Boolean)
                      .join(' ');
                    return (
                      <label key={key} className={rowClass} data-layer-key={key}>
                        <input
                          type="checkbox"
                          checked={layers[key]}
                          disabled={!isReached}
                          onChange={() => onLayerToggle(key)}
                        />
                        <span className="layer-item-main">
                          <span className="layer-item-label">{LAYER_LABELS[key]}</span>
                          <span className="layer-item-legend">
                            <LegendGlyph layerKey={key} />
                          </span>
                        </span>
                      </label>
                    );
                  })()
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
