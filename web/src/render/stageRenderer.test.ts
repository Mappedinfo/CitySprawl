import { describe, expect, it } from 'vitest';

import { isStreamingRoadVisible, type LayerToggles } from './stageRenderer';

const allOn: LayerToggles = {
  terrain: true,
  rivers: true,
  majorRoads: true,
  localRoads: true,
  contours: true,
  blocks: true,
  parcels: true,
  pedestrianPaths: true,
  debugCandidates: true,
  labels: true,
  analysis: true,
  resources: true,
  traffic: true,
  buildings: true,
  greenZones: true,
};

describe('isStreamingRoadVisible', () => {
  it('filters major/local/ped classes by corresponding layer toggles', () => {
    expect(isStreamingRoadVisible({ ...allOn, majorRoads: false }, 'arterial')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, majorRoads: false }, 'collector')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, localRoads: false }, 'local')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, localRoads: false }, 'service')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, pedestrianPaths: false }, 'pedestrian')).toBe(false);
  });

  it('falls back to trace id hints for partial traces', () => {
    expect(isStreamingRoadVisible({ ...allOn, majorRoads: false }, undefined, 'collector-trace-1')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, localRoads: false }, undefined, 'local-trace-2')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, localRoads: false }, undefined, 'unknown-trace')).toBe(true);
  });

  it('hides unknown traces when all road layers are off', () => {
    expect(
      isStreamingRoadVisible(
        { ...allOn, majorRoads: false, localRoads: false, pedestrianPaths: false },
        undefined,
        'mystery-trace',
      ),
    ).toBe(false);
  });
});

