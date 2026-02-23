from __future__ import annotations

from typing import Iterable, List, Sequence

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from engine.blocks.extraction import BlockExtractionResult
from engine.models import HubRecord, LandBlock, ParcelLot, Point2D, ResourceSite, RiverAreaPolygon, RoadNetwork


_ALLOWED = {
    'residential_candidate',
    'commercial_candidate',
    'industrial_candidate',
    'green_candidate',
    'public_facility_candidate',
}


def _poly_to_points(poly: Polygon) -> List[Point2D]:
    coords = list(poly.exterior.coords)
    return [Point2D(x=float(x), y=float(y)) for x, y in coords[:-1]]


def _road_unions(road_network: RoadNetwork):
    from engine.blocks.extraction import vehicular_corridor_union
    from engine.blocks.extraction import _edge_coords

    node_lookup = {n.id: n for n in road_network.nodes}
    arterial = []
    collector = []
    local = []
    from shapely.geometry import LineString
    local_cul_endpoints = []
    for e in road_network.edges:
        if e.road_class not in ('arterial', 'collector', 'local'):
            continue
        coords = _edge_coords(e, node_lookup)
        if coords is None:
            continue
        buf = LineString(coords).buffer(float(getattr(e, 'width_m', 8.0)) / 2.0, cap_style=2, join_style=2)
        if e.road_class == 'arterial':
            arterial.append(buf)
        elif e.road_class == 'collector':
            collector.append(buf)
        else:
            local.append(buf)
            if '-cul' in str(getattr(e, 'id', '')) and len(coords) >= 2:
                local_cul_endpoints.append(coords[0])
                local_cul_endpoints.append(coords[-1])
    return (
        unary_union(arterial) if arterial else None,
        unary_union(collector) if collector else None,
        unary_union(local) if local else None,
        vehicular_corridor_union(road_network),
        local_cul_endpoints,
    )


def _nearest_hub_distance_and_tier(x: float, y: float, hubs: Sequence[HubRecord]):
    best = None
    for h in hubs:
        d = (h.x - x) ** 2 + (h.y - y) ** 2
        if best is None or d < best[0]:
            best = (d, h.tier)
    if best is None:
        return 1e9, 3
    return float(best[0] ** 0.5), int(best[1])


def _resource_score(centroid, resource_sites: Sequence[ResourceSite]):
    score = 0.0
    for r in resource_sites:
        dx = centroid.x - r.x
        dy = centroid.y - r.y
        dist = (dx * dx + dy * dy) ** 0.5
        influence = float(r.influence_radius_m)
        if dist > influence * 1.5:
            continue
        w = max(0.0, 1.0 - dist / max(influence * 1.5, 1e-6)) * float(r.quality)
        if r.kind == 'agri':
            score += 0.7 * w
        elif r.kind == 'ore':
            score += 1.0 * w
        elif r.kind == 'water':
            score += 0.35 * w
        elif r.kind == 'forest':
            score += 0.25 * w
    return score


def _sample_grid(grid: np.ndarray | None, x: float, y: float, extent_m: float) -> float:
    if grid is None or grid.size == 0:
        return 0.0
    rows, cols = grid.shape
    gx = int(round((x / extent_m) * (cols - 1))) if cols > 1 else 0
    gy = int(round((y / extent_m) * (rows - 1))) if rows > 1 else 0
    gx = min(max(gx, 0), cols - 1)
    gy = min(max(gy, 0), rows - 1)
    return float(grid[gy, gx])


def classify_blocks_and_parcels(
    extent_m: float,
    extraction: BlockExtractionResult,
    parcel_polygons_by_block: Sequence[Sequence[Polygon]],
    hubs: Sequence[HubRecord],
    road_network: RoadNetwork,
    river_areas: Sequence[RiverAreaPolygon],
    resource_sites: Sequence[ResourceSite],
    flood_risk_preview: np.ndarray | None,
) -> tuple[List[LandBlock], List[ParcelLot]]:
    river_union = extraction.river_union
    arterial_union, collector_union, local_union, veh_union, local_cul_endpoints = _road_unions(road_network)
    blocks: List[LandBlock] = []
    parcels: List[ParcelLot] = []

    for block_idx, block_poly in enumerate(extraction.macro_blocks):
        block_id = f'block-{block_idx}'
        parcels_in_block = list(parcel_polygons_by_block[block_idx]) if block_idx < len(parcel_polygons_by_block) else []
        if not parcels_in_block:
            parcels_in_block = [block_poly]

        parcel_classes: List[str] = []
        for parcel_idx, parcel_poly in enumerate(parcels_in_block):
            c = parcel_poly.representative_point()
            dist_river = river_union.distance(c) if getattr(river_union, 'is_empty', True) is False else 1e9
            dist_art = arterial_union.distance(c) if arterial_union is not None else 1e9
            dist_col = collector_union.distance(c) if collector_union is not None else 1e9
            dist_local = local_union.distance(c) if local_union is not None else 1e9
            dist_cul = min((((c.x - x) ** 2 + (c.y - y) ** 2) ** 0.5) for x, y in local_cul_endpoints) if local_cul_endpoints else 1e9
            hub_dist, hub_tier = _nearest_hub_distance_and_tier(c.x, c.y, hubs)
            flood = _sample_grid(flood_risk_preview, c.x, c.y, extent_m)
            res_score = _resource_score(c, resource_sites)
            area = float(parcel_poly.area)

            if flood > 0.6 or dist_river < 12.0:
                cls = 'green_candidate'
            elif area > 20_000 and dist_art < 30.0 and hub_dist > 180.0:
                cls = 'industrial_candidate'
            elif (dist_art < 35.0 or dist_col < 24.0) and hub_tier <= 2 and area > 1_200:
                cls = 'commercial_candidate'
            elif area > 6_000 and (res_score > 0.45 or hub_dist < 160.0):
                cls = 'public_facility_candidate'
            elif dist_cul < 35.0 and dist_art > 55.0:
                cls = 'residential_candidate'
            elif dist_local > 65.0 and dist_col > 55.0 and area > 8_000:
                cls = 'green_candidate'
            else:
                cls = 'residential_candidate'

            if cls not in _ALLOWED:
                cls = 'residential_candidate'
            parcel_classes.append(cls)
            parcels.append(
                ParcelLot(
                    id=f'parcel-{len(parcels)}',
                    points=_poly_to_points(parcel_poly),
                    parcel_class=cls,
                    area_m2=area,
                    parent_block_id=block_id,
                )
            )

        # block class = dominant parcel class
        if parcel_classes:
            dominant = max(set(parcel_classes), key=parcel_classes.count)
        else:
            dominant = 'unclassified'
        blocks.append(
            LandBlock(
                id=block_id,
                points=_poly_to_points(block_poly),
                block_class=dominant,
                area_m2=float(block_poly.area),
            )
        )

    return blocks, parcels
