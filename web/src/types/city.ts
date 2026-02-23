export type GenerateConfig = {
  seed: number;
  extent_m: number;
  grid_resolution: number;
  quality: {
    profile: string;
    time_budget_ms: number;
  };
  terrain: {
    noise_octaves: number;
    relief_strength: number;
  };
  hydrology: {
    enable: boolean;
    accum_threshold: number;
    min_river_length_m: number;
    primary_branch_count_max: number;
    centerline_smooth_iters: number;
    width_taper_strength: number;
    bank_irregularity: number;
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
    style: string;
    collector_spacing_m: number;
    local_spacing_m: number;
    collector_jitter: number;
    local_jitter: number;
    local_generator?: string;
    local_geometry_mode?: string;
    local_reroute_coverage?: string;
    local_reroute_min_length_m?: number;
    local_reroute_waypoint_spacing_m?: number;
    local_reroute_max_waypoints?: number;
    local_reroute_corridor_buffer_m?: number;
    local_reroute_block_margin_m?: number;
    local_reroute_slope_penalty_scale?: number;
    local_reroute_river_penalty_scale?: number;
    local_reroute_collector_snap_bias_m?: number;
    local_reroute_smooth_iters?: number;
    local_reroute_simplify_tol_m?: number;
    local_reroute_max_edges_per_city?: number;
    local_reroute_apply_to_grid_supplement?: boolean;
    local_classic_probe_step_m?: number;
    local_classic_seed_spacing_m?: number;
    local_classic_max_trace_len_m?: number;
    local_classic_min_trace_len_m?: number;
    local_classic_turn_limit_deg?: number;
    local_classic_branch_prob?: number;
    local_classic_continue_prob?: number;
    local_classic_culdesac_prob?: number;
    local_classic_max_segments_per_block?: number;
    local_community_seed_count_per_block?: number;
    local_community_spine_prob?: number;
    local_arterial_setback_weight?: number;
    local_collector_follow_weight?: number;
    river_setback_m: number;
    minor_bridge_budget: number;
    max_local_block_area_m2: number;
    collector_generator?: string;
    classic_probe_step_m?: number;
    classic_seed_spacing_m?: number;
    classic_max_trace_len_m?: number;
    classic_min_trace_len_m?: number;
    classic_turn_limit_deg?: number;
    classic_branch_prob?: number;
    classic_continue_prob?: number;
    classic_culdesac_prob?: number;
    classic_max_queue_size?: number;
    classic_max_segments?: number;
    slope_straight_threshold_deg?: number;
    slope_serpentine_threshold_deg?: number;
    slope_hard_limit_deg?: number;
    contour_follow_weight?: number;
    arterial_align_weight?: number;
    hub_seek_weight?: number;
    river_snap_dist_m?: number;
    river_parallel_bias_weight?: number;
    river_avoid_weight?: number;
  };
  parcels: {
    enable: boolean;
    residential_target_area_m2: number;
    mixed_target_area_m2: number;
    min_frontage_m: number;
    min_depth_m: number;
    parcel_local_morphology_coupling?: boolean;
    parcel_culdesac_frontage_relaxation?: number;
    parcel_local_depth_bias?: number;
    parcel_curvilinear_split_bias?: number;
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
    river_mainstem_count?: number;
    visual_envelope_area_ratio?: number | null;
    avg_edge_weight: number;
    road_edge_count_by_class?: Record<string, number>;
    parcel_count?: number;
    median_parcel_area_m2?: number;
    local_reroute_candidate_count?: number;
    local_reroute_applied_count?: number;
    local_reroute_fallback_count?: number;
    local_reroute_grid_supplement_applied_count?: number;
    local_two_point_edge_count?: number;
    local_two_point_edge_ratio?: number;
    local_reroute_avg_path_points?: number;
    local_reroute_avg_length_gain_ratio?: number;
    generation_profile?: string;
    degraded_mode?: boolean;
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

export type GenerateJobLog = {
  seq: number;
  ts: string;
  phase: string;
  progress: number;
  message: string;
};

export type GenerateJobStatusResponse = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | string;
  progress: number;
  phase: string;
  message: string;
  created_at: string;
  updated_at: string;
  error?: string | null;
  logs: GenerateJobLog[];
  last_log_seq: number;
  result_ready: boolean;
};
