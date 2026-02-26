import type { CityArtifact, HubRecord } from '../types/city';

export function MetricsPanel({ artifact, selectedHub }: { artifact: CityArtifact | null; selectedHub: HubRecord | null }) {
  const hasMetrics = artifact && typeof artifact.metrics.connectivity_ratio === 'number';
  return (
    <aside className="panel metrics-panel">
      <h2>Metrics</h2>
      {!hasMetrics ? (
        <p className="muted">No artifact yet.</p>
      ) : (
        <div className="metrics-list">
          <div><span>Seed</span><strong>{artifact.meta.seed}</strong></div>
          <div><span>Duration</span><strong>{artifact.meta.duration_ms.toFixed(1)} ms</strong></div>
          <div><span>Connected</span><strong>{artifact.metrics.connected ? 'Yes' : 'No'}</strong></div>
          <div><span>Connectivity</span><strong>{artifact.metrics.connectivity_ratio.toFixed(2)}</strong></div>
          <div><span>Road edges</span><strong>{artifact.metrics.road_edge_count}</strong></div>
          <div><span>Dead ends</span><strong>{artifact.metrics.dead_end_count}</strong></div>
          <div><span>Bridges</span><strong>{artifact.metrics.bridge_count}</strong></div>
          <div><span>Illegal X</span><strong>{artifact.metrics.illegal_intersection_count}</strong></div>
          <div><span>Rivers</span><strong>{artifact.metrics.river_count}</strong></div>
        </div>
      )}

      <h2>Selection</h2>
      {!selectedHub ? (
        <p className="muted">Click a hub to inspect.</p>
      ) : (
        <div className="selection-card">
          <div className="sel-name">{selectedHub.name ?? selectedHub.id}</div>
          <div>Tier {selectedHub.tier}</div>
          <div>Score {selectedHub.score.toFixed(3)}</div>
          <div>River distance {selectedHub.attrs.river_distance_m?.toFixed?.(1) ?? 'n/a'} m</div>
          <div>Slope score {selectedHub.attrs.slope_score?.toFixed?.(2) ?? 'n/a'}</div>
        </div>
      )}
    </aside>
  );
}
