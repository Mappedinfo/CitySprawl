import type { StageArtifact } from '../types/city';

export function StageInspector({ stage, source }: { stage: StageArtifact | null; source: 'v2' | 'staged' | 'fallback' | 'none' }) {
  return (
    <aside className="hud-panel stage-inspector">
      <div className="hud-title-row">
        <h2>Stage</h2>
        <span className={`source-pill source-${source}`}>{source}</span>
      </div>
      {!stage ? (
        <p className="muted">No staged data.</p>
      ) : (
        <>
          <div className="stage-name-stack">
            <div className="stage-name-zh">{stage.title_zh}</div>
            <div className="stage-name-en">{stage.title}</div>
          </div>
          <div className="stage-subcopy">{stage.subtitle_zh}</div>
          <div className="stage-subcopy stage-subcopy-en">{stage.subtitle}</div>
          <div className="stage-visible-list">
            {stage.visible_layers.map((item) => (
              <span key={item} className="stage-token">
                {item}
              </span>
            ))}
          </div>
          <div className="metrics-list stage-metrics-list">
            {Object.entries(stage.metrics).map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <strong>{String(v)}</strong>
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}
