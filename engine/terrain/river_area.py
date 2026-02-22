from __future__ import annotations

from math import hypot, log
from typing import Dict, List, Sequence, Tuple

from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from engine.models import Point2D, RiverAreaPolygon


def _river_points(river: object) -> Tuple[List[Tuple[float, float]], float, float, str]:
    if isinstance(river, dict):
        pts_raw = river.get('points', [])
        flow = float(river.get('flow', 0.0))
        length_m = float(river.get('length_m', 0.0))
        rid = str(river.get('id', 'river'))
    else:
        pts_raw = getattr(river, 'points', [])
        flow = float(getattr(river, 'flow', 0.0))
        length_m = float(getattr(river, 'length_m', 0.0))
        rid = str(getattr(river, 'id', 'river'))
    pts = []
    for p in pts_raw:
        x = p.get('x') if isinstance(p, dict) else getattr(p, 'x', None)
        y = p.get('y') if isinstance(p, dict) else getattr(p, 'y', None)
        if x is None or y is None:
            continue
        pts.append((float(x), float(y)))
    return pts, flow, length_m, rid


def _span_score(points: Sequence[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    return float(hypot(dx, dy))


def select_primary_rivers(river_polylines: Sequence[object], max_branches: int = 2) -> List[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    for river in river_polylines:
        pts, flow, length_m, rid = _river_points(river)
        if len(pts) < 2:
            continue
        candidates.append({'id': rid, 'points': pts, 'flow': flow, 'length_m': length_m, 'span_score': _span_score(pts)})
    if not candidates:
        return []
    candidates.sort(key=lambda r: (float(r['flow']), float(r['length_m']), float(r.get('span_score', 0.0))), reverse=True)
    main = candidates[0]
    selected = [dict(main, is_main_stem=True)]
    for r in candidates[1:]:
        if len(selected) >= 1 + max_branches:
            break
        if float(r['length_m']) < max(120.0, float(main['length_m']) * 0.2):
            continue
        if float(r['flow']) < max(2.0, float(main['flow']) * 0.06):
            continue
        selected.append(dict(r, is_main_stem=False))
    return selected


def _width_for_flow(flow: float, is_main: bool, width_scale: float = 1.0) -> float:
    if is_main:
        width = 22.0 + 8.5 * log(1.0 + max(flow, 0.0))
        width = float(min(48.0, max(22.0, width)))
        return float(max(6.0, width * max(width_scale, 0.05)))
    width = 8.0 + 4.2 * log(1.0 + max(flow, 0.0))
    width = float(min(20.0, max(8.0, width)))
    return float(max(3.0, width * max(width_scale, 0.05)))


def _polygon_to_model(
    poly: Polygon,
    idx: int,
    flow: float,
    width_m: float,
    is_main: bool,
    source_river_id: str | None = None,
) -> RiverAreaPolygon:
    coords = list(poly.exterior.coords)
    points = [Point2D(x=float(x), y=float(y)) for x, y in coords[:-1]]
    return RiverAreaPolygon(
        id=f'river-area-{idx}',
        points=points,
        flow=float(flow),
        width_mean_m=float(width_m),
        is_main_stem=bool(is_main),
        source_river_id=source_river_id,
    )


def build_river_area_polygons(
    river_polylines: Sequence[object],
    max_branches: int = 2,
    width_scale: float = 1.0,
    clip_extent_m: float | None = None,
    min_area_m2: float = 1.0,
    return_meta: bool = False,
) -> Tuple[List[Dict[str, object]], List[RiverAreaPolygon]] | Tuple[List[Dict[str, object]], List[RiverAreaPolygon], Dict[str, float]]:
    selected = select_primary_rivers(river_polylines, max_branches=max_branches)
    if not selected:
        if return_meta:
            return [], [], {"pre_clip_area_m2": 0.0, "post_clip_area_m2": 0.0}
        return [], []

    buffers = []
    sources = []
    for item in selected:
        line = LineString(item['points'])
        width_m = _width_for_flow(float(item['flow']), bool(item.get('is_main_stem', False)), width_scale=width_scale)
        geom = line.buffer(width_m / 2.0, cap_style=1, join_style=1, resolution=8)
        if geom.is_empty:
            continue
        buffers.append(geom)
        sources.append((line, float(item['flow']), width_m, bool(item.get('is_main_stem', False)), str(item.get('id', 'river'))))

    if not buffers:
        if return_meta:
            return selected, [], {"pre_clip_area_m2": 0.0, "post_clip_area_m2": 0.0}
        return selected, []

    merged = unary_union(buffers)
    pre_clip_area_m2 = float(getattr(merged, "area", 0.0) or 0.0)
    if clip_extent_m is not None:
        boundary = box(0.0, 0.0, float(clip_extent_m), float(clip_extent_m))
        if not getattr(merged, "is_valid", True):
            merged = merged.buffer(0)
        merged = merged.intersection(boundary)
        if not getattr(merged, "is_valid", True):
            merged = merged.buffer(0)
    post_clip_area_m2 = float(getattr(merged, "area", 0.0) or 0.0)
    polygons: List[Polygon] = []
    if isinstance(merged, Polygon):
        polygons = [merged]
    elif isinstance(merged, MultiPolygon):
        polygons = list(merged.geoms)
    else:
        # GeometryCollection fallback: keep polygon pieces only
        polygons = [g for g in getattr(merged, 'geoms', []) if isinstance(g, Polygon)]

    out: List[RiverAreaPolygon] = []
    for poly in polygons:
        if poly.area <= float(min_area_m2):
            continue
        c = poly.representative_point()
        best = None
        for line, flow, width_m, is_main, rid in sources:
            d = line.distance(c)
            if best is None or d < best[0]:
                best = (d, flow, width_m, is_main, rid)
        if best is None:
            continue
        _, flow, width_m, is_main, rid = best
        out.append(_polygon_to_model(poly, len(out), flow, width_m, is_main, source_river_id=rid))

    if return_meta:
        return selected, out, {"pre_clip_area_m2": pre_clip_area_m2, "post_clip_area_m2": post_clip_area_m2}
    return selected, out
