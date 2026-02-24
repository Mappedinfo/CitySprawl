from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees
from typing import Iterable, List, Sequence, Tuple

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import split, unary_union

from engine.models import PedestrianPath


@dataclass
class ParcelizationResult:
    pedestrian_paths: List[PedestrianPath]
    pedestrian_corridors_by_block: List[object]
    parcel_polygons_by_block: List[List[Polygon]]


@dataclass
class FrontageParcelConfig:
    residential_target_area_m2: float = 1800.0
    mixed_target_area_m2: float = 2600.0
    min_frontage_m: float = 10.0
    min_depth_m: float = 12.0
    parcel_local_morphology_coupling: bool = True
    parcel_culdesac_frontage_relaxation: float = 0.18
    parcel_local_depth_bias: float = 0.10
    parcel_curvilinear_split_bias: float = 0.20
    seed: int = 0


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
        min_dim0, max_dim0 = _oriented_bbox_dims(block)
        aspect0 = max_dim0 / max(min_dim0, 1e-9)
        if aspect0 > 12.0 or max_dim0 > 1_800.0:
            # Defensive guard: extraction should prevent this, but do not amplify pathological superblocks.
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


def _bbox_dims(poly: Polygon) -> Tuple[float, float]:
    minx, miny, maxx, maxy = poly.bounds
    return float(maxx - minx), float(maxy - miny)


def _oriented_bbox_dims(poly: Polygon) -> Tuple[float, float]:
    angle = _dominant_angle_deg(poly)
    try:
        rot = affinity.rotate(poly, -angle, origin='centroid')
    except Exception:
        w, h = _bbox_dims(poly)
        return min(w, h), max(w, h)
    minx, miny, maxx, maxy = rot.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    return min(w, h), max(w, h)


def _split_pathological_parcel(
    poly: Polygon,
    *,
    max_aspect_ratio: float = 35.0,
    min_guard_area_m2: float = 1_000.0,
    min_piece_area_m2: float = 180.0,
    depth: int = 0,
    max_depth: int = 4,
) -> List[Polygon]:
    """Last-resort splitter for rare elongated strips that survive parcelization."""
    if getattr(poly, "is_empty", True):
        return []
    poly = poly.buffer(0)
    if poly.is_empty:
        return []
    if not isinstance(poly, Polygon):
        out: List[Polygon] = []
        for g in getattr(poly, "geoms", []):
            if isinstance(g, Polygon):
                out.extend(
                    _split_pathological_parcel(
                        g,
                        max_aspect_ratio=max_aspect_ratio,
                        min_guard_area_m2=min_guard_area_m2,
                        min_piece_area_m2=min_piece_area_m2,
                        depth=depth,
                        max_depth=max_depth,
                    )
                )
        return out

    if depth >= max_depth or float(poly.area) <= float(min_guard_area_m2):
        return [poly]

    w0, h0 = _bbox_dims(poly)
    axis_aspect = max(w0, h0) / max(min(w0, h0), 1e-9)
    # Fast path: only rare axis-aligned strips need the expensive oriented-rectangle work.
    if axis_aspect <= float(max_aspect_ratio):
        return [poly]

    angle = _dominant_angle_deg(poly)
    try:
        rot = affinity.rotate(poly, -angle, origin='centroid')
    except Exception:
        return [poly]
    minx, miny, maxx, maxy = rot.bounds
    w = float(maxx - minx)
    h = float(maxy - miny)
    if min(w, h) <= 1e-6:
        return [poly]

    # Split along the elongated axis midpoint (perpendicular cut) to reduce strip aspect ratio quickly.
    if w >= h:
        x = minx + 0.5 * w
        cutter = LineString([(x, miny - 10.0), (x, maxy + 10.0)])
    else:
        y = miny + 0.5 * h
        cutter = LineString([(minx - 10.0, y), (maxx + 10.0, y)])

    try:
        pieces_rot = split(rot, cutter)
    except Exception:
        return [poly]

    pieces: List[Polygon] = []
    for g in getattr(pieces_rot, "geoms", [pieces_rot]):
        if not isinstance(g, Polygon):
            continue
        world = affinity.rotate(g, angle, origin=poly.centroid).buffer(0)
        if world.is_empty:
            continue
        if isinstance(world, Polygon):
            pieces.append(world)
        else:
            pieces.extend([p for p in getattr(world, "geoms", []) if isinstance(p, Polygon)])
    if len(pieces) < 2:
        return [poly]

    out: List[Polygon] = []
    for piece in pieces:
        if float(piece.area) < float(min_piece_area_m2):
            continue
        out.extend(
            _split_pathological_parcel(
                piece,
                max_aspect_ratio=max_aspect_ratio,
                min_guard_area_m2=min_guard_area_m2,
                min_piece_area_m2=min_piece_area_m2,
                depth=depth + 1,
                max_depth=max_depth,
            )
        )
    return out or [poly]


def _recursive_split_polygon(
    poly: Polygon,
    *,
    target_area_m2: float,
    min_frontage_m: float,
    min_depth_m: float,
    rng_seed: int,
    curvilinear_split_bias: float = 0.0,
    depth: int = 0,
    max_depth: int = 7,
) -> List[Polygon]:
    if poly.is_empty:
        return []
    poly = poly.buffer(0)
    if poly.is_empty or not isinstance(poly, Polygon):
        return [g for g in getattr(poly, 'geoms', []) if isinstance(g, Polygon)]

    area = float(poly.area)
    w0, h0 = _bbox_dims(poly)
    if depth >= max_depth or area <= max(target_area_m2 * 1.35, 450.0):
        if min(w0, h0) < min(min_frontage_m, min_depth_m) * 0.7:
            return []
        return [poly]
    if min(w0, h0) < min(min_frontage_m, min_depth_m) * 1.2:
        return [poly]

    angle = _dominant_angle_deg(poly)
    rot = affinity.rotate(poly, -angle, origin='centroid')
    minx, miny, maxx, maxy = rot.bounds
    w = maxx - minx
    h = maxy - miny
    if min(w, h) < min(min_frontage_m, min_depth_m) * 1.2:
        return [poly]

    split_along_x = w >= h
    if curvilinear_split_bias > 0.0 and abs(w - h) / max(max(w, h), 1e-6) < 0.35:
        # Curvilinear neighborhoods tend to produce less axis-stable lots; allow more varied split axes.
        parity = ((rng_seed + depth * 17) % 7) / 7.0
        if parity < min(0.95, float(curvilinear_split_bias)):
            split_along_x = not split_along_x
    # Deterministic pseudo-random jitter without introducing a global RNG dependency.
    jitter_span = 0.12 + 0.12 * max(0.0, min(1.0, float(curvilinear_split_bias)))
    jitter = ((((rng_seed + depth * 131) % 997) / 997.0) - 0.5) * jitter_span
    t = max(0.35, min(0.65, 0.5 + jitter))
    if split_along_x:
        x = minx + t * w
        cutter = LineString([(x, miny - 10.0), (x, maxy + 10.0)])
    else:
        y = miny + t * h
        cutter = LineString([(minx - 10.0, y), (maxx + 10.0, y)])

    try:
        pieces_rot = split(rot, cutter)
    except Exception:
        return [poly]
    pieces_world = []
    for geom in getattr(pieces_rot, 'geoms', [pieces_rot]):
        if not isinstance(geom, Polygon):
            continue
        world = affinity.rotate(geom, angle, origin=poly.centroid).buffer(0)
        if world.is_empty:
            continue
        if isinstance(world, Polygon):
            pieces_world.append(world)
        else:
            pieces_world.extend([g for g in getattr(world, 'geoms', []) if isinstance(g, Polygon)])
    if len(pieces_world) < 2:
        return [poly]

    out: List[Polygon] = []
    for idx, piece in enumerate(sorted(pieces_world, key=lambda p: float(p.area), reverse=True)):
        pw, ph = _bbox_dims(piece)
        if piece.area < 120.0 or min(pw, ph) < min(min_frontage_m, min_depth_m) * 0.55:
            continue
        out.extend(
            _recursive_split_polygon(
                piece,
                target_area_m2=target_area_m2,
                min_frontage_m=min_frontage_m,
                min_depth_m=min_depth_m,
                rng_seed=rng_seed + idx * 17 + depth * 101,
                curvilinear_split_bias=curvilinear_split_bias,
                depth=depth + 1,
                max_depth=max_depth,
            )
        )
    return out or [poly]


def _road_network_local_context(road_network: object | None):
    if road_network is None:
        return {"local_cul_endpoints": [], "local_lines": [], "collector_lines": []}
    node_lookup = {getattr(n, "id"): n for n in getattr(road_network, "nodes", [])}
    local_cul_endpoints = []
    local_lines = []
    collector_lines = []
    for e in getattr(road_network, "edges", []):
        rc = str(getattr(e, "road_class", ""))
        path = getattr(e, "path_points", None)
        coords = []
        if path and len(path) >= 2:
            coords = [(float(p.x if hasattr(p, "x") else p["x"]), float(p.y if hasattr(p, "y") else p["y"])) for p in path]
        else:
            u = node_lookup.get(getattr(e, "u"))
            v = node_lookup.get(getattr(e, "v"))
            if u is not None and v is not None and hasattr(u, "x") and hasattr(v, "x"):
                coords = [(float(u.x), float(u.y)), (float(v.x), float(v.y))]
            elif u is not None and hasattr(u, "pos") and v is not None and hasattr(v, "pos"):
                coords = [(float(u.pos.x), float(u.pos.y)), (float(v.pos.x), float(v.pos.y))]
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if line.length <= 1e-6:
            continue
        if rc == "local":
            local_lines.append(line)
            if "-cul" in str(getattr(e, "id", "")):
                local_cul_endpoints.append(line.coords[0])
                local_cul_endpoints.append(line.coords[-1])
        elif rc == "collector":
            collector_lines.append(line)
    return {
        "local_cul_endpoints": local_cul_endpoints,
        "local_lines": local_lines,
        "collector_lines": collector_lines,
    }


def generate_frontage_parcels(
    macro_blocks: Sequence[Polygon],
    road_network: object | None = None,
    river_areas: Sequence[object] | None = None,
    pedestrian_width_m: float = 3.0,
    config: FrontageParcelConfig | None = None,
) -> ParcelizationResult:
    _ = river_areas
    cfg = config or FrontageParcelConfig()
    local_ctx = _road_network_local_context(road_network)

    base = generate_pedestrian_paths_and_parcels(
        macro_blocks=macro_blocks,
        pedestrian_width_m=pedestrian_width_m,
    )
    refined_by_block: List[List[Polygon]] = []
    for block_idx, base_parcels in enumerate(base.parcel_polygons_by_block):
        block = macro_blocks[block_idx] if block_idx < len(macro_blocks) else None
        block_area = float(getattr(block, "area", 0.0) or 0.0)
        target_area = float(cfg.mixed_target_area_m2 if block_area >= 40_000.0 else cfg.residential_target_area_m2)
        block_frontage = float(cfg.min_frontage_m)
        block_depth = float(cfg.min_depth_m)
        block_curvy_bias = 0.0
        if bool(getattr(cfg, "parcel_local_morphology_coupling", True)) and block is not None:
            centroid = block.representative_point()
            cx, cy = float(centroid.x), float(centroid.y)
            cul_pts = local_ctx.get("local_cul_endpoints", [])
            local_lines = local_ctx.get("local_lines", [])
            collector_lines = local_ctx.get("collector_lines", [])
            if cul_pts:
                d_cul = min((((cx - px) ** 2 + (cy - py) ** 2) ** 0.5) for px, py in cul_pts)
                if d_cul < 140.0:
                    relax = float(max(0.0, min(0.8, cfg.parcel_culdesac_frontage_relaxation)))
                    block_frontage *= (1.0 - 0.35 * relax)
                    block_depth *= (1.0 - 0.20 * relax)
                    target_area *= (1.0 + 0.28 * relax)
                    block_curvy_bias = max(block_curvy_bias, 0.25 + 0.5 * relax)
            if local_lines:
                pt = centroid
                d_local = min(float(line.distance(pt)) for line in local_lines)
                if d_local < 90.0:
                    depth_bias = float(max(-0.5, min(1.0, cfg.parcel_local_depth_bias)))
                    block_depth *= (1.0 - 0.18 * depth_bias)
                    block_curvy_bias = max(block_curvy_bias, 0.15)
            if collector_lines:
                pt = centroid
                d_col = min(float(line.distance(pt)) for line in collector_lines)
                if d_col < 70.0:
                    target_area *= 1.08
            block_curvy_bias = max(block_curvy_bias, float(max(0.0, min(1.0, cfg.parcel_curvilinear_split_bias))) * block_curvy_bias)
        refined: List[Polygon] = []
        for parcel_idx, poly in enumerate(base_parcels):
            splits = _recursive_split_polygon(
                poly,
                target_area_m2=target_area,
                min_frontage_m=block_frontage,
                min_depth_m=block_depth,
                rng_seed=int(cfg.seed + block_idx * 1009 + parcel_idx * 97),
                curvilinear_split_bias=block_curvy_bias,
            )
            parcel_refined: List[Polygon] = []
            revert_to_base = False
            for piece in splits:
                if piece.area < 180.0:
                    continue
                w, h = _bbox_dims(piece)
                if min(w, h) < min(block_frontage, block_depth) * 0.5:
                    continue
                if piece.area > 1_000.0:
                    axis_aspect = max(w, h) / max(min(w, h), 1e-9)
                    paspect = axis_aspect
                    if axis_aspect > 20.0:
                        pmin, pmax = _oriented_bbox_dims(piece)
                        paspect = pmax / max(pmin, 1e-9)
                    if paspect > 35.0 or axis_aspect > 35.0:
                        revert_to_base = True
                        break
                parcel_refined.append(piece)
            if revert_to_base:
                refined.append(poly)
            elif parcel_refined:
                refined.extend(parcel_refined)
            else:
                refined.append(poly)
        sanitized: List[Polygon] = []
        for poly in (refined or list(base_parcels)):
            if poly.area <= 1_000.0:
                sanitized.append(poly)
                continue
            w0, h0 = _bbox_dims(poly)
            axis_aspect0 = max(w0, h0) / max(min(w0, h0), 1e-9)
            if axis_aspect0 <= 35.0:
                sanitized.append(poly)
                continue
            for piece in _split_pathological_parcel(poly):
                if piece.area < 180.0:
                    continue
                w, h = _bbox_dims(piece)
                if min(w, h) < min(block_frontage, block_depth) * 0.45:
                    continue
                sanitized.append(piece)
        refined_by_block.append(sanitized or refined or list(base_parcels))

    return ParcelizationResult(
        pedestrian_paths=list(base.pedestrian_paths),
        pedestrian_corridors_by_block=list(base.pedestrian_corridors_by_block),
        parcel_polygons_by_block=refined_by_block,
    )
