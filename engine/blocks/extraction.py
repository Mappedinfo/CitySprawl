from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot
from typing import Iterable, List, Sequence

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import polygonize, snap, split, unary_union

from engine.models import RiverAreaPolygon, RoadNetwork


@dataclass
class BlockExtractionConfig:
    min_block_area_m2: float = 2_000.0
    min_block_width_m: float = 20.0
    max_block_span_m: float = 700.0
    max_block_aspect_ratio: float = 8.0
    max_block_area_m2: float = 180_000.0
    split_max_depth: int = 8
    split_jitter: float = 0.06
    topology_snap_tol_m: float = 0.5


@dataclass
class BlockExtractionResult:
    boundary: Polygon
    river_union: Polygon | MultiPolygon | object
    vehicular_corridor_union: Polygon | MultiPolygon | object
    macro_blocks: List[Polygon]
    topology_block_count: int = 0
    fallback_block_count: int = 0


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
        coords = _dedupe_consecutive_coords(coords)
        if len(coords) >= 2:
            return coords
    u = node_lookup.get(edge.u)
    v = node_lookup.get(edge.v)
    if u is None or v is None:
        return None
    return _dedupe_consecutive_coords([(float(u.x), float(u.y)), (float(v.x), float(v.y))])


def vehicular_corridor_union(road_network: RoadNetwork):
    node_lookup = {n.id: n for n in road_network.nodes}
    geoms = []
    for edge in road_network.edges:
        if edge.road_class not in ("arterial", "collector", "local"):
            continue
        coords = _edge_coords(edge, node_lookup)
        if coords is None:
            continue
        width_m = float(getattr(edge, "width_m", 8.0) or 8.0)
        if width_m <= 0.0:
            continue
        line = LineString(coords)
        if line.is_empty or line.length <= 1e-6:
            continue
        geoms.append(line.buffer(width_m / 2.0, cap_style=2, join_style=2, resolution=4))
    if not geoms:
        return Polygon()
    return unary_union(geoms)


def _iter_polygons(geom) -> Iterable[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def _iter_lines(geom) -> Iterable[LineString]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, LineString)]


def _dedupe_consecutive_coords(coords: Sequence[tuple[float, float]]) -> List[tuple[float, float]]:
    out: List[tuple[float, float]] = []
    for x, y in coords:
        if out and abs(out[-1][0] - x) <= 1e-9 and abs(out[-1][1] - y) <= 1e-9:
            continue
        out.append((float(x), float(y)))
    return out


def _ring_line(ring) -> LineString | None:
    coords = _dedupe_consecutive_coords(list(getattr(ring, "coords", [])))
    if len(coords) < 2:
        return None
    line = LineString(coords)
    if line.is_empty or line.length <= 1e-6:
        return None
    return line


def _oriented_bbox_metrics(poly: Polygon) -> tuple[float, float, float, float]:
    try:
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords) if isinstance(mrr, Polygon) else []
    except Exception:
        coords = []
    dims: List[float] = []
    if len(coords) >= 5:
        for i in range(4):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            d = hypot(x1 - x0, y1 - y0)
            if d > 1e-9:
                dims.append(float(d))
    if len(dims) < 2:
        minx, miny, maxx, maxy = poly.bounds
        dims = [float(maxx - minx), float(maxy - miny)]
    d_max = max(dims) if dims else 0.0
    nonzero = [d for d in dims if d > 1e-9]
    d_min = min(nonzero) if nonzero else 0.0
    aspect = (d_max / max(d_min, 1e-9)) if d_max > 0 else 1.0
    return float(d_min), float(d_max), float(aspect), float(d_max)


def _dominant_angle_deg(poly: Polygon) -> float:
    try:
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords) if isinstance(mrr, Polygon) else []
    except Exception:
        coords = []
    if len(coords) < 5:
        minx, miny, maxx, maxy = poly.bounds
        return 0.0 if (maxx - minx) >= (maxy - miny) else 90.0
    best_len = -1.0
    best_angle = 0.0
    for i in range(4):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        length2 = dx * dx + dy * dy
        if length2 > best_len:
            best_len = length2
            best_angle = degrees(atan2(dy, dx))
    return float(best_angle)


def _poly_touches_outer_boundary(poly: Polygon, boundary: Polygon) -> bool:
    try:
        return bool(poly.boundary.intersects(boundary.boundary))
    except Exception:
        return False


def _explode_holey_polygon(poly: Polygon) -> List[Polygon]:
    if poly.is_empty:
        return []
    if len(poly.interiors) == 0:
        return [poly]
    lines: List[LineString] = []
    shell_line = _ring_line(poly.exterior)
    if shell_line is not None:
        lines.append(shell_line)
    for ring in poly.interiors:
        line = _ring_line(ring)
        if line is not None:
            lines.append(line)
    if not lines:
        return []
    pieces: List[Polygon] = []
    try:
        for candidate in polygonize(unary_union(lines)):
            if candidate.is_empty or candidate.area <= 1e-9:
                continue
            rep = candidate.representative_point()
            if poly.covers(rep):
                pieces.append(candidate.buffer(0))
    except Exception:
        return [poly]
    out = [p for p in pieces if isinstance(p, Polygon) and not p.is_empty and p.area > 1e-9]
    return out or [poly]


def _clean_and_filter_polygons(
    geom,
    cfg: BlockExtractionConfig,
    *,
    allow_boundary_touch: bool,
    boundary: Polygon,
) -> List[Polygon]:
    out: List[Polygon] = []
    for poly in _iter_polygons(geom):
        try:
            cleaned = poly.buffer(0)
        except Exception:
            cleaned = poly
        for c in _iter_polygons(cleaned):
            for piece in _explode_holey_polygon(c):
                if piece.is_empty:
                    continue
                try:
                    piece = piece.buffer(0)
                except Exception:
                    pass
                if not isinstance(piece, Polygon) or piece.is_empty:
                    continue
                if float(piece.area) < float(cfg.min_block_area_m2):
                    continue
                min_dim, _, _, _ = _oriented_bbox_metrics(piece)
                if min_dim < float(cfg.min_block_width_m):
                    continue
                if not allow_boundary_touch and _poly_touches_outer_boundary(piece, boundary):
                    continue
                out.append(piece)
    return out


def _road_centerline_linework(road_network: RoadNetwork) -> List[LineString]:
    node_lookup = {n.id: n for n in road_network.nodes}
    lines: List[LineString] = []
    for edge in road_network.edges:
        if edge.road_class not in ("arterial", "collector", "local"):
            continue
        coords = _edge_coords(edge, node_lookup)
        if coords is None or len(coords) < 2:
            continue
        line = LineString(coords)
        if line.is_empty or line.length <= 1e-6:
            continue
        lines.append(line)
    return lines


def _boundary_linework(boundary: Polygon, river_union) -> List[LineString]:
    lines: List[LineString] = []
    shell = _ring_line(boundary.exterior)
    if shell is not None:
        lines.append(shell)
    for poly in _iter_polygons(river_union):
        ext = _ring_line(poly.exterior)
        if ext is not None:
            lines.append(ext)
        for ring in poly.interiors:
            inner = _ring_line(ring)
            if inner is not None:
                lines.append(inner)
    return lines


def _extract_topology_blocks(
    *,
    boundary: Polygon,
    buildable,
    road_network: RoadNetwork,
    river_union,
    cfg: BlockExtractionConfig,
) -> List[Polygon]:
    road_lines = _road_centerline_linework(road_network)
    if not road_lines:
        return []
    linework = list(road_lines) + _boundary_linework(boundary, river_union)
    if not linework:
        return []
    try:
        noded = unary_union(linework)
        tol = float(max(0.0, cfg.topology_snap_tol_m))
        if tol > 0.0:
            noded = snap(noded, noded, tol)
    except Exception:
        return []

    out: List[Polygon] = []
    for face in polygonize(noded):
        if face.is_empty or float(face.area) < float(cfg.min_block_area_m2):
            continue
        rep = face.representative_point()
        if not boundary.covers(rep):
            continue
        if not getattr(river_union, "is_empty", True) and river_union.covers(rep):
            continue
        try:
            clipped = face.intersection(buildable).buffer(0)
        except Exception:
            continue
        out.extend(_clean_and_filter_polygons(clipped, cfg, allow_boundary_touch=False, boundary=boundary))
    return _dedupe_polygons(out)


def _split_ratio_for(poly: Polygon, depth: int, cfg: BlockExtractionConfig) -> float:
    jitter = max(0.0, min(0.2, float(cfg.split_jitter)))
    if jitter <= 1e-9:
        return 0.5
    c = poly.centroid
    seed = (
        int(round(float(poly.area) * 0.01))
        + int(round(float(c.x) * 0.13)) * 17
        + int(round(float(c.y) * 0.11)) * 31
        + depth * 97
    )
    frac = ((seed % 997) / 997.0) - 0.5
    t = 0.5 + frac * 2.0 * jitter
    return float(max(0.44, min(0.56, t)))


def _split_polygon_once(poly: Polygon, cfg: BlockExtractionConfig, depth: int) -> List[Polygon]:
    angle = _dominant_angle_deg(poly)
    try:
        rot = affinity.rotate(poly, -angle, origin="centroid")
    except Exception:
        return [poly]
    minx, miny, maxx, maxy = rot.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    if min(w, h) <= max(float(cfg.min_block_width_m) * 1.1, 1.0):
        return [poly]

    split_along_x = w >= h
    t = _split_ratio_for(poly, depth, cfg)
    cutter_candidates: List[LineString] = []
    if split_along_x:
        x = minx + t * w
        cutter_candidates.append(LineString([(x, miny - 10.0), (x, maxy + 10.0)]))
        y = miny + t * h
        cutter_candidates.append(LineString([(minx - 10.0, y), (maxx + 10.0, y)]))
    else:
        y = miny + t * h
        cutter_candidates.append(LineString([(minx - 10.0, y), (maxx + 10.0, y)]))
        x = minx + t * w
        cutter_candidates.append(LineString([(x, miny - 10.0), (x, maxy + 10.0)]))

    for cutter in cutter_candidates:
        try:
            pieces_rot = split(rot, cutter)
        except Exception:
            continue
        pieces_world: List[Polygon] = []
        for geom in getattr(pieces_rot, "geoms", [pieces_rot]):
            if not isinstance(geom, Polygon) or geom.is_empty:
                continue
            try:
                world = affinity.rotate(geom, angle, origin=poly.centroid).buffer(0)
            except Exception:
                continue
            for p in _iter_polygons(world):
                if not p.is_empty and float(p.area) > 1e-6:
                    pieces_world.append(p)
        if len(pieces_world) >= 2:
            return pieces_world
    return [poly]


def _needs_superblock_split(poly: Polygon, cfg: BlockExtractionConfig) -> bool:
    min_dim, max_dim, aspect, _ = _oriented_bbox_metrics(poly)
    if float(poly.area) > float(cfg.max_block_area_m2):
        return True
    if max_dim > float(cfg.max_block_span_m):
        return True
    if aspect > float(cfg.max_block_aspect_ratio):
        return True
    if min_dim < float(cfg.min_block_width_m):
        return False
    return False


def _split_residual_superblock(poly: Polygon, cfg: BlockExtractionConfig, depth: int = 0) -> List[Polygon]:
    if poly.is_empty:
        return []
    try:
        poly = poly.buffer(0)
    except Exception:
        pass
    out: List[Polygon] = []
    for sub in _iter_polygons(poly):
        if sub.is_empty:
            continue
        for simple in _explode_holey_polygon(sub):
            if simple.is_empty:
                continue
            area = float(simple.area)
            min_dim, _, _, _ = _oriented_bbox_metrics(simple)
            if area < float(cfg.min_block_area_m2) or min_dim < float(cfg.min_block_width_m):
                continue
            if depth >= int(cfg.split_max_depth) or not _needs_superblock_split(simple, cfg):
                out.append(simple)
                continue
            pieces = _split_polygon_once(simple, cfg, depth)
            if len(pieces) < 2:
                out.append(simple)
                continue
            for piece in pieces:
                out.extend(_split_residual_superblock(piece, cfg, depth=depth + 1))
    return out


def _dedupe_polygons(polys: Sequence[Polygon]) -> List[Polygon]:
    kept: List[Polygon] = []
    for poly in polys:
        area = float(poly.area)
        if area <= 1e-9:
            continue
        duplicate = False
        for ref in kept:
            ra = float(ref.area)
            if abs(ra - area) / max(area, ra, 1e-9) > 0.02:
                continue
            minx0, miny0, maxx0, maxy0 = poly.bounds
            minx1, miny1, maxx1, maxy1 = ref.bounds
            if maxx0 < minx1 or maxx1 < minx0 or maxy0 < miny1 or maxy1 < miny0:
                continue
            try:
                inter_area = float(poly.intersection(ref).area)
            except Exception:
                inter_area = 0.0
            if inter_area / max(min(area, ra), 1e-9) > 0.995:
                duplicate = True
                break
        if not duplicate:
            kept.append(poly)
    return kept


def extract_macro_blocks(
    extent_m: float,
    road_network: RoadNetwork,
    river_areas: Sequence[RiverAreaPolygon],
    config: BlockExtractionConfig | None = None,
) -> BlockExtractionResult:
    cfg = config or BlockExtractionConfig()

    boundary = box(0.0, 0.0, extent_m, extent_m)
    rivers = river_union_geometry(river_areas)
    roads = vehicular_corridor_union(road_network)

    buildable = boundary
    if not getattr(rivers, "is_empty", True):
        buildable = buildable.difference(rivers)
    if not getattr(roads, "is_empty", True):
        buildable = buildable.difference(roads)
    try:
        buildable = buildable.buffer(0)
    except Exception:
        pass

    topology_blocks = _extract_topology_blocks(
        boundary=boundary,
        buildable=buildable,
        road_network=road_network,
        river_union=rivers,
        cfg=cfg,
    )

    accepted_topology_union = unary_union(topology_blocks) if topology_blocks else Polygon()
    try:
        residual = buildable.difference(accepted_topology_union).buffer(0)
    except Exception:
        residual = buildable

    fallback_raw: List[Polygon] = []
    for poly in _iter_polygons(residual):
        fallback_raw.extend(_split_residual_superblock(poly, cfg, depth=0))
    fallback_blocks: List[Polygon] = []
    for poly in fallback_raw:
        fallback_blocks.extend(
            _clean_and_filter_polygons(
                poly,
                cfg,
                allow_boundary_touch=True,
                boundary=boundary,
            )
        )
    fallback_blocks = _dedupe_polygons(fallback_blocks)

    macro_blocks = _dedupe_polygons([*topology_blocks, *fallback_blocks])
    macro_blocks.sort(key=lambda p: float(p.area), reverse=True)

    return BlockExtractionResult(
        boundary=boundary,
        river_union=rivers,
        vehicular_corridor_union=roads,
        macro_blocks=macro_blocks,
        topology_block_count=int(len(topology_blocks)),
        fallback_block_count=int(len(fallback_blocks)),
    )
