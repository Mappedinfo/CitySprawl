from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from engine.models import CityArtifact, RiverAreaPolygon, RoadNetwork


@dataclass
class BlockExtractionResult:
    boundary: Polygon
    river_union: Polygon | MultiPolygon | object
    vehicular_corridor_union: Polygon | MultiPolygon | object
    macro_blocks: List[Polygon]


def river_union_geometry(river_areas: Sequence[RiverAreaPolygon]):
    polys = []
    for area in river_areas:
        if len(area.points) < 3:
            continue
        poly = Polygon([(p.x, p.y) for p in area.points])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        polys.append(poly)
    if not polys:
        return Polygon()
    return unary_union(polys)


def _edge_coords(edge, node_lookup):
    path_points = getattr(edge, "path_points", None)
    if path_points and len(path_points) >= 2:
        coords = []
        for p in path_points:
            x = p.x if hasattr(p, "x") else p.get("x")
            y = p.y if hasattr(p, "y") else p.get("y")
            coords.append((float(x), float(y)))
        if len(coords) >= 2:
            return coords
    u = node_lookup.get(edge.u)
    v = node_lookup.get(edge.v)
    if u is None or v is None:
        return None
    return [(u.x, u.y), (v.x, v.y)]


def vehicular_corridor_union(road_network: RoadNetwork):
    node_lookup = {n.id: n for n in road_network.nodes}
    geoms = []
    for edge in road_network.edges:
        if edge.road_class not in ('arterial', 'collector', 'local'):
            continue
        coords = _edge_coords(edge, node_lookup)
        if coords is None:
            continue
        width_m = float(getattr(edge, 'width_m', 8.0) or 8.0)
        if width_m <= 0.0:
            continue
        line = LineString(coords)
        geoms.append(line.buffer(width_m / 2.0, cap_style=2, join_style=2, resolution=4))
    if not geoms:
        return Polygon()
    return unary_union(geoms)


def _iter_polygons(geom) -> Iterable[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, Polygon)]


def extract_macro_blocks(extent_m: float, road_network: RoadNetwork, river_areas: Sequence[RiverAreaPolygon]) -> BlockExtractionResult:
    boundary = box(0.0, 0.0, extent_m, extent_m)
    rivers = river_union_geometry(river_areas)
    roads = vehicular_corridor_union(road_network)
    buildable = boundary
    if not getattr(rivers, 'is_empty', True):
        buildable = buildable.difference(rivers)
    if not getattr(roads, 'is_empty', True):
        buildable = buildable.difference(roads)

    macro_blocks: List[Polygon] = []
    for poly in _iter_polygons(buildable):
        if poly.area < 2_000.0:
            continue
        cleaned = poly.buffer(0)
        if cleaned.is_empty:
            continue
        if isinstance(cleaned, Polygon):
            candidates = [cleaned]
        else:
            candidates = [g for g in getattr(cleaned, 'geoms', []) if isinstance(g, Polygon)]
        for c in candidates:
            if c.area < 2_000.0:
                continue
            minx, miny, maxx, maxy = c.bounds
            if (maxx - minx) < 20 or (maxy - miny) < 20:
                continue
            macro_blocks.append(c)

    macro_blocks.sort(key=lambda p: p.area, reverse=True)
    return BlockExtractionResult(
        boundary=boundary,
        river_union=rivers,
        vehicular_corridor_union=roads,
        macro_blocks=macro_blocks,
    )
