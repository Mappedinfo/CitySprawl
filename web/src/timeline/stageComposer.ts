import type {
  BuildingFootprint,
  CityArtifact,
  ResourceSite,
  StageArtifact,
  StagedCityResponse,
  TrafficEdgeFlow,
} from '../types/city';

const STAGE_TEMPLATES: Array<Omit<StageArtifact, 'layers' | 'metrics'>> = [
  {
    stage_id: 'terrain',
    title: 'Terrain Input',
    title_zh: '地形输入',
    subtitle: 'Complex terrain and hydrology baseline',
    subtitle_zh: '复杂地形与水文基底',
    timestamp_ms: 0,
    visible_layers: ['terrain', 'rivers', 'river_areas', 'contours'],
    caption: { text: 'Complex terrain input', text_zh: '复杂地形输入' },
  },
  {
    stage_id: 'analysis',
    title: 'Habitable Analysis',
    title_zh: '宜居性分析',
    subtitle: 'Identifying habitable areas and allocating resources',
    subtitle_zh: '识别宜居区域并配置资源',
    timestamp_ms: 3000,
    visible_layers: ['terrain', 'rivers', 'river_areas', 'contours', 'analysis_heatmaps', 'resources'],
    caption: { text: 'Identifying habitable areas', text_zh: '识别宜居区域' },
  },
  {
    stage_id: 'infrastructure',
    title: 'Infrastructure Planning',
    title_zh: '基础设施规划',
    subtitle: 'Road network generation and bridge placement',
    subtitle_zh: '道路网络生成与桥梁布设',
    timestamp_ms: 7000,
    visible_layers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels'],
    caption: { text: 'Mapping infrastructure', text_zh: '基础设施规划中' },
  },
  {
    stage_id: 'traffic',
    title: 'Traffic Simulation',
    title_zh: '交通模拟',
    subtitle: 'OD flow assignment preview',
    subtitle_zh: 'OD流量分配预览',
    timestamp_ms: 11000,
    visible_layers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'traffic_heat'],
    caption: { text: 'Simulating traffic', text_zh: '交通模拟中' },
  },
  {
    stage_id: 'final_preview',
    title: 'City Preview',
    title_zh: '城市预览',
    subtitle: 'Composite preview with buildings and green zones',
    subtitle_zh: '带建筑与绿地的合成预览',
    timestamp_ms: 15000,
    visible_layers: ['terrain', 'rivers', 'river_areas', 'contours', 'roads', 'hubs', 'labels', 'pedestrian_paths', 'blocks', 'parcels', 'buildings', 'green_zones'],
    caption: { text: 'High-quality city preview generated', text_zh: '高质量城市预览已生成' },
  },
];

function normalizeGrid(grid: number[][] | null | undefined): number[][] | undefined {
  if (!grid || !grid.length || !grid[0]?.length) return undefined;
  let min = Infinity;
  let max = -Infinity;
  for (const row of grid) {
    for (const v of row) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  const span = Math.max(1e-6, max - min);
  return grid.map((row) => row.map((v) => (v - min) / span));
}

function pseudoResources(artifact: CityArtifact): ResourceSite[] {
  const resources: ResourceSite[] = [];
  artifact.hubs.slice(0, 6).forEach((hub, i) => {
    resources.push({
      id: `fallback-agri-${i}`,
      x: hub.x + (i % 2 === 0 ? 35 : -35),
      y: hub.y + (i % 3 === 0 ? 28 : -22),
      kind: i % 3 === 0 ? 'agri' : i % 3 === 1 ? 'forest' : 'ore',
      quality: Math.max(0.35, Math.min(0.95, hub.score)),
      influence_radius_m: 140,
    });
  });
  artifact.rivers.slice(0, 3).forEach((river, i) => {
    const p = river.points[Math.floor(river.points.length / 2)] ?? river.points[0];
    if (!p) return;
    resources.push({
      id: `fallback-water-${i}`,
      x: p.x,
      y: p.y,
      kind: 'water',
      quality: Math.min(1, Math.log10(1 + river.flow) / 3.5),
      influence_radius_m: 180,
    });
  });
  return resources;
}

function pseudoTraffic(artifact: CityArtifact): TrafficEdgeFlow[] {
  const weights = artifact.roads.edges.map((e) => e.weight);
  const wMin = Math.min(...weights, 0);
  const wMax = Math.max(...weights, 1);
  return artifact.roads.edges.map((edge) => {
    const inv = 1 - (edge.weight - wMin) / Math.max(1e-6, wMax - wMin);
    const base = edge.road_class === 'arterial' ? 150 : 45;
    const flow = Math.max(0, base * (0.3 + inv));
    const capacity = (edge.road_class === 'arterial' ? 1100 : 420) * Math.max(0.6, Math.min(1.8, edge.length_m / 140));
    return {
      edge_id: edge.id,
      flow,
      capacity,
      congestion_ratio: flow / Math.max(capacity, 1e-6),
      road_class: edge.road_class,
    };
  });
}

function pseudoBuildings(artifact: CityArtifact): BuildingFootprint[] {
  const out: BuildingFootprint[] = [];
  let idx = 0;
  for (const hub of artifact.hubs) {
    const n = hub.tier === 1 ? 8 : hub.tier === 2 ? 5 : 2;
    const spread = hub.tier === 1 ? 120 : hub.tier === 2 ? 80 : 50;
    for (let i = 0; i < n; i += 1) {
      const angle = (i / Math.max(1, n)) * Math.PI * 2;
      const x = hub.x + Math.cos(angle) * spread * (0.55 + (i % 3) * 0.15);
      const y = hub.y + Math.sin(angle) * spread * (0.55 + (i % 2) * 0.18);
      const w = hub.tier === 1 ? 22 : hub.tier === 2 ? 16 : 11;
      const h = hub.tier === 1 ? 14 : hub.tier === 2 ? 10 : 8;
      out.push({
        id: `fb-bldg-${idx++}`,
        height_hint: hub.tier === 1 ? 1.0 : hub.tier === 2 ? 0.65 : 0.35,
        points: [
          { x: x - w / 2, y: y - h / 2 },
          { x: x + w / 2, y: y - h / 2 },
          { x: x + w / 2, y: y + h / 2 },
          { x: x - w / 2, y: y + h / 2 },
        ],
      });
    }
  }
  return out;
}

function pseudoGreenZones(artifact: CityArtifact): number[][] | undefined {
  const terrain = artifact.terrain.heights;
  if (!terrain?.length) return undefined;
  const suitability = normalizeGrid(artifact.debug_layers.suitability_preview ?? terrain);
  const accum = normalizeGrid(artifact.debug_layers.accumulation_preview ?? terrain);
  if (!suitability || !accum) return undefined;
  return suitability.map((row, y) =>
    row.map((s, x) => Math.max(0, Math.min(1, 0.55 * (accum[y]?.[x] ?? 0) + 0.45 * (1 - s)))),
  );
}

function pseudoPopulation(artifact: CityArtifact): number[][] | undefined {
  const suit = normalizeGrid(artifact.debug_layers.suitability_preview);
  const accum = normalizeGrid(artifact.debug_layers.accumulation_preview);
  if (!suit) return undefined;
  return suit.map((row, y) =>
    row.map((s, x) => {
      const floodish = accum?.[y]?.[x] ?? 0;
      return Math.max(0, Math.min(1, 0.75 * s + 0.25 * (1 - floodish)));
    }),
  );
}

function pseudoFlood(artifact: CityArtifact): number[][] | undefined {
  const accum = normalizeGrid(artifact.debug_layers.accumulation_preview);
  return accum;
}

export function composeFallbackStagedResponse(artifact: CityArtifact): StagedCityResponse {
  const suitability = normalizeGrid(artifact.debug_layers.suitability_preview);
  const flood = pseudoFlood(artifact);
  const population = pseudoPopulation(artifact);
  const resources = pseudoResources(artifact);
  const traffic = pseudoTraffic(artifact);
  const buildings = pseudoBuildings(artifact);
  const green = pseudoGreenZones(artifact);

  const stages: StageArtifact[] = STAGE_TEMPLATES.map((base) => {
    let layers: StageArtifact['layers'] = {
      contour_lines: artifact.terrain.contours,
      river_area_polygons: artifact.river_areas,
    };
    let metrics: StageArtifact['metrics'] = {};
    if (base.stage_id === 'terrain') {
      layers = {
        ...layers,
        terrain_class_preview: artifact.terrain.terrain_class_preview,
        hillshade_preview: artifact.terrain.hillshade_preview,
      };
      metrics = { road_edge_count: artifact.metrics.road_edge_count };
    } else if (base.stage_id === 'analysis') {
      layers = {
        ...layers,
        terrain_class_preview: artifact.terrain.terrain_class_preview,
        hillshade_preview: artifact.terrain.hillshade_preview,
        suitability_preview: suitability,
        flood_risk_preview: flood,
        population_potential_preview: population,
        resource_sites: resources,
      };
      metrics = { resource_site_count: resources.length };
    } else if (base.stage_id === 'traffic') {
      layers = { ...layers, traffic_edge_flows: traffic };
      metrics = { max_congestion_ratio: Math.max(0, ...traffic.map((t) => t.congestion_ratio)) };
    } else if (base.stage_id === 'final_preview') {
      layers = {
        ...layers,
        pedestrian_paths: artifact.pedestrian_paths,
        land_blocks: artifact.blocks,
        parcel_lots: artifact.parcels,
        building_footprints: buildings,
        green_zones_preview: green,
      };
      metrics = { building_count: buildings.length };
    } else {
      metrics = { road_edge_count: artifact.metrics.road_edge_count };
    }
    return { ...base, layers, metrics };
  });

  return { final_artifact: artifact, stages };
}
