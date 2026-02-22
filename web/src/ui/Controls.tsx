import type { GenerateConfig, PresetsResponse } from '../types/city';

type Props = {
  config: GenerateConfig;
  presets: PresetsResponse;
  selectedPreset: string;
  onPresetChange: (name: string) => void;
  onConfigChange: (next: GenerateConfig) => void;
  onGenerate: () => void;
  onExport: () => void;
  loading: boolean;
  layers: {
    terrain: boolean;
    rivers: boolean;
    roads: boolean;
    debugCandidates: boolean;
    labels: boolean;
  };
  onLayerToggle: (key: keyof Props['layers']) => void;
};

export function Controls({
  config,
  presets,
  selectedPreset,
  onPresetChange,
  onConfigChange,
  onGenerate,
  onExport,
  loading,
  layers,
  onLayerToggle,
}: Props) {
  return (
    <aside className="panel controls-panel">
      <div className="panel-header">
        <h1>GeoAI Urban Sandbox</h1>
        <p>MVP: deterministic terrain + rivers + hubs + road skeleton</p>
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
        {Object.entries(layers).map(([key, value]) => (
          <label key={key} className="checkbox-row compact">
            <input type="checkbox" checked={value} onChange={() => onLayerToggle(key as keyof Props['layers'])} />
            <span>{key}</span>
          </label>
        ))}
      </div>

      <div className="button-row">
        <button onClick={onGenerate} disabled={loading} className="primary">
          {loading ? 'Generating...' : 'Generate'}
        </button>
        <button onClick={onExport}>Export JSON</button>
      </div>

      <div className="hint">Pan: drag canvas. Zoom: wheel. Click hub for attributes.</div>
    </aside>
  );
}
