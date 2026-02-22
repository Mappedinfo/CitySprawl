import type { Viewport } from '../render/viewport';

type Props = {
  extent: number;
  viewport: Viewport;
  cssWidth: number;
};

function niceScaleMeters(targetMeters: number): number {
  if (!Number.isFinite(targetMeters) || targetMeters <= 0) return 100;
  const exp = Math.floor(Math.log10(targetMeters));
  const base = 10 ** exp;
  const normalized = targetMeters / base;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * base;
}

function formatMeters(meters: number): string {
  if (meters >= 1000) {
    const km = meters / 1000;
    const text = Number.isInteger(km) ? `${km}` : km.toFixed(1);
    return `${text} km`;
  }
  return `${Math.round(meters)} m`;
}

export function ScaleBar({ extent, viewport, cssWidth }: Props) {
  if (!cssWidth || cssWidth <= 0 || extent <= 0) return null;
  const metersPerCssPixel = extent / Math.max(1, cssWidth * Math.max(viewport.scale, 1e-6));
  if (!Number.isFinite(metersPerCssPixel) || metersPerCssPixel <= 0) return null;

  const targetPx = 108;
  const meters = niceScaleMeters(targetPx * metersPerCssPixel);
  const px = meters / metersPerCssPixel;
  if (!Number.isFinite(px) || px <= 1) return null;

  return (
    <div className="scale-bar hud-panel" aria-label={`Scale bar ${formatMeters(meters)}`}>
      <div className="scale-bar-track" style={{ width: `${Math.round(px)}px` }}>
        <span className="scale-bar-tick start" />
        <span className="scale-bar-tick mid" />
        <span className="scale-bar-tick end" />
      </div>
      <div className="scale-bar-label">{formatMeters(meters)}</div>
    </div>
  );
}

