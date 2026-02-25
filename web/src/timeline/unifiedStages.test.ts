import { describe, expect, it } from 'vitest';

import { UNIFIED_STAGE_DEFS, getInitialPreviewStageTimestampMs } from './unifiedStages';

describe('getInitialPreviewStageTimestampMs', () => {
  it('returns canonical stage timestamp for known phases', () => {
    const roadsTs = UNIFIED_STAGE_DEFS.find((s) => s.id === 'roads_arterial')?.timestampMs;
    expect(getInitialPreviewStageTimestampMs('roads')).toBe(roadsTs);
  });

  it('maps completed aliases to done stage', () => {
    const doneTs = UNIFIED_STAGE_DEFS.find((s) => s.id === 'done')?.timestampMs;
    expect(getInitialPreviewStageTimestampMs('completed')).toBe(doneTs);
    expect(getInitialPreviewStageTimestampMs('done')).toBe(doneTs);
  });

  it('falls back to done stage for unknown phases', () => {
    const doneTs = UNIFIED_STAGE_DEFS.find((s) => s.id === 'done')?.timestampMs;
    expect(getInitialPreviewStageTimestampMs('not_a_real_phase')).toBe(doneTs);
  });
});

