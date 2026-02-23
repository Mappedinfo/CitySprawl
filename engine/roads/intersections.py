from __future__ import annotations

from collections import defaultdict
from math import hypot
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from engine.core.geometry import Segment, Vec2, point_segment_distance, project_point_to_segment, segment_intersection
from engine.core.spatial import SpatialHashIndex


def _edge_flags(edge: object) -> frozenset[str]:
    flags = getattr(edge, "flags", None)
    if flags is None:
        if "-cul" in str(getattr(edge, "id", "")):
            return frozenset({"culdesac"})
        return frozenset()
    try:
        out = frozenset(str(v) for v in flags if v)
    except Exception:
        out = frozenset()
    if not out and "-cul" in str(getattr(edge, "id", "")):
        return frozenset({"culdesac"})
    return out


def _edge_id_with_flags(base_id: str, flags: frozenset[str]) -> str:
    sid = str(base_id)
    if "culdesac" in flags and "-cul" not in sid:
        return f"{sid}-cul"
    return sid


def _rebuild_edge_like(
    edge: object,
    *,
    path_points=None,
    edge_id: Optional[str] = None,
    u: Optional[str] = None,
    v: Optional[str] = None,
) -> object:
    cls = edge.__class__
    flags = _edge_flags(edge)
    kwargs = dict(
        id=_edge_id_with_flags(edge_id if edge_id is not None else getattr(edge, "id"), flags),
        u=u if u is not None else getattr(edge, "u"),
        v=v if v is not None else getattr(edge, "v"),
        road_class=getattr(edge, "road_class"),
        weight=float(getattr(edge, "weight")),
        length_m=float(getattr(edge, "length_m")),
        river_crossings=int(getattr(edge, "river_crossings")),
        width_m=float(getattr(edge, "width_m")),
        render_order=int(getattr(edge, "render_order")),
        path_points=path_points if path_points is not None else getattr(edge, "path_points"),
    )
    try:
        return cls(flags=flags, **kwargs)
    except TypeError:
        return cls(**kwargs)


def _edge_points(edge: object, node_lookup: dict[str, object]) -> list[Vec2]:
    path = getattr(edge, "path_points", None)
    if path and len(path) >= 2:
        out: list[Vec2] = []
        for p in path:
            x = p.x if hasattr(p, "x") else p["x"]
            y = p.y if hasattr(p, "y") else p["y"]
            out.append(Vec2(float(x), float(y)))
        if len(out) >= 2:
            return out
    u = node_lookup.get(str(getattr(edge, "u", "")))
    v = node_lookup.get(str(getattr(edge, "v", "")))
    if u is None or v is None:
        return []
    pu = getattr(u, "pos")
    pv = getattr(v, "pos")
    return [Vec2(float(pu.x), float(pu.y)), Vec2(float(pv.x), float(pv.y))]


def _polyline_length(points: Sequence[Vec2]) -> float:
    return float(sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1)))


def _segmentize_polyline(points: Sequence[Vec2]) -> list[tuple[int, Segment]]:
    out: list[tuple[int, Segment]] = []
    for i in range(len(points) - 1):
        seg = Segment(points[i], points[i + 1])
        if seg.length() <= 1e-6:
            continue
        out.append((i, seg))
    return out


def _project_on_polyline(points: Sequence[Vec2], p: Vec2) -> tuple[float, Vec2, int]:
    best_dist = float("inf")
    best_proj = points[0]
    best_idx = 0
    cum_len = 0.0
    best_along = 0.0
    for i, seg in _segmentize_polyline(points):
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        if d < best_dist:
            best_dist = d
            best_proj = proj
            best_idx = i
            best_along = cum_len + seg.p0.distance_to(proj)
        cum_len += seg.length()
    return float(best_along), best_proj, int(best_idx)


def _point_near_polyline_endpoint(points: Sequence[Vec2], p: Vec2, tol: float) -> bool:
    if not points:
        return True
    return p.distance_to(points[0]) <= tol or p.distance_to(points[-1]) <= tol


def _angle_optimize_t_endpoint(
    points: list[Vec2],
    *,
    end_idx: int,
    proj: Vec2,
    target_path: Sequence[Vec2],
    target_seg_idx: int,
    t_radius_m: float,
) -> list[Vec2]:
    if len(points) < 2:
        return points
    adj_idx = 1 if end_idx == 0 else len(points) - 2
    if not (0 <= adj_idx < len(points)):
        return points
    if not (0 <= target_seg_idx < len(target_path) - 1):
        return points
    tan = (target_path[target_seg_idx + 1] - target_path[target_seg_idx]).normalized()
    if tan.length() <= 1e-9:
        return points
    normal = Vec2(-tan.y, tan.x).normalized()
    if normal.length() <= 1e-9:
        return points
    adj = points[adj_idx]
    vec = adj - proj
    if vec.length() <= 1e-6:
        return points
    # Keep the branch approach roughly perpendicular to the target segment.
    if vec.dot(normal) < 0.0:
        normal = Vec2(-normal.x, -normal.y)
    depth = max(2.0, min(float(t_radius_m) * 0.9, max(2.0, abs(vec.dot(normal)))))
    candidate = proj + normal * depth
    if candidate.distance_to(proj) <= 1.0:
        return points
    updated = list(points)
    updated[adj_idx] = candidate
    return updated


def _ensure_node_at_position(nodes: list[object], pos: Vec2, prefix: str = "inode") -> str:
    node_cls = nodes[0].__class__ if nodes else None
    if node_cls is None:
        raise ValueError("nodes list must not be empty")
    new_id = f"{prefix}-{len(nodes)}"
    nodes.append(node_cls(id=new_id, pos=pos, kind="junction", source_hub_id=None))
    return new_id


def _set_node_position(nodes: list[object], node_id: str, pos: Vec2) -> None:
    for i, n in enumerate(nodes):
        if str(getattr(n, "id", "")) != node_id:
            continue
        cls = n.__class__
        nodes[i] = cls(
            id=getattr(n, "id"),
            pos=pos,
            kind=getattr(n, "kind", "junction"),
            source_hub_id=getattr(n, "source_hub_id", None),
        )
        return


def snap_endpoints_to_nodes(
    nodes: list[object],
    edges: list[object],
    *,
    snap_radius_m: float,
) -> int:
    if snap_radius_m <= 0.0:
        return 0
    moved = 0
    node_lookup = {str(getattr(n, "id")): n for n in nodes}
    anchors = []
    for n in nodes:
        pos = getattr(n, "pos", None)
        if pos is None:
            continue
        anchors.append((str(getattr(n, "id")), Vec2(float(pos.x), float(pos.y)), str(getattr(n, "kind", ""))))

    for ei, edge in enumerate(edges):
        if str(getattr(edge, "road_class", "")) != "collector":
            continue
        path = _edge_points(edge, node_lookup)
        if len(path) < 2:
            continue
        new_path = list(path)
        u_id = str(getattr(edge, "u"))
        v_id = str(getattr(edge, "v"))
        for end_idx, node_id in ((0, u_id), (-1, v_id)):
            p = new_path[end_idx]
            candidates = sorted(
                anchors,
                key=lambda item: (
                    p.distance_to(item[1]),
                    {"arterial": 0, "hub": 0, "collector": 1, "local": 2}.get(item[2], 3),
                ),
            )
            for target_id, target_pos, _ in candidates:
                if target_id == node_id:
                    continue
                if p.distance_to(target_pos) > snap_radius_m:
                    break
                new_path[end_idx] = target_pos
                _set_node_position(nodes, node_id, target_pos)
                moved += 1
                break
        edges[ei] = _rebuild_edge_like(edge, path_points=new_path, u=u_id, v=v_id)
    return moved


def _split_edge_at_points(
    edge: object,
    points: Sequence[Vec2],
    split_points: Sequence[Vec2],
    nodes: list[object],
    *,
    split_tol_m: float,
) -> list[object]:
    if len(split_points) == 0:
        return [edge]
    try:
        from shapely.geometry import LineString, MultiPoint, Point  # type: ignore
        from shapely.ops import split as shapely_split  # type: ignore
    except Exception:
        return [edge]

    line = LineString([(p.x, p.y) for p in points])
    if line.length <= 1e-6:
        return [edge]

    uniq: list[Vec2] = []
    for p in split_points:
        if _point_near_polyline_endpoint(points, p, max(split_tol_m, 1.0)):
            continue
        if any(p.distance_to(q) <= max(split_tol_m, 0.5) for q in uniq):
            continue
        uniq.append(p)
    if not uniq:
        return [edge]

    try:
        pieces_geom = shapely_split(line, MultiPoint([(p.x, p.y) for p in uniq]))
    except Exception:
        return [edge]

    pieces = [g for g in getattr(pieces_geom, "geoms", [pieces_geom]) if getattr(g, "length", 0.0) > max(split_tol_m, 0.5)]
    if len(pieces) <= 1:
        return [edge]

    # Orient and sort pieces along original line.
    ordered: list[tuple[float, list[Vec2]]] = []
    for geom in pieces:
        coords = [Vec2(float(x), float(y)) for x, y in list(geom.coords)]
        if len(coords) < 2:
            continue
        p0 = Point(coords[0].x, coords[0].y)
        p1 = Point(coords[-1].x, coords[-1].y)
        a0 = float(line.project(p0))
        a1 = float(line.project(p1))
        if a0 > a1:
            coords.reverse()
            a0, a1 = a1, a0
        ordered.append((0.5 * (a0 + a1), coords))
    ordered.sort(key=lambda item: item[0])
    if len(ordered) <= 1:
        return [edge]

    out: list[object] = []
    boundary_node_ids: dict[tuple[int, int], str] = {}
    edge_flags = _edge_flags(edge)
    for idx, (_, coords) in enumerate(ordered):
        if _polyline_length(coords) <= max(split_tol_m, 0.5):
            continue
        if idx == 0:
            u_id = str(getattr(edge, "u"))
        else:
            key = (int(round(coords[0].x * 1000)), int(round(coords[0].y * 1000)))
            u_id = boundary_node_ids.get(key)
            if u_id is None:
                u_id = _ensure_node_at_position(nodes, coords[0])
                boundary_node_ids[key] = u_id
        if idx == len(ordered) - 1:
            v_id = str(getattr(edge, "v"))
        else:
            key = (int(round(coords[-1].x * 1000)), int(round(coords[-1].y * 1000)))
            v_id = boundary_node_ids.get(key)
            if v_id is None:
                v_id = _ensure_node_at_position(nodes, coords[-1])
                boundary_node_ids[key] = v_id
        base_id = str(getattr(edge, "id"))
        if "culdesac" in edge_flags and "-cul" not in base_id:
            base_id = f"{base_id}-cul"
        out.append(_rebuild_edge_like(edge, edge_id=f"{base_id}-s{idx}", u=u_id, v=v_id, path_points=coords))
    return out or [edge]


def snap_endpoints_to_segments_create_t_junctions(
    nodes: list[object],
    edges: list[object],
    *,
    t_radius_m: float,
    split_tol_m: float,
) -> tuple[list[object], int, int]:
    if t_radius_m <= 0.0:
        return edges, 0, 0

    node_lookup = {str(getattr(n, "id")): n for n in nodes}
    split_points_by_edge: DefaultDict[str, list[Vec2]] = defaultdict(list)
    snapped = 0
    split_targets = 0

    for ei, edge in enumerate(list(edges)):
        if str(getattr(edge, "road_class", "")) != "collector":
            continue
        path = _edge_points(edge, node_lookup)
        if len(path) < 2:
            continue
        new_path = list(path)
        for end_idx in (0, -1):
            p = new_path[end_idx]
            best = None
            for target in edges:
                if getattr(target, "id") == getattr(edge, "id"):
                    continue
                target_path = _edge_points(target, node_lookup)
                if len(target_path) < 2:
                    continue
                along, proj, seg_idx = _project_on_polyline(target_path, p)
                dist = p.distance_to(proj)
                if dist > t_radius_m:
                    continue
                # Prefer arterial/collector targets
                prio = 0 if str(getattr(target, "road_class", "")) in ("arterial", "collector") else 1
                if best is None or (prio, dist, along) < (best[0], best[1], best[4]):
                    best = (prio, dist, target, proj, along, target_path, seg_idx)
            if best is None:
                continue
            _, _, target, proj, _, target_path, target_seg_idx = best
            if _point_near_polyline_endpoint(target_path, proj, max(split_tol_m * 3.0, 2.0)):
                continue
            new_path = _angle_optimize_t_endpoint(
                new_path,
                end_idx=end_idx,
                proj=proj,
                target_path=target_path,
                target_seg_idx=int(target_seg_idx),
                t_radius_m=float(t_radius_m),
            )
            new_path[end_idx] = proj
            node_id = str(getattr(edge, "u" if end_idx == 0 else "v"))
            _set_node_position(nodes, node_id, proj)
            split_points_by_edge[str(getattr(target, "id"))].append(proj)
            snapped += 1

        edges[ei] = _rebuild_edge_like(edge, path_points=new_path)

    if not split_points_by_edge:
        return edges, snapped, split_targets

    rebuilt: list[object] = []
    for edge in edges:
        points = _edge_points(edge, {str(getattr(n, "id")): n for n in nodes})
        if len(points) < 2:
            rebuilt.append(edge)
            continue
        splits = split_points_by_edge.get(str(getattr(edge, "id")), [])
        parts = _split_edge_at_points(edge, points, splits, nodes, split_tol_m=split_tol_m)
        if len(parts) > 1:
            split_targets += 1
        rebuilt.extend(parts)
    return rebuilt, snapped, split_targets


def split_crossings(
    nodes: list[object],
    edges: list[object],
    *,
    split_tol_m: float,
) -> tuple[list[object], int]:
    node_lookup = {str(getattr(n, "id")): n for n in nodes}
    line_data: list[tuple[object, list[Vec2], list[tuple[int, Segment]]]] = []
    spatial = SpatialHashIndex(cell_size=256.0)
    crossings = 0
    split_points_by_edge: DefaultDict[str, list[Vec2]] = defaultdict(list)

    for edge in edges:
        rc = str(getattr(edge, "road_class", ""))
        if rc not in ("arterial", "collector"):
            continue
        pts = _edge_points(edge, node_lookup)
        segs = _segmentize_polyline(pts)
        if len(segs) == 0:
            continue
        line_data.append((edge, pts, segs))
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        bbox = Segment(Vec2(min(xs), min(ys)), Vec2(max(xs), max(ys))).bbox()
        spatial.insert(str(getattr(edge, "id")), bbox)

    line_lookup = {str(getattr(e, "id")): (e, pts, segs) for e, pts, segs in line_data}
    seen_pairs: set[tuple[str, str]] = set()
    for edge, pts, segs in line_data:
        eid = str(getattr(edge, "id"))
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        bbox = Segment(Vec2(min(xs), min(ys)), Vec2(max(xs), max(ys))).bbox()
        for oid in spatial.query(bbox):
            if oid == eid:
                continue
            pair = tuple(sorted((eid, oid)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            other = line_lookup.get(oid)
            if other is None:
                continue
            oedge, opts, osegs = other
            if {getattr(edge, "u"), getattr(edge, "v")} & {getattr(oedge, "u"), getattr(oedge, "v")}:
                continue
            for _, s1 in segs:
                for _, s2 in osegs:
                    hit = segment_intersection(s1, s2)
                    if hit.kind != "point" or hit.point is None:
                        continue
                    p = hit.point
                    if _point_near_polyline_endpoint(pts, p, max(split_tol_m * 2.0, 1.5)):
                        continue
                    if _point_near_polyline_endpoint(opts, p, max(split_tol_m * 2.0, 1.5)):
                        continue
                    split_points_by_edge[eid].append(p)
                    split_points_by_edge[oid].append(p)
                    crossings += 1

    if not split_points_by_edge:
        return edges, 0

    rebuilt: list[object] = []
    for edge in edges:
        pts = _edge_points(edge, {str(getattr(n, "id")): n for n in nodes})
        if len(pts) < 2:
            rebuilt.append(edge)
            continue
        rebuilt.extend(_split_edge_at_points(edge, pts, split_points_by_edge.get(str(getattr(edge, "id")), []), nodes, split_tol_m=split_tol_m))
    return rebuilt, crossings


def prune_short_dangles(
    nodes: list[object],
    edges: list[object],
    *,
    min_dangle_length_m: float,
) -> tuple[list[object], int]:
    if min_dangle_length_m <= 0.0:
        return edges, 0
    degree: Dict[str, int] = defaultdict(int)
    for e in edges:
        degree[str(getattr(e, "u"))] += 1
        degree[str(getattr(e, "v"))] += 1
    kept: list[object] = []
    pruned = 0
    for e in edges:
        if str(getattr(e, "road_class", "")) != "collector":
            kept.append(e)
            continue
        if "culdesac" in _edge_flags(e) or "-cul" in str(getattr(e, "id", "")):
            kept.append(e)
            continue
        u = str(getattr(e, "u"))
        v = str(getattr(e, "v"))
        if degree.get(u, 0) != 1 and degree.get(v, 0) != 1:
            kept.append(e)
            continue
        length = float(getattr(e, "length_m", 0.0))
        path = getattr(e, "path_points", None)
        if path and len(path) >= 2:
            pts = [Vec2(float(p.x if hasattr(p, "x") else p["x"]), float(p.y if hasattr(p, "y") else p["y"])) for p in path]
            length = _polyline_length(pts)
        if length < min_dangle_length_m:
            pruned += 1
            continue
        kept.append(e)
    return kept, pruned


def apply_intersection_operators(
    nodes: list[object],
    edges: list[object],
    *,
    snap_radius_m: float,
    t_junction_radius_m: float,
    split_tolerance_m: float,
    min_dangle_length_m: float,
) -> tuple[list[object], list[object], list[str], dict[str, float]]:
    notes: list[str] = []
    numeric: dict[str, float] = {}

    snap_count = snap_endpoints_to_nodes(nodes, edges, snap_radius_m=snap_radius_m)
    if snap_count:
        notes.append(f"intersection_snap_to_node:{snap_count}")
    numeric["intersection_snap_to_node_count"] = float(snap_count)

    edges, t_snap_count, t_split_targets = snap_endpoints_to_segments_create_t_junctions(
        nodes,
        edges,
        t_radius_m=t_junction_radius_m,
        split_tol_m=split_tolerance_m,
    )
    if t_snap_count:
        notes.append(f"intersection_t_junctions:{t_snap_count}")
    numeric["intersection_t_junction_count"] = float(t_snap_count)
    numeric["intersection_t_split_target_count"] = float(t_split_targets)

    edges, crossing_splits = split_crossings(nodes, edges, split_tol_m=split_tolerance_m)
    if crossing_splits:
        notes.append(f"intersection_crossing_splits:{crossing_splits}")
    numeric["intersection_crossing_split_count"] = float(crossing_splits)

    edges, pruned_dangles = prune_short_dangles(nodes, edges, min_dangle_length_m=min_dangle_length_m)
    if pruned_dangles:
        notes.append(f"intersection_pruned_dangles:{pruned_dangles}")
    numeric["intersection_pruned_dangle_count"] = float(pruned_dangles)

    return nodes, edges, notes, numeric
