import { describe, expect, it } from 'vitest';

import { isStreamingRoadVisible, type LayerToggles } from './stageRenderer';

const allOn: LayerToggles = {
  terrain: true,
  rivers: true,
  arterialRoads: true,
  majorLocalRoads: true,
  minorLocalRoads: true,
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
  it('filters arterial/major_local/minor_local/ped classes by corresponding layer toggles', () => {
    expect(isStreamingRoadVisible({ ...allOn, arterialRoads: false }, 'arterial')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, majorLocalRoads: false }, 'major_local')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, minorLocalRoads: false }, 'minor_local')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, minorLocalRoads: false }, 'service')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, pedestrianPaths: false }, 'pedestrian')).toBe(false);
  });

  it('falls back to trace id hints for partial traces', () => {
    expect(isStreamingRoadVisible({ ...allOn, arterialRoads: false }, undefined, 'arterial-trace-1')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, majorLocalRoads: false }, undefined, 'major_local-trace-1')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, minorLocalRoads: false }, undefined, 'minor_local-trace-2')).toBe(false);
    expect(isStreamingRoadVisible({ ...allOn, minorLocalRoads: false }, undefined, 'unknown-trace')).toBe(true);
  });

  it('hides unknown traces when all road layers are off', () => {
    expect(
      isStreamingRoadVisible(
        { ...allOn, arterialRoads: false, majorLocalRoads: false, minorLocalRoads: false, pedestrianPaths: false },
        undefined,
        'mystery-trace',
      ),
    ).toBe(false);
  });
});
