from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees
from typing import Iterable, List, Sequence, Tuple

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from engine.models import PedestrianPath


@dataclass
class ParcelizationResult:
    pedestrian_paths: List[PedestrianPath]
    pedestrian_corridors_by_block: List[object]
    parcel_polygons_by_block: List[List[Polygon]]


def _iter_lines(geom) -> Iterable[LineString]:
    if getattr(geom, 'is_empty', True):
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, LineString)]


def _iter_polygons(geom) -> Iterable[Polygon]:
    if getattr(geom, 'is_empty', True):
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, Polygon)]


def _dominant_angle_deg(poly: Polygon) -> float:
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)
    if len(coords) < 4:
        return 0.0
    best_len = -1.0
    best_angle = 0.0
    for i in range(4):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        length = dx * dx + dy * dy
        if length > best_len:
            best_len = length
            best_angle = degrees(atan2(dy, dx))
    return best_angle


def _line_to_path(line: LineString, idx: int, parent_block_id: str, width_m: float) -> PedestrianPath | None:
    coords = list(line.coords)
    if len(coords) < 2:
        return None
    from engine.models import Point2D  # local import to avoid cycle issues in typing tools
    pts = [Point2D(x=float(x), y=float(y)) for x, y in coords]
    return PedestrianPath(id=f'ped-{idx}', points=pts, width_m=float(width_m), parent_block_id=parent_block_id)


def generate_pedestrian_paths_and_parcels(
    macro_blocks: Sequence[Polygon],
    pedestrian_width_m: float = 3.0,
) -> ParcelizationResult:
    pedestrian_paths: List[PedestrianPath] = []
    corridors_by_block: List[object] = []
    parcels_by_block: List[List[Polygon]] = []
    path_idx = 0

    for block_idx, block in enumerate(macro_blocks):
        block_id = f'block-{block_idx}'
        if block.area < 6_000.0:
            corridors_by_block.append(Polygon())
            parcels_by_block.append([block])
            continue

        angle = _dominant_angle_deg(block)
        rot = affinity.rotate(block, -angle, origin='centroid')
        minx, miny, maxx, maxy = rot.bounds
        w = maxx - minx
        h = maxy - miny
        shorter = min(w, h)
        longer = max(w, h)
        if shorter < 20 or longer < 40:
            corridors_by_block.append(Polygon())
            parcels_by_block.append([block])
            continue

        if block.area < 25_000:
            n_lines = 1
        elif block.area < 80_000:
            n_lines = 2
        else:
            n_lines = 3

        use_vertical = w <= h  # cut across shorter span to make parcels
        line_geoms = []
        for i in range(n_lines):
            t = (i + 1) / float(n_lines + 1)
            if use_vertical:
                x = minx + t * w
                candidate = LineString([(x, miny - 5.0), (x, maxy + 5.0)])
            else:
                y = miny + t * h
                candidate = LineString([(minx - 5.0, y), (maxx + 5.0, y)])
            clipped = rot.intersection(candidate)
            for line in _iter_lines(clipped):
                if line.length < 8.0:
                    continue
                line_world = affinity.rotate(line, angle, origin=block.centroid)
                line_geoms.append(line_world)

        if not line_geoms:
            corridors_by_block.append(Polygon())
            parcels_by_block.append([block])
            continue

        path_models = []
        for line in line_geoms:
            model = _line_to_path(line, path_idx, block_id, pedestrian_width_m)
            path_idx += 1
            if model is not None:
                path_models.append(model)
        pedestrian_paths.extend(path_models)

        corridors = [line.buffer(pedestrian_width_m / 2.0, cap_style=2, join_style=2, resolution=4) for line in line_geoms]
        corridor_union = unary_union(corridors).intersection(block).buffer(0)
        corridors_by_block.append(corridor_union)

        parcels_geom = block.difference(corridor_union).buffer(0)
        parcels = []
        for poly in _iter_polygons(parcels_geom):
            if poly.area < 500.0:
                continue
            minx2, miny2, maxx2, maxy2 = poly.bounds
            if (maxx2 - minx2) < 8.0 or (maxy2 - miny2) < 8.0:
                continue
            parcels.append(poly)
        if not parcels:
            parcels = [block]
        parcels_by_block.append(parcels)

    return ParcelizationResult(
        pedestrian_paths=pedestrian_paths,
        pedestrian_corridors_by_block=corridors_by_block,
        parcel_polygons_by_block=parcels_by_block,
    )
