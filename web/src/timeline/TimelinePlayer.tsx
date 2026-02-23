import type { StageArtifact } from '../types/city';

type Props = {
  stages: StageArtifact[];
  currentStageIndex: number;
  currentTimeMs: number;
  totalMs: number;
  playing: boolean;
  onTogglePlay: () => void;
  onSeek: (ms: number) => void;
  onSelectStage: (idx: number) => void;
};

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function stageProgress(stages: StageArtifact[], idx: number, currentTimeMs: number, totalMs: number): number {
  const start = stages[idx]?.timestamp_ms ?? 0;
  const end = idx < stages.length - 1 ? (stages[idx + 1]?.timestamp_ms ?? totalMs) : totalMs;
  if (currentTimeMs <= start) return 0;
  if (currentTimeMs >= end) return 1;
  return clamp01((currentTimeMs - start) / Math.max(end - start, 1));
}

export function TimelinePlayer({
  stages,
  currentStageIndex,
  currentTimeMs,
  totalMs,
  playing,
  onSelectStage,
}: Props) {
  if (!stages.length) return null;

  return (
    <div className="hud-panel timeline-panel">
      <div className="timeline-top timeline-top-compact">
        <div className="timeline-title-stack">
          <span className="timeline-title">Growth Steps</span>
          <span className="timeline-subtitle">{playing ? 'Auto-growing' : 'Click a step to inspect'}</span>
        </div>
        <div className={`timeline-auto-pill ${playing ? 'is-playing' : ''}`}>{playing ? 'AUTO' : 'PAUSED'}</div>
      </div>

      <div className="timeline-step-rail" role="tablist" aria-label="City growth steps">
        {stages.map((stage, idx) => {
          const localProgress = stageProgress(stages, idx, currentTimeMs, totalMs);
          const isActive = idx === currentStageIndex;
          const isDone = idx < currentStageIndex || (idx === currentStageIndex && localProgress >= 0.999);
          const isReached = idx <= currentStageIndex;
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
              onClick={() => onSelectStage(idx)}
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
    </div>
  );
}
