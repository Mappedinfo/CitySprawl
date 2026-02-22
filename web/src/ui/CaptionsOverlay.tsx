import type { StageArtifact } from '../types/city';

export function CaptionsOverlay({ stage }: { stage: StageArtifact | null }) {
  if (!stage) return null;
  return (
    <div className="captions-overlay" aria-live="polite">
      <div className="caption-title-zh">{stage.title_zh}</div>
      <div className="caption-title-en">{stage.title}</div>
      <div className="caption-sub-zh">{stage.subtitle_zh}</div>
      <div className="caption-sub-en">{stage.subtitle}</div>
    </div>
  );
}
