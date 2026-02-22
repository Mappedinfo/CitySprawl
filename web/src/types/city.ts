export type GenerateConfig = {
  seed: number;
  extent_m: number;
  grid_resolution: number;
  terrain: {
    noise_octaves: number;
    relief_strength: number;
  };
  hydrology: {
    enable: boolean;
    accum_threshold: number;
    min_river_length_m: number;
  };
  hubs: {
    t1_count: number;
    t2_count: number;
    t3_count: number;
    min_distance_m: number;
  };
  roads: {
    k_neighbors: number;
    loop_budget: number;
    branch_steps: number;
    slope_penalty: number;
    river_cross_penalty: number;
  };
  naming: {
    provider: string;
  };
};

export type Point2D = { x: number; y: number };

export type Polyline2D = { id: string; points: Point2D[] };
export type Polygon2D = { id: string; points: Point2D[] };
export type ContourLine = Polyline2D & { elevation_norm: number };
export type RiverAreaPolygon = Polygon2D & { flow: number; width_mean_m: number; is_main_stem: boolean; source_river_id?: string | null };
export type PedestrianPath = Polyline2D & { width_m: number; parent_block_id?: string | null };
export type LandBlock = Polygon2D & { block_class: string; area_m2: number; parent_id?: string | null };
export type ParcelLot = Polygon2D & { parcel_class: string; area_m2: number; parent_block_id?: string | null };

export type RiverLine = {
  id: string;
  points: Point2D[];
  flow: number;
  length_m: number;
};

export type HubRecord = {
  id: string;
  x: number;
  y: number;
  tier: number;
  score: number;
  name?: string | null;
  attrs: Record<string, number>;
};

export type RoadNodeRecord = {
  id: string;
  x: number;
  y: number;
  kind: string;
};

export type RoadEdgeRecord = {
  id: string;
  u: string;
  v: string;
  road_class: string;
  weight: number;
  length_m: number;
  river_crossings: number;
  width_m: number;
  render_order: number;
  path_points?: Point2D[] | null;
};

export type ResourceSite = {
  id: string;
  x: number;
  y: number;
  kind: string;
  quality: number;
  influence_radius_m: number;
};

export type TrafficEdgeFlow = {
  edge_id: string;
  flow: number;
  capacity: number;
  congestion_ratio: number;
  road_class: string;
};

export type BuildingFootprint = {
  id: string;
  points: Point2D[];
  height_hint: number;
};

export type CityArtifact = {
  meta: {
    schema_version: string;
    seed: number;
    duration_ms: number;
    generated_at_utc: string;
    config: GenerateConfig;
  };
  terrain: {
    extent_m: number;
    resolution: number;
    display_resolution: number;
    heights: number[][];
    slope_preview?: number[][] | null;
    terrain_class_preview?: number[][] | null;
    hillshade_preview?: number[][] | null;
    contours?: ContourLine[];
  };
  rivers: RiverLine[];
  river_areas?: RiverAreaPolygon[];
  visual_envelope?: Polygon2D | null;
  hubs: HubRecord[];
  roads: {
    nodes: RoadNodeRecord[];
    edges: RoadEdgeRecord[];
  };
  pedestrian_paths?: PedestrianPath[];
  blocks?: LandBlock[];
  parcels?: ParcelLot[];
  metrics: {
    hub_count: number;
    road_node_count: number;
    road_edge_count: number;
    connected: boolean;
    connectivity_ratio: number;
    dead_end_count: number;
    duplicate_edge_count: number;
    zero_length_edge_count: number;
    illegal_intersection_count: number;
    bridge_count: number;
    river_count: number;
    river_coverage_ratio: number;
    main_river_length_m: number;
    river_area_m2: number;
    river_area_clipped_ratio?: number | null;
    visual_envelope_area_ratio?: number | null;
    avg_edge_weight: number;
    notes: string[];
  };
  debug_layers: {
    candidate_edges: Array<{ id: string; a: Point2D; b: Point2D; weight?: number | null }>;
    suitability_preview?: number[][] | null;
    accumulation_preview?: number[][] | null;
  };
};

export type StageCaption = {
  text: string;
  text_zh?: string | null;
};

export type StageLayersSnapshot = {
  terrain_class_preview?: number[][] | null;
  hillshade_preview?: number[][] | null;
  contour_lines?: ContourLine[];
  river_area_polygons?: RiverAreaPolygon[];
  visual_envelope?: Polygon2D | null;
  suitability_preview?: number[][] | null;
  flood_risk_preview?: number[][] | null;
  population_potential_preview?: number[][] | null;
  resource_sites?: ResourceSite[];
  traffic_edge_flows?: TrafficEdgeFlow[];
  pedestrian_paths?: PedestrianPath[];
  land_blocks?: LandBlock[];
  parcel_lots?: ParcelLot[];
  building_footprints?: BuildingFootprint[];
  green_zones_preview?: number[][] | null;
};

export type StageArtifact = {
  stage_id: 'terrain' | 'analysis' | 'infrastructure' | 'traffic' | 'final_preview' | string;
  title: string;
  title_zh: string;
  subtitle: string;
  subtitle_zh: string;
  timestamp_ms: number;
  visible_layers: string[];
  metrics: Record<string, string | number | boolean | null>;
  caption?: StageCaption | null;
  layers: StageLayersSnapshot;
};

export type StagedCityResponse = {
  final_artifact: CityArtifact;
  stages: StageArtifact[];
};

export type PresetsResponse = Record<string, GenerateConfig>;
