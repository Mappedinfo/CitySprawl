from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from engine.models import (
    BuildingFootprint,
    CityArtifact,
    ContourLine,
    LandBlock,
    ParcelLot,
    PedestrianPath,
    Point2D,
    ResourceSite,
    RiverAreaPolygon,
    StageArtifact,
    StageCaption,
    StageLayersSnapshot,
    TrafficEdgeFlow,
)
from .narration import STAGE_SPECS


def _grid_to_list(grid: np.ndarray, precision: int = 4) -> List[List[float]]:
    return np.round(grid.astype(np.float64), precision).tolist()


def _grid_to_world(ix: int, iy: int, rows: int, cols: int, extent_m: float) -> Tuple[float, float]:
    x = (ix / float(max(cols - 1, 1))) * extent_m
    y = (iy / float(max(rows - 1, 1))) * extent_m
    return float(x), float(y)


def generate_building_footprints(
    seed: int,
    extent_m: float,
    hubs: Sequence[Any],
    population_potential_preview: np.ndarray,
    flood_risk_preview: np.ndarray,
) -> List[BuildingFootprint]:
    rng = np.random.default_rng(seed + 4701)
    rows, cols = population_potential_preview.shape
    footprints: List[BuildingFootprint] = []

    def flood_at(x: float, y: float) -> float:
        gx = int(round((x / extent_m) * (cols - 1))) if cols > 1 else 0
        gy = int(round((y / extent_m) * (rows - 1))) if rows > 1 else 0
        gx = min(max(gx, 0), cols - 1)
        gy = min(max(gy, 0), rows - 1)
        return float(flood_risk_preview[gy, gx]) if rows and cols else 0.0

    for hub in hubs:
        tier = int(getattr(hub, 'tier', 3))
        hx = float(getattr(hub, 'x', 0.0))
        hy = float(getattr(hub, 'y', 0.0))
        count = {1: 22, 2: 12}.get(tier, 6)
        spread = {1: 170.0, 2: 120.0}.get(tier, 80.0)
        base_size = {1: (26.0, 18.0), 2: (18.0, 12.0)}.get(tier, (12.0, 9.0))
        local_points: List[Tuple[float, float]] = []
        for _ in range(count * 3):
            if len(local_points) >= count:
                break
            x = hx + float(rng.uniform(-spread, spread))
            y = hy + float(rng.uniform(-spread, spread))
            if not (12.0 <= x <= extent_m - 12.0 and 12.0 <= y <= extent_m - 12.0):
                continue
            if flood_at(x, y) > 0.65:
                continue
            too_close = False
            for px, py in local_points:
                if (px - x) ** 2 + (py - y) ** 2 < (base_size[0] * 1.25) ** 2:
                    too_close = True
                    break
            if too_close:
                continue
            local_points.append((x, y))
        for px, py in local_points:
            w = base_size[0] * float(rng.uniform(0.8, 1.4))
            h = base_size[1] * float(rng.uniform(0.8, 1.4))
            height_hint = {1: 1.0, 2: 0.65}.get(tier, 0.35) * float(rng.uniform(0.8, 1.2))
            points = [
                Point2D(x=px - w / 2, y=py - h / 2),
                Point2D(x=px + w / 2, y=py - h / 2),
                Point2D(x=px + w / 2, y=py + h / 2),
                Point2D(x=px - w / 2, y=py + h / 2),
            ]
            footprints.append(BuildingFootprint(id=f'bldg-{len(footprints)}', points=points, height_hint=float(height_hint)))

    return footprints


def generate_green_zones_preview(
    suitability_preview: np.ndarray,
    flood_risk_preview: np.ndarray,
    population_potential_preview: np.ndarray,
) -> np.ndarray:
    green = (
        0.55 * np.clip(flood_risk_preview, 0.0, 1.0)
        + 0.25 * np.clip(1.0 - population_potential_preview, 0.0, 1.0)
        + 0.20 * np.clip(1.0 - suitability_preview, 0.0, 1.0)
    )
    return np.clip(green, 0.0, 1.0)


def build_stages(
    final_artifact: CityArtifact,
    suitability_preview: np.ndarray,
    flood_risk_preview: np.ndarray,
    population_potential_preview: np.ndarray,
    resource_sites: Sequence[ResourceSite],
    traffic_edge_flows: Sequence[TrafficEdgeFlow],
    building_footprints: Sequence[BuildingFootprint],
    green_zones_preview: np.ndarray,
    terrain_class_preview: np.ndarray,
    hillshade_preview: np.ndarray,
    contour_lines: Sequence[ContourLine],
    river_area_polygons: Sequence[RiverAreaPolygon],
    pedestrian_paths: Sequence[PedestrianPath],
    land_blocks: Sequence[LandBlock],
    parcel_lots: Sequence[ParcelLot],
) -> List[StageArtifact]:
    stages: List[StageArtifact] = []

    traffic_metrics = {
        'active_edges': int(sum(1 for f in traffic_edge_flows if f.flow > 0.0)),
        'max_congestion_ratio': float(max((f.congestion_ratio for f in traffic_edge_flows), default=0.0)),
    }
    final_metrics = {
        'building_count': len(building_footprints),
        'green_mean': float(np.mean(green_zones_preview)) if green_zones_preview.size else 0.0,
    }

    for spec in STAGE_SPECS:
        stage_id = str(spec['stage_id'])
        layers = StageLayersSnapshot()
        metrics: Dict[str, Any] = {}

        if stage_id == 'terrain':
            layers = StageLayersSnapshot(
                terrain_class_preview=np.asarray(terrain_class_preview, dtype=np.int64).tolist()
                if np.asarray(terrain_class_preview).size
                else None,
                hillshade_preview=_grid_to_list(hillshade_preview),
                contour_lines=list(contour_lines),
                river_area_polygons=list(river_area_polygons),
                visual_envelope=final_artifact.visual_envelope,
            )
            metrics = {
                'river_area_count': len(river_area_polygons),
                'contour_count': len(contour_lines),
            }
        elif stage_id == 'analysis':
            layers = StageLayersSnapshot(
                terrain_class_preview=np.asarray(terrain_class_preview, dtype=np.int64).tolist()
                if np.asarray(terrain_class_preview).size
                else None,
                hillshade_preview=_grid_to_list(hillshade_preview),
                contour_lines=list(contour_lines),
                river_area_polygons=list(river_area_polygons),
                suitability_preview=_grid_to_list(suitability_preview),
                flood_risk_preview=_grid_to_list(flood_risk_preview),
                population_potential_preview=_grid_to_list(population_potential_preview),
                resource_sites=list(resource_sites),
                visual_envelope=final_artifact.visual_envelope,
            )
            metrics = {
                'resource_site_count': len(resource_sites),
                'mean_suitability': float(np.mean(suitability_preview)) if suitability_preview.size else 0.0,
            }
        elif stage_id == 'traffic':
            layers = StageLayersSnapshot(
                contour_lines=list(contour_lines),
                river_area_polygons=list(river_area_polygons),
                visual_envelope=final_artifact.visual_envelope,
                traffic_edge_flows=list(traffic_edge_flows),
            )
            metrics = traffic_metrics
        elif stage_id == 'final_preview':
            layers = StageLayersSnapshot(
                contour_lines=list(contour_lines),
                river_area_polygons=list(river_area_polygons),
                pedestrian_paths=list(pedestrian_paths),
                land_blocks=list(land_blocks),
                parcel_lots=list(parcel_lots),
                building_footprints=list(building_footprints),
                green_zones_preview=_grid_to_list(green_zones_preview),
                visual_envelope=final_artifact.visual_envelope,
            )
            metrics = final_metrics
        else:
            layers = StageLayersSnapshot(
                contour_lines=list(contour_lines),
                river_area_polygons=list(river_area_polygons),
                visual_envelope=final_artifact.visual_envelope,
            )
            metrics = {
                'road_edge_count': final_artifact.metrics.road_edge_count,
                'river_count': final_artifact.metrics.river_count,
            }
            if stage_id == 'infrastructure':
                metrics['bridge_count'] = final_artifact.metrics.bridge_count
                metrics['hub_count'] = final_artifact.metrics.hub_count

        stages.append(
            StageArtifact(
                stage_id=stage_id,
                title=str(spec['title']),
                title_zh=str(spec['title_zh']),
                subtitle=str(spec['subtitle']),
                subtitle_zh=str(spec['subtitle_zh']),
                timestamp_ms=int(spec['timestamp_ms']),
                visible_layers=list(spec['visible_layers']),
                metrics=metrics,
                caption=StageCaption(text=str(spec['subtitle']), text_zh=str(spec['subtitle_zh'])),
                layers=layers,
            )
        )

    return stages
