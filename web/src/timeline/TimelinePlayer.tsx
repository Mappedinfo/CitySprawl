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

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const ss = String(s % 60).padStart(2, '0');
  return `${m}:${ss}`;
}

export function TimelinePlayer({
  stages,
  currentStageIndex,
  currentTimeMs,
  totalMs,
  playing,
  onTogglePlay,
  onSeek,
  onSelectStage,
}: Props) {
  return (
    <div className="hud-panel timeline-panel">
      <div className="timeline-top">
        <button className="hud-btn" onClick={onTogglePlay} type="button">
          {playing ? 'Pause' : 'Play'}
        </button>
        <div className="timeline-readout">
          <span>{fmt(currentTimeMs)}</span>
          <span>/</span>
          <span>{fmt(totalMs)}</span>
        </div>
      </div>

      <input
        className="timeline-slider"
        type="range"
        min={0}
        max={totalMs}
        step={10}
        value={Math.round(currentTimeMs)}
        onChange={(e) => onSeek(Number(e.target.value))}
      />

      <div className="timeline-stages">
        {stages.map((stage, idx) => (
          <button
            key={`${stage.stage_id}-${idx}`}
            type="button"
            className={`stage-chip ${idx === currentStageIndex ? 'is-active' : ''}`}
            onClick={() => onSelectStage(idx)}
            title={stage.title}
          >
            <span className="chip-index">{idx + 1}</span>
            <span className="chip-label">{stage.title_zh}</span>
            <span className="chip-sub">{stage.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
