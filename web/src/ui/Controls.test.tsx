import type { ComponentProps } from 'react';

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { GenerateConfig, PresetsResponse } from '../types/city';
import { Controls } from './Controls';

const testConfig: GenerateConfig = {
  seed: 42,
  extent_m: 10000,
  grid_resolution: 256,
  quality: { profile: 'balanced', time_budget_ms: 15000 },
  terrain: { noise_octaves: 2, relief_strength: 0.12 },
  hydrology: {
    enable: true,
    accum_threshold: 0.015,
    min_river_length_m: 1000,
    primary_branch_count_max: 4,
    centerline_smooth_iters: 2,
    width_taper_strength: 0.35,
    bank_irregularity: 0.08,
  },
  hubs: { t1_count: 1, t2_count: 4, t3_count: 20, min_distance_m: 600 },
  roads: {
    k_neighbors: 4,
    loop_budget: 3,
    branch_steps: 2,
    slope_penalty: 2,
    river_cross_penalty: 300,
    style: 'mixed_organic',
    collector_spacing_m: 420,
    local_spacing_m: 130,
    collector_jitter: 0.16,
    local_jitter: 0.22,
    river_setback_m: 18,
    minor_bridge_budget: 4,
    max_local_block_area_m2: 180000,
  },
  parcels: {
    enable: true,
    residential_target_area_m2: 1800,
    mixed_target_area_m2: 2600,
    min_frontage_m: 10,
    min_depth_m: 12,
  },
  naming: { provider: 'mock' },
};

const testPresets: PresetsResponse = {
  default: testConfig,
};

function renderControls(overrides: Partial<ComponentProps<typeof Controls>> = {}) {
  const props: ComponentProps<typeof Controls> = {
    config: testConfig,
    presets: testPresets,
    selectedPreset: 'default',
    onPresetChange: vi.fn(),
    onConfigChange: vi.fn(),
    onExport: vi.fn(),
    stagedJsonPath: '',
    onStagedJsonPathChange: vi.fn(),
    onLoadStagedJson: vi.fn(),
    loading: false,
    layers: {
      terrain: true,
      rivers: true,
      arterialRoads: true,
      majorLocalRoads: true,
      minorLocalRoads: true,
      contours: true,
      blocks: true,
      parcels: true,
      pedestrianPaths: true,
      debugCandidates: false,
      labels: true,
      analysis: true,
      resources: true,
      traffic: true,
      buildings: true,
      greenZones: true,
    },
    onLayerToggle: vi.fn(),
    ...overrides,
  };

  const renderResult = render(<Controls {...props} />);
  return { ...renderResult, props };
}

function getLayersSection(): HTMLElement {
  const layersTitle = screen.getByText('Layers');
  const section = layersTitle.closest('.section');
  if (!section) throw new Error('Layers section not found');
  return section as HTMLElement;
}

describe('Controls layers groups', () => {
  it('renders grouped layers in field/surface/line/point order with unique entries', () => {
    renderControls();
    const layersSection = getLayersSection();
    const scoped = within(layersSection);

    const groupTitles = scoped
      .getAllByText(/^(Field \/ 场|Surface \/ 面|Line \/ 线|Point \/ 点)$/)
      .map((el) => el.textContent);
    expect(groupTitles).toEqual(['Field / 场', 'Surface / 面', 'Line / 线', 'Point / 点']);

    const expectedLayerLabels = [
      'Terrain',
      'Contours',
      'Analysis Heatmaps',
      'Rivers',
      'Blocks',
      'Parcels',
      'Buildings',
      'Green Zones',
      'Arterial Roads',
      'Major Local Roads',
      'Minor Local Roads',
      'Pedestrian Paths',
      'Candidate Edges',
      'Traffic Heat',
      'Resource Sites',
      'Labels',
    ];

    for (const label of expectedLayerLabels) {
      expect(scoped.getAllByText(label)).toHaveLength(1);
    }
  });

  it('shows line-layer road toggles without the hidden unified roads switch', () => {
    renderControls();
    const layersSection = getLayersSection();
    const lineGroup = layersSection.querySelector('[data-layer-group="line"]');
    expect(lineGroup).not.toBeNull();

    const arterialRoadsItem = lineGroup?.querySelector('[data-layer-key="arterialRoads"]');
    const majorLocalRoadsItem = lineGroup?.querySelector('[data-layer-key="majorLocalRoads"]');
    const minorLocalRoadsItem = lineGroup?.querySelector('[data-layer-key="minorLocalRoads"]');

    expect(lineGroup?.querySelector('[data-layer-key="roads"]')).toBeNull();
    expect(arterialRoadsItem).toBeInTheDocument();
    expect(majorLocalRoadsItem).toBeInTheDocument();
    expect(minorLocalRoadsItem).toBeInTheDocument();
    expect(arterialRoadsItem).not.toHaveClass('is-child');
    expect(majorLocalRoadsItem).not.toHaveClass('is-child');
    expect(minorLocalRoadsItem).not.toHaveClass('is-child');
  });

  it('calls onLayerToggle with the correct key when toggling a grouped layer', async () => {
    const user = userEvent.setup();
    const onLayerToggle = vi.fn();
    renderControls({ onLayerToggle });
    const layersSection = getLayersSection();

    const arterialRoadsCheckbox = layersSection.querySelector('[data-layer-key="arterialRoads"] input');
    expect(arterialRoadsCheckbox).not.toBeNull();
    await user.click(arterialRoadsCheckbox!);
    expect(onLayerToggle).toHaveBeenCalledWith('arterialRoads');
  });

  it('renders inline legends for layer rows', () => {
    renderControls();
    const layersSection = getLayersSection();

    for (const layerKey of ['terrain', 'arterialRoads', 'traffic']) {
      const row = layersSection.querySelector(`[data-layer-key="${layerKey}"]`);
      expect(row).not.toBeNull();
      expect(row?.querySelector('.layer-item-legend')).not.toBeNull();
    }
  });

  it('disables unreached layers during generation phase while keeping reached layers interactive', async () => {
    const user = userEvent.setup();
    const onLayerToggle = vi.fn();
    renderControls({
      onLayerToggle,
      layerUiState: {
        isGenerationPhase: true,
        reachedLayerKeys: ['terrain', 'contours', 'rivers', 'arterialRoads'],
        activeLayerKeys: ['arterialRoads'],
        activeGroupIds: ['line'],
      },
    });

    const layersSection = getLayersSection();
    const arterial = layersSection.querySelector('[data-layer-key="arterialRoads"] input') as HTMLInputElement;
    const parcels = layersSection.querySelector('[data-layer-key="parcels"] input') as HTMLInputElement;

    expect(arterial.disabled).toBe(false);
    expect(parcels.disabled).toBe(true);

    await user.click(arterial);
    expect(onLayerToggle).toHaveBeenCalledWith('arterialRoads');

    await user.click(parcels);
    expect(onLayerToggle).toHaveBeenCalledTimes(1);
  });

  it('highlights active generating layer rows and their group title', () => {
    renderControls({
      layerUiState: {
        isGenerationPhase: true,
        reachedLayerKeys: ['terrain', 'contours', 'rivers', 'arterialRoads', 'majorLocalRoads', 'debugCandidates'],
        activeLayerKeys: ['arterialRoads', 'majorLocalRoads', 'debugCandidates'],
        activeGroupIds: ['line'],
      },
    });
    const layersSection = getLayersSection();
    const lineGroup = layersSection.querySelector('[data-layer-group="line"]') as HTMLElement;
    const lineGroupTitle = lineGroup.querySelector('.layer-group-title');
    expect(lineGroupTitle).toHaveClass('is-active-group');

    expect(lineGroup.querySelector('[data-layer-key="arterialRoads"]')).toHaveClass('is-active-generating');
    expect(lineGroup.querySelector('[data-layer-key="majorLocalRoads"]')).toHaveClass('is-active-generating');
    expect(lineGroup.querySelector('[data-layer-key="debugCandidates"]')).toHaveClass('is-active-generating');
  });
});
