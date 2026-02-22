export function NorthArrow() {
  return (
    <div className="north-arrow hud-panel" aria-label="North arrow">
      <div className="north-arrow-label">N</div>
      <svg viewBox="0 0 24 24" className="north-arrow-icon" aria-hidden="true">
        <path d="M12 2 L19 19 L12 15 L5 19 Z" />
      </svg>
    </div>
  );
}

