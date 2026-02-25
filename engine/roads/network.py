from __future__ import annotations

from dataclasses import dataclass
import heapq
from math import atan2, cos, hypot, pi, sin
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
try:
    import networkx as nx  # type: ignore
except ImportError:  # pragma: no cover - exercised in minimal environments
    from engine.roads import _nx_compat as nx  # type: ignore

from engine.core.geometry import Segment, Vec2, segment_intersection
from engine.core.spatial import SpatialHashIndex
from engine.hubs.sampling import HubPoint


@dataclass
class BuiltRoadNode:
    id: str
    pos: Vec2
    kind: str
    source_hub_id: Optional[str] = None


@dataclass
class BuiltRoadEdge:
    id: str
    u: str
    v: str
    road_class: str
    weight: float
    length_m: float
    river_crossings: int
    width_m: float = 8.0
    render_order: int = 1
    path_points: Optional[List[Vec2]] = None
    flags: frozenset[str] = frozenset()


@dataclass
class RoadBuildResult:
    nodes: List[BuiltRoadNode]
    edges: List[BuiltRoadEdge]
    candidate_debug: List[Tuple[str, Vec2, Vec2, float]]
    metrics: Dict[str, float]


RoadProgressCallback = Callable[[str, float, str], None]
RoadStreamCallback = Callable[[Dict[str, Any]], None]


def _emit_stream_event(stream_cb: RoadStreamCallback | None, event: Dict[str, Any]) -> None:
    """Emit a streaming event if callback is provided."""
    if stream_cb is None:
        return
    try:
        stream_cb(event)
    except Exception:
        return


def _emit_road_progress(progress_cb: RoadProgressCallback | None, phase: str, progress: float, message: str) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(str(phase), float(max(0.0, min(1.0, progress))), str(message))
    except Exception:
        return


def _world_to_grid(pos: Vec2, extent_m: float, resolution: int) -> Tuple[int, int]:
    if resolution <= 1:
        return (0, 0)
    x = int(round((pos.x / extent_m) * (resolution - 1)))
    y = int(round((pos.y / extent_m) * (resolution - 1)))
    x = min(max(x, 0), resolution - 1)
    y = min(max(y, 0), resolution - 1)
    return x, y


def _segment_cost(
    a: Vec2,
    b: Vec2,
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    slope_penalty: float,
    river_cross_penalty: float,
) -> Tuple[float, float, int]:
    seg = Segment(a, b)
    length = seg.length()
    if length <= 1e-6:
        return (0.0, 0.0, 0)

    steps = max(8, int(length / max(extent_m / 64.0, 1.0)))
    xs: List[int] = []
    ys: List[int] = []
    for i in range(steps + 1):
        t = i / float(steps)
        p = seg.point_at(t)
        gx, gy = _world_to_grid(p, extent_m, slope.shape[0])
        xs.append(gx)
        ys.append(gy)

    slope_vals = slope[np.array(ys), np.array(xs)]
    slope_norm = float(np.mean(slope_vals) / (float(np.max(slope)) + 1e-9))

    river_vals = river_mask[np.array(ys), np.array(xs)] if river_mask.size else np.zeros(len(xs), dtype=bool)
    river_crossings = 0
    prev = bool(river_vals[0]) if len(river_vals) else False
    for value in river_vals[1:]:
        current = bool(value)
        if current != prev:
            river_crossings += 1
        prev = current
    river_crossings //= 2  # in-out transitions count as one crossing approximately

    weight = length * (1.0 + slope_penalty * slope_norm) + river_crossings * river_cross_penalty
    return (float(weight), float(length), int(river_crossings))


def _polyline_length(points: Sequence[Vec2]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += points[i].distance_to(points[i + 1])
    return float(total)


def _polyline_endpoint_span(points: Sequence[Vec2]) -> float:
    if len(points) < 2:
        return 0.0
    return float(points[0].distance_to(points[-1]))


def _split_polyline_by_length(points: Sequence[Vec2], max_chunk_length_m: float) -> list[list[Vec2]]:
    pts = list(points)
    if len(pts) < 2 or max_chunk_length_m <= 1e-6:
        return [pts]
    out: list[list[Vec2]] = []
    chunk: list[Vec2] = [pts[0]]
    acc = 0.0
    for i in range(len(pts) - 1):
        a = chunk[-1]
        b_full = pts[i + 1]
        rem_end = b_full
        rem_len = a.distance_to(rem_end)
        if rem_len <= 1e-9:
            continue
        while rem_len > 1e-9 and acc + rem_len > max_chunk_length_m:
            cut_dist = max_chunk_length_m - acc
            if cut_dist <= 1e-6:
                break
            t = cut_dist / max(rem_len, 1e-9)
            cut = Vec2(a.x + (rem_end.x - a.x) * t, a.y + (rem_end.y - a.y) * t)
            chunk.append(cut)
            if len(chunk) >= 2 and _polyline_length(chunk) >= 8.0:
                out.append(chunk)
            chunk = [cut]
            a = cut
            rem_len = a.distance_to(rem_end)
            acc = 0.0
        if rem_len > 1e-9:
            chunk.append(rem_end)
            acc += rem_len
    if len(chunk) >= 2 and _polyline_length(chunk) >= 8.0:
        out.append(chunk)
    if not out:
        return [pts]
    if len(out) >= 2 and _polyline_length(out[-1]) < 14.0:
        tail = out.pop()
        merged = out[-1][:-1] + tail
        out[-1] = merged
    return out


def _polyline_cost(
    points: Sequence[Vec2],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    slope_penalty: float,
    river_cross_penalty: float,
) -> Tuple[float, float, int]:
    if len(points) < 2:
        return (0.0, 0.0, 0)
    total_weight = 0.0
    total_len = 0.0
    crossings = 0
    for i in range(len(points) - 1):
        w, length_m, river_cross = _segment_cost(
            points[i],
            points[i + 1],
            extent_m,
            slope,
            river_mask,
            slope_penalty,
            river_cross_penalty,
        )
        total_weight += float(w)
        total_len += float(length_m)
        crossings += int(river_cross)
    return (float(total_weight), float(total_len), int(crossings))


def _edge_flags(edge: object) -> frozenset[str]:
    flags = getattr(edge, "flags", None)
    if flags is None:
        # Backward-compatible fallback for historical ID suffixes.
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


def _has_edge_flag(edge: object, name: str) -> bool:
    return str(name) in _edge_flags(edge)


def _edge_id_suffix_from_flags(flags: frozenset[str]) -> str:
    if "culdesac" in flags:
        return "-cul"
    return ""


def _grid_to_world(ix: int, iy: int, extent_m: float, resolution: int) -> Vec2:
    if resolution <= 1:
        return Vec2(0.0, 0.0)
    x = (ix / float(resolution - 1)) * extent_m
    y = (iy / float(resolution - 1)) * extent_m
    return Vec2(float(x), float(y))


def _resample_grid_nn(grid: np.ndarray, target_res: int) -> np.ndarray:
    if grid.ndim != 2:
        raise ValueError("grid must be 2D")
    src_rows, src_cols = grid.shape
    if src_rows == target_res and src_cols == target_res:
        return grid
    ys = np.linspace(0, src_rows - 1, target_res)
    xs = np.linspace(0, src_cols - 1, target_res)
    yi = np.clip(np.round(ys).astype(int), 0, src_rows - 1)
    xi = np.clip(np.round(xs).astype(int), 0, src_cols - 1)
    return grid[np.ix_(yi, xi)]


_NBR8: Tuple[Tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def _astar_grid(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    slope_norm: np.ndarray,
    river_mask: np.ndarray,
    slope_factor: float,
    river_penalty: float,
    allowed_mask: Optional[np.ndarray] = None,
    extra_cost: Optional[np.ndarray] = None,
) -> Optional[List[Tuple[int, int]]]:
    rows, cols = slope_norm.shape
    if rows == 0 or cols == 0:
        return None

    def in_bounds(y: int, x: int) -> bool:
        return 0 <= y < rows and 0 <= x < cols

    sy, sx = start
    gy, gx = goal
    if not in_bounds(sy, sx) or not in_bounds(gy, gx):
        return None

    open_heap: List[Tuple[float, float, Tuple[int, int]]] = []
    heapq.heappush(open_heap, (0.0, 0.0, start))
    came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
    g_score: Dict[Tuple[int, int], float] = {start: 0.0}
    closed: Set[Tuple[int, int]] = set()

    def h(y: int, x: int) -> float:
        return hypot(gx - x, gy - y)

    while open_heap:
        _, current_g, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        closed.add(cur)
        if cur == goal:
            path = [cur]
            while path[-1] in came_from:
                path.append(came_from[path[-1]])
            path.reverse()
            return path
        cy, cx = cur
        for dy, dx in _NBR8:
            ny = cy + dy
            nx = cx + dx
            if not in_bounds(ny, nx):
                continue
            if allowed_mask is not None and (ny >= allowed_mask.shape[0] or nx >= allowed_mask.shape[1] or not bool(allowed_mask[ny, nx])):
                continue
            step_len = 1.41421356237 if (dx != 0 and dy != 0) else 1.0
            slope_cost = slope_factor * float(slope_norm[ny, nx] ** 2)
            river_cost = river_penalty if bool(river_mask[ny, nx]) else 0.0
            extra = float(extra_cost[ny, nx]) if extra_cost is not None else 0.0
            step_cost = step_len * (1.0 + slope_cost) + river_cost + extra
            tentative = current_g + step_cost
            nbr = (ny, nx)
            if tentative >= g_score.get(nbr, float("inf")):
                continue
            came_from[nbr] = cur
            g_score[nbr] = tentative
            heapq.heappush(open_heap, (tentative + h(ny, nx), tentative, nbr))
    return None


def _corridor_allowed_mask(
    corridor_geom: object | None,
    *,
    extent_m: float,
    route_res: int,
) -> Optional[np.ndarray]:
    if corridor_geom is None:
        return None
    try:
        from shapely.geometry import Point  # type: ignore
    except Exception:
        return None
    try:
        if getattr(corridor_geom, "is_empty", True):
            return None
    except Exception:
        return None
    mask = np.zeros((route_res, route_res), dtype=bool)
    for iy in range(route_res):
        for ix in range(route_res):
            p = _grid_to_world(ix, iy, extent_m, route_res)
            try:
                inside = bool(corridor_geom.covers(Point(float(p.x), float(p.y))))
            except Exception:
                try:
                    inside = bool(corridor_geom.buffer(0.5).contains(Point(float(p.x), float(p.y))))
                except Exception:
                    inside = False
            mask[iy, ix] = inside
    return mask


def _route_points_with_cost_mask(
    start: Vec2,
    end: Vec2,
    *,
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    road_class: str,
    corridor_geom: object | None = None,
    slope_penalty_scale: float = 1.0,
    river_penalty_scale: float = 1.0,
) -> Optional[List[Vec2]]:
    route_res = min(192, max(96, slope.shape[0] // 2 if slope.shape[0] > 0 else 96))
    slope_grid = _resample_grid_nn(slope, route_res).astype(np.float64)
    river_grid = _resample_grid_nn(river_mask.astype(np.float64), route_res) > 0.5
    slope_max = float(np.max(slope_grid)) if slope_grid.size else 0.0
    slope_norm = slope_grid / (slope_max + 1e-9) if slope_max > 0 else np.zeros_like(slope_grid)

    sx, sy = _world_to_grid(start, extent_m, route_res)
    gx, gy = _world_to_grid(end, extent_m, route_res)
    start_cell = (sy, sx)
    goal_cell = (gy, gx)

    allowed_mask = _corridor_allowed_mask(corridor_geom, extent_m=extent_m, route_res=route_res)
    if allowed_mask is not None:
        # Always allow endpoints even if rasterization missed them at the boundary.
        allowed_mask[sy, sx] = True
        allowed_mask[gy, gx] = True

    if road_class == "arterial":
        slope_factor = 18.0
        river_pen = 1500.0
    elif road_class == "collector":
        slope_factor = 10.0
        river_pen = 700.0
    else:
        slope_factor = 8.0
        river_pen = 850.0
    cells = _astar_grid(
        start_cell,
        goal_cell,
        slope_norm,
        river_grid,
        float(slope_factor) * float(slope_penalty_scale),
        float(river_pen) * float(river_penalty_scale),
        allowed_mask=allowed_mask,
        extra_cost=None,
    )
    if not cells or len(cells) < 2:
        return None
    pts = [_grid_to_world(x, y, extent_m, route_res) for (y, x) in cells]
    pts[0] = start
    pts[-1] = end
    cleaned: List[Vec2] = []
    for p in pts:
        if not cleaned or p.distance_to(cleaned[-1]) > 1e-6:
            cleaned.append(p)
    if len(cleaned) >= 3:
        cleaned = _rdp(cleaned, epsilon=max(4.0, extent_m * 0.0008))
        cleaned[0] = start
        cleaned[-1] = end
    return cleaned


def _perpendicular_distance(p: Vec2, a: Vec2, b: Vec2) -> float:
    if a.distance_to(b) <= 1e-6:
        return p.distance_to(a)
    ab = b - a
    ap = p - a
    area2 = abs(ab.cross(ap))
    return area2 / max(ab.length(), 1e-6)


def _rdp(points: Sequence[Vec2], epsilon: float) -> List[Vec2]:
    if len(points) <= 2:
        return list(points)
    a = points[0]
    b = points[-1]
    max_dist = -1.0
    idx = -1
    for i in range(1, len(points) - 1):
        d = _perpendicular_distance(points[i], a, b)
        if d > max_dist:
            max_dist = d
            idx = i
    if max_dist <= epsilon or idx < 0:
        return [a, b]
    left = _rdp(points[: idx + 1], epsilon)
    right = _rdp(points[idx:], epsilon)
    return left[:-1] + right


def _route_polyline_for_edge(
    edge: BuiltRoadEdge,
    node_lookup: Dict[str, BuiltRoadNode],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
) -> List[Vec2]:
    u = node_lookup.get(edge.u)
    v = node_lookup.get(edge.v)
    if u is None or v is None:
        return []
    start = u.pos
    end = v.pos
    route_res = min(192, max(96, slope.shape[0] // 2 if slope.shape[0] > 0 else 96))
    slope_grid = _resample_grid_nn(slope, route_res).astype(np.float64)
    river_grid = _resample_grid_nn(river_mask.astype(np.float64), route_res) > 0.5
    slope_max = float(np.max(slope_grid)) if slope_grid.size else 0.0
    slope_norm = slope_grid / (slope_max + 1e-9) if slope_max > 0 else np.zeros_like(slope_grid)

    sx, sy = _world_to_grid(start, extent_m, route_res)
    gx, gy = _world_to_grid(end, extent_m, route_res)
    start_cell = (sy, sx)
    goal_cell = (gy, gx)

    slope_factor = 18.0 if edge.road_class == "arterial" else 10.0
    river_penalty = 1500.0 if edge.road_class == "arterial" else 700.0
    cells = _astar_grid(start_cell, goal_cell, slope_norm, river_grid, slope_factor, river_penalty)
    if not cells or len(cells) < 2:
        return [start, end]

    pts = [_grid_to_world(x, y, extent_m, route_res) for (y, x) in cells]
    pts[0] = start
    pts[-1] = end

    # Drop immediate duplicates.
    cleaned: List[Vec2] = []
    for p in pts:
        if not cleaned or p.distance_to(cleaned[-1]) > 1e-6:
            cleaned.append(p)
    if len(cleaned) >= 3:
        cleaned = _rdp(cleaned, epsilon=max(8.0, extent_m * 0.0015))
        cleaned[0] = start
        cleaned[-1] = end
    return cleaned


def _route_all_edges(
    nodes: Sequence[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    slope_penalty: float,
    river_cross_penalty: float,
) -> None:
    node_lookup = {n.id: n for n in nodes}
    for i, edge in enumerate(edges):
        if edge.road_class not in ("arterial", "collector", "local"):
            continue
        if edge.path_points and len(edge.path_points) >= 2:
            # Preserve geometry generated by hierarchy linework; only recompute cost metrics.
            weight, length_m, crossings = _polyline_cost(
                edge.path_points,
                extent_m=extent_m,
                slope=slope,
                river_mask=river_mask,
                slope_penalty=slope_penalty,
                river_cross_penalty=river_cross_penalty,
            )
            edges[i] = BuiltRoadEdge(
                id=edge.id,
                u=edge.u,
                v=edge.v,
                road_class=edge.road_class,
                weight=weight,
                length_m=length_m,
                river_crossings=crossings,
                width_m=edge.width_m,
                render_order=edge.render_order,
                path_points=list(edge.path_points),
                flags=_edge_flags(edge),
            )
            continue
        path_points = _route_polyline_for_edge(edge, node_lookup, extent_m, slope, river_mask)
        if len(path_points) < 2:
            continue
        weight, length_m, crossings = _polyline_cost(
            path_points,
            extent_m=extent_m,
            slope=slope,
            river_mask=river_mask,
            slope_penalty=slope_penalty,
            river_cross_penalty=river_cross_penalty,
        )
        edges[i] = BuiltRoadEdge(
            id=edge.id,
            u=edge.u,
            v=edge.v,
            road_class=edge.road_class,
            weight=weight,
            length_m=length_m,
            river_crossings=crossings,
            width_m=edge.width_m,
            render_order=edge.render_order,
            path_points=path_points,
            flags=_edge_flags(edge),
        )


def _build_candidate_graph(
    hubs: Sequence[HubPoint],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    k_neighbors: int,
    slope_penalty: float,
    river_cross_penalty: float,
) -> Tuple[nx.Graph, List[Tuple[str, Vec2, Vec2, float]]]:
    graph = nx.Graph()
    debug: List[Tuple[str, Vec2, Vec2, float]] = []
    for hub in hubs:
        graph.add_node(hub.id, pos=hub.pos, tier=hub.tier)

    positions = np.array([[h.pos.x, h.pos.y] for h in hubs], dtype=np.float64)
    if len(hubs) <= 1:
        return graph, debug

    for i, hub in enumerate(hubs):
        diff = positions - positions[i]
        dists = np.sqrt(np.sum(diff * diff, axis=1))
        order = np.argsort(dists)
        neighbors = [j for j in order if j != i][: max(1, k_neighbors)]
        for j in neighbors:
            if graph.has_edge(hub.id, hubs[j].id):
                continue
            w, length_m, river_crossings = _segment_cost(
                hub.pos,
                hubs[j].pos,
                extent_m,
                slope,
                river_mask,
                slope_penalty,
                river_cross_penalty,
            )
            graph.add_edge(
                hub.id,
                hubs[j].id,
                weight=w,
                length_m=length_m,
                river_crossings=river_crossings,
            )
            debug.append((f"cand-{hub.id}-{hubs[j].id}", hub.pos, hubs[j].pos, w))

    # Ensure connectivity by adding nearest inter-component bridges if needed.
    while graph.number_of_nodes() > 0 and not nx.is_connected(graph):
        components = [list(c) for c in nx.connected_components(graph)]
        comp_sets = [set(c) for c in components]
        best = None
        for ci in range(len(comp_sets)):
            for cj in range(ci + 1, len(comp_sets)):
                for a_id in comp_sets[ci]:
                    for b_id in comp_sets[cj]:
                        a = next(h for h in hubs if h.id == a_id)
                        b = next(h for h in hubs if h.id == b_id)
                        w, length_m, river_crossings = _segment_cost(
                            a.pos,
                            b.pos,
                            extent_m,
                            slope,
                            river_mask,
                            slope_penalty,
                            river_cross_penalty,
                        )
                        if best is None or w < best[0]:
                            best = (w, a, b, length_m, river_crossings)
        if best is None:
            break
        _, a, b, length_m, river_crossings = best
        graph.add_edge(
            a.id,
            b.id,
            weight=float(best[0]),
            length_m=float(length_m),
            river_crossings=int(river_crossings),
        )
        debug.append((f"cand-{a.id}-{b.id}", a.pos, b.pos, float(best[0])))

    return graph, debug


def _generate_backbone_edges(graph: nx.Graph, loop_budget: int) -> List[Tuple[str, str, Dict[str, float]]]:
    if graph.number_of_edges() == 0:
        return []

    tree = nx.minimum_spanning_tree(graph, weight="weight")
    selected: Set[Tuple[str, str]] = set()
    result: List[Tuple[str, str, Dict[str, float]]] = []
    for u, v, data in tree.edges(data=True):
        key = tuple(sorted((u, v)))
        selected.add(key)
        result.append((u, v, dict(data)))

    if loop_budget <= 0:
        return result

    # Loop enhancement: pick non-tree edges that provide high detour reduction.
    candidates = []
    for u, v, data in graph.edges(data=True):
        key = tuple(sorted((u, v)))
        if key in selected:
            continue
        try:
            detour = nx.shortest_path_length(tree, source=u, target=v, weight="weight")
        except nx.NetworkXNoPath:
            detour = float("inf")
        direct = float(data.get("weight", 0.0))
        gain = (detour - direct) / max(direct, 1e-6)
        candidates.append((gain, u, v, dict(data)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    added = 0
    for gain, u, v, data in candidates:
        if added >= loop_budget:
            break
        if gain <= 0.10:
            continue
        tree.add_edge(u, v, **data)
        result.append((u, v, data))
        added += 1

    return result


def _branch_direction_candidates(base_angle: float, rng: np.random.Generator) -> List[float]:
    offsets = [0.0, pi / 6.0, -pi / 6.0, pi / 3.0, -pi / 3.0, pi]
    jitter = float(rng.uniform(-0.15, 0.15))
    return [base_angle + off + jitter for off in offsets]


def _branch_step_cost(
    start: Vec2,
    end: Vec2,
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    existing_nodes: Sequence[BuiltRoadNode],
    existing_edges: Sequence[BuiltRoadEdge],
    node_lookup: Dict[str, BuiltRoadNode],
    slope_penalty: float,
    river_cross_penalty: float,
) -> Tuple[float, int]:
    if not (0.0 <= end.x <= extent_m and 0.0 <= end.y <= extent_m):
        return (float("inf"), 0)
    for node in existing_nodes:
        if end.distance_to(node.pos) < max(8.0, extent_m * 0.01):
            return (float("inf"), 0)
    candidate_seg = Segment(start, end)
    for edge in existing_edges:
        edge_u = node_lookup.get(edge.u)
        edge_v = node_lookup.get(edge.v)
        if edge_u is None or edge_v is None:
            continue
        # Shared endpoint at the branch origin is legal.
        if edge_u.pos.distance_to(start) <= 1e-6 or edge_v.pos.distance_to(start) <= 1e-6:
            continue
        hit = segment_intersection(candidate_seg, Segment(edge_u.pos, edge_v.pos))
        if hit.kind in ("point", "overlap"):
            return (float("inf"), 0)
    weight, _, river_cross = _segment_cost(
        start,
        end,
        extent_m,
        slope,
        river_mask,
        slope_penalty,
        river_cross_penalty,
    )
    return (weight, river_cross)


def _generate_branches(
    hubs: Sequence[HubPoint],
    nodes: List[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    branch_steps: int,
    slope_penalty: float,
    river_cross_penalty: float,
    seed: int,
    stream_cb: Optional[RoadStreamCallback] = None,
) -> None:
    if branch_steps <= 0:
        return

    rng = np.random.default_rng(seed + 2003)
    center = Vec2(extent_m * 0.5, extent_m * 0.5)
    next_node_idx = 0
    if nodes:
        next_node_idx = len(nodes)

    node_lookup = {n.id: n for n in nodes}

    for hub in hubs:
        if hub.tier == 1:
            continue
        current_id = hub.id
        current_pos = node_lookup[current_id].pos
        base_angle = atan2(current_pos.y - center.y, current_pos.x - center.x)
        step_len = extent_m * (0.05 if hub.tier == 2 else 0.035)
        for _ in range(branch_steps):
            best = None
            for angle in _branch_direction_candidates(base_angle, rng):
                candidate = Vec2(current_pos.x + cos(angle) * step_len, current_pos.y + sin(angle) * step_len)
                weight, river_cross = _branch_step_cost(
                    current_pos,
                    candidate,
                    extent_m,
                    slope,
                    river_mask,
                    nodes,
                    edges,
                    node_lookup,
                    slope_penalty,
                    river_cross_penalty,
                )
                if np.isinf(weight):
                    continue
                if best is None or weight < best[0]:
                    best = (float(weight), candidate, int(river_cross), angle)
            if best is None:
                break
            _, next_pos, river_cross, chosen_angle = best
            new_id = f"node-{next_node_idx}"
            next_node_idx += 1
            nodes.append(BuiltRoadNode(id=new_id, pos=next_pos, kind="branch", source_hub_id=hub.id))
            # Stream the new node
            _emit_stream_event(stream_cb, {
                "event_type": "road_node_added",
                "data": {"id": new_id, "x": next_pos.x, "y": next_pos.y, "kind": "branch"},
            })
            edge = BuiltRoadEdge(
                id=f"edge-{len(edges)}",
                u=current_id,
                v=new_id,
                road_class="local",
                weight=float(best[0]),
                length_m=current_pos.distance_to(next_pos),
                river_crossings=river_cross,
                width_m=8.0,
                render_order=1,
            )
            edges.append(edge)
            # Stream the new edge
            _emit_stream_event(stream_cb, {
                "event_type": "road_edge_added",
                "data": {
                    "id": edge.id,
                    "u": edge.u,
                    "v": edge.v,
                    "road_class": edge.road_class,
                    "length_m": edge.length_m,
                },
            })
            node_lookup[new_id] = nodes[-1]
            current_id = new_id
            current_pos = next_pos
            base_angle = chosen_angle


def _iter_lines_geom(geom) -> Iterable[object]:
    if getattr(geom, "is_empty", True):
        return []
    gt = getattr(geom, "geom_type", "")
    if gt == "LineString":
        return [geom]
    if gt == "MultiLineString":
        return list(getattr(geom, "geoms", []))
    return [g for g in getattr(geom, "geoms", []) if getattr(g, "geom_type", "") == "LineString"]


def _iter_polys_geom(geom) -> Iterable[object]:
    if getattr(geom, "is_empty", True):
        return []
    gt = getattr(geom, "geom_type", "")
    if gt == "Polygon":
        return [geom]
    if gt == "MultiPolygon":
        return list(getattr(geom, "geoms", []))
    return [g for g in getattr(geom, "geoms", []) if getattr(g, "geom_type", "") == "Polygon"]


def _dominant_axis_angle_deg(poly) -> float:
    coords = list(poly.minimum_rotated_rectangle.exterior.coords)
    if len(coords) < 4:
        return 0.0
    best_len = -1.0
    best_angle = 0.0
    for i in range(4):
        x0, y0 = coords[i]
        x1, y1 = coords[i + 1]
        dx = float(x1 - x0)
        dy = float(y1 - y0)
        l2 = dx * dx + dy * dy
        if l2 > best_len:
            best_len = l2
            best_angle = np.degrees(np.arctan2(dy, dx))
    return float(best_angle)


def _normalize_angle_deg(angle_deg: float) -> float:
    a = float(angle_deg)
    while a <= -180.0:
        a += 360.0
    while a > 180.0:
        a -= 360.0
    return a


def _styled_axis_angle_deg(angle_deg: float, road_style: str, rng: np.random.Generator, kind: str) -> float:
    a = _normalize_angle_deg(angle_deg)
    style = (road_style or "mixed_organic").lower()
    if style == "grid":
        # Snap to coarse orthogonal directions for rectilinear districts.
        options = [0.0, 90.0, -90.0, 180.0]
        return min(options, key=lambda x: abs(_normalize_angle_deg(a - x)))
    if style == "organic":
        jitter = 16.0 if kind == "collector" else 22.0
        return _normalize_angle_deg(a + float(rng.uniform(-jitter, jitter)))
    if style == "skeleton":
        return a
    # mixed_organic
    jitter = 6.0 if kind == "collector" else 10.0
    return _normalize_angle_deg(a + float(rng.uniform(-jitter, jitter)))


def _parallel_lines_in_polygon(
    poly,
    *,
    spacing_m: float,
    angle_deg: float,
    jitter_ratio: float,
    rng: np.random.Generator,
    min_length_m: float,
    max_lines: int,
) -> List[object]:
    if spacing_m <= 0.0 or max_lines <= 0:
        return []
    try:
        from shapely import affinity  # type: ignore
        from shapely.geometry import LineString  # type: ignore
    except Exception:
        return []

    rot = affinity.rotate(poly, -float(angle_deg), origin="centroid")
    minx, miny, maxx, maxy = rot.bounds
    width = float(maxx - minx)
    height = float(maxy - miny)
    if width < min_length_m or height < spacing_m * 0.8:
        return []

    usable = max(0.0, height - spacing_m)
    approx_n = int(usable / max(spacing_m, 1e-6))
    n_lines = min(max_lines, max(0, approx_n))
    if n_lines <= 0:
        return []

    out: List[object] = []
    for i in range(n_lines):
        t = (i + 1) / float(n_lines + 1)
        y = miny + t * height
        y += float(rng.uniform(-0.5, 0.5)) * spacing_m * max(0.0, min(1.0, jitter_ratio))
        candidate = LineString([(minx - 5.0, y), (maxx + 5.0, y)])
        clipped = rot.intersection(candidate)
        for line in _iter_lines_geom(clipped):
            if float(getattr(line, "length", 0.0) or 0.0) < min_length_m:
                continue
            world_line = affinity.rotate(line, float(angle_deg), origin=poly.centroid)
            if float(getattr(world_line, "length", 0.0) or 0.0) < min_length_m:
                continue
            out.append(world_line)
    return out


def _append_line_edge(
    line,
    *,
    road_class: str,
    width_m: float,
    render_order: int,
    nodes: List[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    slope_penalty: float,
    river_cross_penalty: float,
) -> None:
    pts = _line_to_points(line)
    if len(pts) < 2:
        return
    _append_polyline_edge(
        pts,
        road_class=road_class,
        width_m=width_m,
        render_order=render_order,
        nodes=nodes,
        edges=edges,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
    )


def _line_to_points(line) -> List[Vec2]:
    coords = list(getattr(line, "coords", []))
    if len(coords) < 2:
        return []
    pts: List[Vec2] = []
    for x, y in coords:
        p = Vec2(float(x), float(y))
        if not pts or p.distance_to(pts[-1]) > 1e-6:
            pts.append(p)
    return pts


def _nearest_distance_to_road_classes(
    p: Vec2,
    nodes: Sequence[BuiltRoadNode],
    edges: Sequence[BuiltRoadEdge],
    classes: set[str],
) -> float:
    node_lookup = {n.id: n for n in nodes}
    best = float("inf")
    for e in edges:
        if str(e.road_class) not in classes:
            continue
        pts = list(e.path_points or [])
        if not pts or len(pts) < 2:
            u = node_lookup.get(e.u)
            v = node_lookup.get(e.v)
            if u is None or v is None:
                continue
            pts = [u.pos, v.pos]
        for i in range(len(pts) - 1):
            seg = Segment(pts[i], pts[i + 1])
            if seg.length() <= 1e-6:
                continue
            # projection distance without importing extra helpers
            vseg = seg.vector()
            denom = max(vseg.dot(vseg), 1e-9)
            t = max(0.0, min(1.0, (p - seg.p0).dot(vseg) / denom))
            proj = seg.point_at(t)
            d = p.distance_to(proj)
            if d < best:
                best = d
    return float(best)


def _append_polyline_edge(
    pts: Sequence[Vec2],
    *,
    road_class: str,
    width_m: float,
    render_order: int,
    edge_id_suffix: str = "",
    edge_flags: Optional[set[str]] = None,
    nodes: List[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    extent_m: float,
    slope: np.ndarray,
    river_mask: np.ndarray,
    slope_penalty: float,
    river_cross_penalty: float,
) -> None:
    points = list(pts)
    if len(points) < 2:
        return
    total_poly_len = _polyline_length(points)
    if total_poly_len < 8.0:
        return
    if road_class == "local":
        end_span = _polyline_endpoint_span(points)
        sinuosity = total_poly_len / max(end_span, 1e-6)
        if total_poly_len > 260.0 and (total_poly_len > 340.0 or sinuosity > 1.75):
            chunk_cap = 280.0
            if sinuosity > 2.8 or total_poly_len > 500.0:
                chunk_cap = 220.0
            chunks = _split_polyline_by_length(points, chunk_cap)
            if len(chunks) > 1:
                base_flags = set(edge_flags or set())
                for ci, chunk in enumerate(chunks):
                    sub_flags = set(base_flags)
                    if "culdesac" in sub_flags and ci != (len(chunks) - 1):
                        sub_flags.discard("culdesac")
                    _append_polyline_edge(
                        chunk,
                        road_class=road_class,
                        width_m=width_m,
                        render_order=render_order,
                        edge_id_suffix=edge_id_suffix if ci == (len(chunks) - 1) else "",
                        edge_flags=sub_flags or None,
                        nodes=nodes,
                        edges=edges,
                        extent_m=extent_m,
                        slope=slope,
                        river_mask=river_mask,
                        slope_penalty=slope_penalty,
                        river_cross_penalty=river_cross_penalty,
                    )
                return

    u_id = f"hnode-{len(nodes)}-{len(edges)}-u"
    nodes.append(BuiltRoadNode(id=u_id, pos=points[0], kind=road_class))
    v_id = f"hnode-{len(nodes)}-{len(edges)}-v"
    nodes.append(BuiltRoadNode(id=v_id, pos=points[-1], kind=road_class))

    weight, length_m, crossings = _polyline_cost(
        points,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
    )
    flags = frozenset(str(v) for v in (edge_flags or set()) if v)
    suffix = edge_id_suffix or _edge_id_suffix_from_flags(flags)
    edges.append(
        BuiltRoadEdge(
            id=f"edge-{len(edges)}{suffix}",
            u=u_id,
            v=v_id,
            road_class=road_class,
            weight=float(weight),
            length_m=float(length_m),
            river_crossings=int(crossings),
            width_m=float(width_m),
            render_order=int(render_order),
            path_points=points,
            flags=flags,
        )
    )


def _nearest_road_tangent_angle_deg(poly, nodes: Sequence[BuiltRoadNode], edges: Sequence[BuiltRoadEdge]) -> Optional[float]:
    c = getattr(poly, "centroid", None)
    if c is None:
        return None
    cp = Vec2(float(c.x), float(c.y))
    node_lookup = {n.id: n for n in nodes}
    best_dist = float("inf")
    best_angle = None
    for edge in edges:
        if edge.road_class not in ("arterial", "collector"):
            continue
        pts = edge.path_points
        if not pts or len(pts) < 2:
            u = node_lookup.get(edge.u)
            v = node_lookup.get(edge.v)
            if u is None or v is None:
                continue
            pts = [u.pos, v.pos]
        for i in range(len(pts) - 1):
            seg = Segment(pts[i], pts[i + 1])
            if seg.length() <= 1e-6:
                continue
            d = cp.distance_to(seg.point_at(max(0.0, min(1.0, (cp - seg.p0).dot(seg.vector()) / max(seg.vector().dot(seg.vector()), 1e-9)))))
            if d < best_dist:
                best_dist = d
                v = seg.vector()
                best_angle = float(np.degrees(np.arctan2(v.y, v.x)))
    return best_angle


def _build_block_polygons_from_network(
    *,
    extent_m: float,
    nodes: Sequence[BuiltRoadNode],
    edges: Sequence[BuiltRoadEdge],
    river_areas: Optional[Sequence[object]],
) -> Tuple[List[object], object]:
    try:
        from engine.blocks.extraction import extract_macro_blocks  # type: ignore
        from engine.models import Point2D, RoadEdgeRecord, RoadNetwork, RoadNodeRecord  # type: ignore
    except Exception:
        return ([], None)

    road_nodes = [RoadNodeRecord(id=n.id, x=float(n.pos.x), y=float(n.pos.y), kind=n.kind) for n in nodes]
    road_edges: List[RoadEdgeRecord] = []
    for e in edges:
        path_points = [Point2D(x=float(p.x), y=float(p.y)) for p in (e.path_points or [])] or None
        road_edges.append(
            RoadEdgeRecord(
                id=e.id,
                u=e.u,
                v=e.v,
                road_class=e.road_class,
                weight=float(e.weight),
                length_m=float(e.length_m),
                river_crossings=int(e.river_crossings),
                width_m=float(e.width_m),
                render_order=int(e.render_order),
                path_points=path_points,
            )
        )
    extraction = extract_macro_blocks(float(extent_m), RoadNetwork(nodes=road_nodes, edges=road_edges), list(river_areas or []))
    return (list(extraction.macro_blocks), extraction.river_union)


def _subtract_river_setback(poly, river_union, setback_m: float):
    if river_union is None or getattr(river_union, "is_empty", True) or setback_m <= 0.0:
        return poly
    try:
        trimmed = poly.difference(river_union.buffer(float(setback_m)))
    except Exception:
        return poly
    return trimmed


def _generate_hierarchy_linework(
    *,
    extent_m: float,
    height: Optional[np.ndarray],
    slope: np.ndarray,
    river_mask: np.ndarray,
    nodes: List[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    slope_penalty: float,
    river_cross_penalty: float,
    seed: int,
    road_style: str,
    collector_spacing_m: float,
    local_spacing_m: float,
    collector_jitter: float,
    local_jitter: float,
    local_generator: str,
    local_geometry_mode: str,
    local_reroute_coverage: str,
    local_reroute_min_length_m: float,
    local_reroute_waypoint_spacing_m: float,
    local_reroute_max_waypoints: int,
    local_reroute_corridor_buffer_m: float,
    local_reroute_block_margin_m: float,
    local_reroute_slope_penalty_scale: float,
    local_reroute_river_penalty_scale: float,
    local_reroute_collector_snap_bias_m: float,
    local_reroute_smooth_iters: int,
    local_reroute_simplify_tol_m: float,
    local_reroute_max_edges_per_city: int,
    local_reroute_apply_to_grid_supplement: bool,
    local_classic_probe_step_m: float,
    local_classic_seed_spacing_m: float,
    local_classic_max_trace_len_m: float,
    local_classic_min_trace_len_m: float,
    local_classic_turn_limit_deg: float,
    local_classic_branch_prob: float,
    local_classic_continue_prob: float,
    local_classic_culdesac_prob: float,
    local_classic_max_segments_per_block: int,
    local_classic_max_road_distance_m: float,
    local_classic_depth_decay_power: float,
    local_community_seed_count_per_block: int,
    local_community_spine_prob: float,
    local_arterial_setback_weight: float,
    local_collector_follow_weight: float,
    river_setback_m: float,
    minor_bridge_budget: int,
    max_local_block_area_m2: float,
    collector_generator: str,
    classic_probe_step_m: float,
    classic_seed_spacing_m: float,
    classic_max_trace_len_m: float,
    classic_min_trace_len_m: float,
    classic_turn_limit_deg: float,
    classic_branch_prob: float,
    classic_continue_prob: float,
    classic_culdesac_prob: float,
    classic_max_queue_size: int,
    classic_max_segments: int,
    classic_max_arterial_distance_m: float,
    classic_depth_decay_power: float,
    slope_straight_threshold_deg: float,
    slope_serpentine_threshold_deg: float,
    slope_hard_limit_deg: float,
    contour_follow_weight: float,
    arterial_align_weight: float,
    hub_seek_weight: float,
    river_snap_dist_m: float,
    river_parallel_bias_weight: float,
    river_avoid_weight: float,
    tensor_grid_resolution: int,
    tensor_step_m: float,
    tensor_seed_spacing_m: float,
    tensor_max_trace_len_m: float,
    tensor_min_trace_len_m: float,
    tensor_turn_limit_deg: float,
    tensor_water_tangent_weight: float,
    tensor_contour_tangent_weight: float,
    tensor_arterial_align_weight: float,
    tensor_hub_attract_weight: float,
    tensor_water_influence_m: float,
    tensor_arterial_influence_m: float,
    hubs: Optional[Sequence[HubPoint]] = None,
    river_areas: Optional[Sequence[object]] = None,
    stream_cb: Optional[RoadStreamCallback] = None,
) -> tuple[list[str], dict[str, float]]:
    style = (road_style or "skeleton").lower()
    notes: list[str] = []
    numeric: dict[str, float] = {
        "collector_classic_enabled": 0.0,
        "collector_classic_degraded": 0.0,
        "collector_classic_trace_count": 0.0,
        "collector_classic_riverfront_seed_count": 0.0,
        "collector_classic_riverfront_trace_count": 0.0,
        "collector_classic_arterial_t_attach_count": 0.0,
        "collector_classic_network_attach_fallback_count": 0.0,
        "collector_classic_failed_arterial_attach_count": 0.0,
        "local_classic_enabled": 0.0,
        "local_classic_degraded": 0.0,
        "local_classic_trace_count": 0.0,
        "local_culdesac_edge_count_pre_topology": 0.0,
        "local_reroute_candidate_count": 0.0,
        "local_reroute_applied_count": 0.0,
        "local_reroute_fallback_count": 0.0,
        "local_reroute_grid_supplement_applied_count": 0.0,
        "local_reroute_avg_path_points": 0.0,
        "local_reroute_avg_length_gain_ratio": 0.0,
        "collector_tensor_enabled": 0.0,
        "collector_tensor_degraded": 0.0,
        "collector_tensor_trace_count": 0.0,
    }
    if style == "skeleton":
        return notes, numeric
    _ = minor_bridge_budget  # reserved for future constrained tributary bridges

    rng = np.random.default_rng(seed + 4109)

    # Collector lines are generated first from macro blocks carved by the arterial skeleton.
    collector_blocks, river_union = _build_block_polygons_from_network(
        extent_m=extent_m,
        nodes=nodes,
        edges=edges,
        river_areas=river_areas,
    )
    requested_backend = (collector_generator or "classic_turtle").lower()
    collector_backend = requested_backend
    if collector_backend == "tensor_streamline":
        # Deprecated alias kept for compatibility; use classic turtle generator.
        collector_backend = "classic_turtle"
        notes.append("collector_generator_alias:tensor_streamline->classic_turtle")
    if collector_backend not in {"classic_turtle", "grid_clip"}:
        collector_backend = "grid_clip"
        notes.append("collector_generator_degraded:grid_clip")
    collector_added = 0
    if collector_backend == "classic_turtle":
        try:
            from engine.roads.classic_growth import ClassicCollectorConfig, generate_classic_collectors  # type: ignore
        except Exception:
            collector_backend = "grid_clip"
            notes.append("collector_generator_degraded:grid_clip")
            numeric["collector_classic_degraded"] = 1.0
        else:
            try:
                numeric["collector_classic_enabled"] = 1.0
                traces, cul_flags, classic_notes, classic_numeric = generate_classic_collectors(
                    extent_m=extent_m,
                    height=height,
                    slope=slope,
                    river_mask=river_mask,
                    river_areas=river_areas,
                    river_union=river_union,
                    nodes=nodes,
                    edges=edges,
                    hubs=list(hubs or []),
                    blocks=collector_blocks,
                    cfg=ClassicCollectorConfig(
                        classic_probe_step_m=classic_probe_step_m,
                        classic_seed_spacing_m=classic_seed_spacing_m,
                        classic_max_trace_len_m=classic_max_trace_len_m,
                        classic_min_trace_len_m=classic_min_trace_len_m,
                        classic_turn_limit_deg=classic_turn_limit_deg,
                        classic_branch_prob=classic_branch_prob,
                        classic_continue_prob=classic_continue_prob,
                        classic_culdesac_prob=classic_culdesac_prob,
                        classic_max_queue_size=classic_max_queue_size,
                        classic_max_segments=classic_max_segments,
                        classic_max_arterial_distance_m=classic_max_arterial_distance_m,
                        classic_depth_decay_power=classic_depth_decay_power,
                        slope_straight_threshold_deg=slope_straight_threshold_deg,
                        slope_serpentine_threshold_deg=slope_serpentine_threshold_deg,
                        slope_hard_limit_deg=slope_hard_limit_deg,
                        contour_follow_weight=contour_follow_weight,
                        arterial_align_weight=arterial_align_weight,
                        hub_seek_weight=hub_seek_weight,
                        river_snap_dist_m=river_snap_dist_m,
                        river_parallel_bias_weight=river_parallel_bias_weight,
                        river_avoid_weight=river_avoid_weight,
                        river_setback_m=river_setback_m,
                    ),
                    seed=seed,
                    stream_cb=stream_cb,
                )
            except Exception:
                collector_backend = "grid_clip"
                notes.append("collector_generator_degraded:grid_clip")
                numeric["collector_classic_degraded"] = 1.0
            else:
                notes.extend(classic_notes)
                numeric.update({k: float(v) for k, v in classic_numeric.items()})
                if not traces:
                    collector_backend = "grid_clip"
                    notes.append("collector_generator_degraded:grid_clip")
                    numeric["collector_classic_degraded"] = 1.0
                else:
                    for trace_idx, pts in enumerate(traces):
                        edge_suffix = "-cul" if trace_idx < len(cul_flags) and bool(cul_flags[trace_idx]) else ""
                        _append_polyline_edge(
                            pts,
                            road_class="collector",
                            width_m=11.0,
                            render_order=1,
                            edge_id_suffix=edge_suffix,
                            edge_flags={"culdesac"} if edge_suffix else None,
                            nodes=nodes,
                            edges=edges,
                            extent_m=extent_m,
                            slope=slope,
                            river_mask=river_mask,
                            slope_penalty=slope_penalty,
                            river_cross_penalty=river_cross_penalty * 1.1,
                        )
                        collector_added += 1
                    numeric["collector_classic_trace_count"] = float(len(traces))

    if collector_backend != "classic_turtle":
        for bi, block in enumerate(collector_blocks):
            if float(getattr(block, "area", 0.0) or 0.0) < max(2.0 * collector_spacing_m * collector_spacing_m, max_local_block_area_m2 * 0.65):
                continue
            geom = _subtract_river_setback(block, river_union, river_setback_m)
            for part in _iter_polys_geom(geom):
                area = float(getattr(part, "area", 0.0) or 0.0)
                if area < max(collector_spacing_m * collector_spacing_m * 2.0, 25_000.0):
                    continue
                angle = _dominant_axis_angle_deg(part)
                angle = _styled_axis_angle_deg(angle, style, rng, "collector")
                max_lines = int(min(16, max(1, area / max(collector_spacing_m * collector_spacing_m * 4.0, 1.0))))
                lines = _parallel_lines_in_polygon(
                    part,
                    spacing_m=float(collector_spacing_m),
                    angle_deg=angle,
                    jitter_ratio=float(collector_jitter if style != "grid" else 0.0),
                    rng=np.random.default_rng(seed + 4200 + bi),
                    min_length_m=max(80.0, collector_spacing_m * 0.8),
                    max_lines=max_lines,
                )
                for line in lines:
                    _append_line_edge(
                        line,
                        road_class="collector",
                        width_m=11.0,
                        render_order=1,
                        nodes=nodes,
                        edges=edges,
                        extent_m=extent_m,
                        slope=slope,
                        river_mask=river_mask,
                        slope_penalty=slope_penalty,
                        river_cross_penalty=river_cross_penalty * 1.1,
                    )
                    collector_added += 1
        notes.append("collector_generator:grid_clip")
    else:
        notes.append("collector_generator:classic_turtle")
    numeric["collector_added_count"] = float(collector_added)

    # Local lines are generated after collectors so they subdivide the updated blocks.
    local_blocks, river_union = _build_block_polygons_from_network(
        extent_m=extent_m,
        nodes=nodes,
        edges=edges,
        river_areas=river_areas,
    )
    local_backend = (local_generator or "classic_sprawl").lower()
    local_added = 0
    local_need_grid_supplement = False
    local_grid_supplement_budget: Optional[int] = None
    supplement_added = 0
    pending_local_entries: list[dict] = []
    if local_backend == "classic_sprawl":
        try:
            from engine.roads.classic_local_fill import LocalClassicFillConfig, generate_classic_local_fill  # type: ignore
        except Exception:
            local_backend = "grid_clip"
            notes.append("local_generator_degraded:grid_clip")
            numeric["local_classic_degraded"] = 1.0
        else:
            try:
                numeric["local_classic_enabled"] = 1.0
                local_traces, local_cul_flags, local_trace_meta, local_notes, local_numeric = generate_classic_local_fill(
                    extent_m=extent_m,
                    height=height,
                    slope=slope,
                    river_mask=river_mask,
                    river_areas=river_areas,
                    river_union=river_union,
                    nodes=nodes,
                    edges=edges,
                    hubs=list(hubs or []),
                    blocks=list(local_blocks),
                    cfg=LocalClassicFillConfig(
                        local_spacing_m=local_spacing_m,
                        local_classic_probe_step_m=local_classic_probe_step_m,
                        local_classic_seed_spacing_m=local_classic_seed_spacing_m,
                        local_classic_max_trace_len_m=local_classic_max_trace_len_m,
                        local_classic_min_trace_len_m=local_classic_min_trace_len_m,
                        local_classic_turn_limit_deg=local_classic_turn_limit_deg,
                        local_classic_branch_prob=local_classic_branch_prob,
                        local_classic_continue_prob=local_classic_continue_prob,
                        local_classic_culdesac_prob=local_classic_culdesac_prob,
                        local_classic_max_segments_per_block=local_classic_max_segments_per_block,
                        local_classic_max_road_distance_m=local_classic_max_road_distance_m,
                        local_classic_depth_decay_power=local_classic_depth_decay_power,
                        local_community_seed_count_per_block=local_community_seed_count_per_block,
                        local_community_spine_prob=local_community_spine_prob,
                        local_arterial_setback_weight=local_arterial_setback_weight,
                        local_collector_follow_weight=local_collector_follow_weight,
                        slope_straight_threshold_deg=slope_straight_threshold_deg,
                        slope_serpentine_threshold_deg=slope_serpentine_threshold_deg,
                        slope_hard_limit_deg=slope_hard_limit_deg,
                        contour_follow_weight=contour_follow_weight,
                        river_snap_dist_m=river_snap_dist_m,
                        river_parallel_bias_weight=river_parallel_bias_weight,
                        river_avoid_weight=river_avoid_weight,
                        river_setback_m=river_setback_m,
                    ),
                    seed=seed,
                    stream_cb=stream_cb,
                )
            except Exception:
                local_backend = "grid_clip"
                notes.append("local_generator_degraded:grid_clip")
                numeric["local_classic_degraded"] = 1.0
            else:
                notes.extend(local_notes)
                numeric.update({k: float(v) for k, v in local_numeric.items()})
                if not local_traces:
                    local_backend = "grid_clip"
                    notes.append("local_generator_degraded:grid_clip")
                    numeric["local_classic_degraded"] = 1.0
                else:
                    for trace_idx, pts in enumerate(local_traces):
                        cul = bool(trace_idx < len(local_cul_flags) and local_cul_flags[trace_idx])
                        meta = local_trace_meta[trace_idx] if trace_idx < len(local_trace_meta) else None
                        flags = set()
                        if cul:
                            flags.add("culdesac")
                        if bool(getattr(meta, "is_spine_candidate", False)):
                            flags.add("local_spine")
                        pending_local_entries.append(
                            {
                                "pts": list(pts),
                                "cul": cul,
                                "meta": meta,
                                "is_grid_supplement": False,
                                "flags": flags,
                                "length_m": _polyline_length(pts),
                            }
                        )
                        local_added += 1
                    numeric["local_culdesac_edge_count_pre_topology"] = float(sum(1 for v in local_cul_flags if bool(v)))
                    numeric["local_classic_trace_count"] = float(len(local_traces))
                    arterial_count_now = sum(1 for e in edges if str(getattr(e, "road_class", "")) == "arterial")
                    # Use an arterial-anchored local target; pre-topology collector counts are unstable and
                    # often shrink after intersection/syntax postprocess, which previously suppressed
                    # supplement when it was still needed.
                    min_local_target = max(24, arterial_count_now * 4)
                    if local_added < min_local_target:
                        local_need_grid_supplement = True
                        deficit = int(max(0, min_local_target - local_added))
                        # Budgeted supplement: add enough extra locals to recover hierarchy density,
                        # but avoid flooding every block with grid stripes.
                        # Budget leaves headroom for downstream intersection/syntax pruning.
                        local_grid_supplement_budget = int(max(16, min(320, deficit * 4)))
                        notes.append(f"local_generator_supplement:grid_clip:{local_added}->{min_local_target}")

    if local_backend != "classic_sprawl" or local_need_grid_supplement:
        for bi, block in enumerate(local_blocks):
            if local_backend == "classic_sprawl" and local_need_grid_supplement and local_grid_supplement_budget is not None:
                if supplement_added >= int(local_grid_supplement_budget):
                    break
            area = float(getattr(block, "area", 0.0) or 0.0)
            if area < max(local_spacing_m * local_spacing_m * 3.0, 4_000.0):
                continue
            geom = _subtract_river_setback(block, river_union, river_setback_m)
            for part in _iter_polys_geom(geom):
                if local_backend == "classic_sprawl" and local_need_grid_supplement and local_grid_supplement_budget is not None:
                    if supplement_added >= int(local_grid_supplement_budget):
                        break
                part_area = float(getattr(part, "area", 0.0) or 0.0)
                if part_area < max(local_spacing_m * local_spacing_m * 2.0, 3_000.0):
                    continue
                angle = _nearest_road_tangent_angle_deg(part, nodes, edges)
                if angle is None:
                    angle = _dominant_axis_angle_deg(part)
                if (bi % 2) == 1:
                    angle += 90.0
                angle = _styled_axis_angle_deg(angle, style, rng, "local")
                max_lines = int(min(36, max(1, part_area / max(local_spacing_m * local_spacing_m * 3.0, 1.0))))
                if local_backend == "classic_sprawl" and local_need_grid_supplement and local_grid_supplement_budget is not None:
                    remaining = max(0, int(local_grid_supplement_budget) - supplement_added)
                    if remaining <= 0:
                        break
                    # In supplement mode, add sparse connectors instead of fully striping each residual block.
                    max_lines = int(max(1, min(max_lines, 2, remaining)))
                lines = _parallel_lines_in_polygon(
                    part,
                    spacing_m=float(local_spacing_m),
                    angle_deg=angle,
                    jitter_ratio=float(local_jitter if style != "grid" else 0.0),
                    rng=np.random.default_rng(seed + 5300 + bi),
                    min_length_m=max(26.0, local_spacing_m * 0.9),
                    max_lines=max_lines,
                )
                for line in lines:
                    pts = _line_to_points(line)
                    if len(pts) < 2:
                        continue
                    mid = pts[len(pts) // 2]
                    d_col = min(
                        _nearest_distance_to_road_classes(pts[0], nodes, edges, {"collector", "arterial"}),
                        _nearest_distance_to_road_classes(pts[-1], nodes, edges, {"collector", "arterial"}),
                        _nearest_distance_to_road_classes(mid, nodes, edges, {"collector", "arterial"}),
                    )
                    pending_local_entries.append(
                        {
                            "pts": pts,
                            "cul": False,
                            "meta": {
                                "block_idx": int(bi),
                                "is_spine_candidate": bool(_polyline_length(pts) >= max(local_reroute_min_length_m * 1.2, 110.0)),
                                "connected_to_collector": bool(d_col <= max(local_reroute_collector_snap_bias_m * 1.5, 40.0)),
                                "culdesac": False,
                            },
                            "is_grid_supplement": True,
                            "flags": {"local_grid_supplement"},
                            "length_m": _polyline_length(pts),
                        }
                    )
                    local_added += 1
                    supplement_added += 1
                    if local_backend == "classic_sprawl" and local_need_grid_supplement and local_grid_supplement_budget is not None:
                        if supplement_added >= int(local_grid_supplement_budget):
                            break
        if local_backend != "classic_sprawl":
            notes.append("local_generator:grid_clip")
        else:
            notes.append("local_generator_grid_clip_supplement:1")
            if local_grid_supplement_budget is not None:
                notes.append(f"local_generator_grid_clip_supplement_budget:{int(local_grid_supplement_budget)}")
                notes.append(f"local_generator_grid_clip_supplement_added:{int(supplement_added)}")
    else:
        notes.append("local_generator:classic_sprawl")
    if local_backend == "classic_sprawl" and local_need_grid_supplement:
        notes.append("local_generator:classic_sprawl")

    # Hybrid local geometry reroute: keep classic/local topology but reroute selected geometries
    reroute_applied = 0
    reroute_fallback = 0
    reroute_grid_applied = 0
    reroute_candidate_count = 0
    reroute_path_points_sum = 0.0
    reroute_length_gain_sum = 0.0
    reroute_length_gain_n = 0
    reroute_rejected_noodle = 0
    reroute_rejected_gain = 0
    if pending_local_entries and str(local_geometry_mode or "classic_sprawl_rerouted").lower() != "trace_direct":
        try:
            from engine.roads.local_reroute import (  # type: ignore
                LocalRerouteConfig,
                reroute_local_polyline,
                select_local_reroute_candidates,
            )
        except Exception:
            notes.append("local_geometry_reroute:degraded_unavailable")
        else:
            lr_cfg = LocalRerouteConfig(
                local_geometry_mode=str(local_geometry_mode),
                local_reroute_coverage=str(local_reroute_coverage),
                local_reroute_min_length_m=float(local_reroute_min_length_m),
                local_reroute_waypoint_spacing_m=float(local_reroute_waypoint_spacing_m),
                local_reroute_max_waypoints=int(local_reroute_max_waypoints),
                local_reroute_corridor_buffer_m=float(local_reroute_corridor_buffer_m),
                local_reroute_block_margin_m=float(local_reroute_block_margin_m),
                local_reroute_slope_penalty_scale=float(local_reroute_slope_penalty_scale),
                local_reroute_river_penalty_scale=float(local_reroute_river_penalty_scale),
                local_reroute_collector_snap_bias_m=float(local_reroute_collector_snap_bias_m),
                local_reroute_smooth_iters=int(local_reroute_smooth_iters),
                local_reroute_simplify_tol_m=float(local_reroute_simplify_tol_m),
                local_reroute_max_edges_per_city=int(local_reroute_max_edges_per_city),
                local_reroute_apply_to_grid_supplement=bool(local_reroute_apply_to_grid_supplement),
            )
            candidate_idxs = select_local_reroute_candidates(
                pending_local_entries,
                coverage=str(local_reroute_coverage),
                min_length_m=float(local_reroute_min_length_m),
                max_edges=int(local_reroute_max_edges_per_city),
                apply_to_grid_supplement=bool(local_reroute_apply_to_grid_supplement),
            )
            reroute_candidate_count = int(len(candidate_idxs))
            for idx in candidate_idxs:
                entry = pending_local_entries[idx]
                entry_flags = set(entry.get("flags", set()) or set())
                entry_flags.add("local_candidate_reroute")
                entry["flags"] = entry_flags
                meta = entry.get("meta")
                block_idx = -1
                if meta is not None:
                    block_idx = int(getattr(meta, "block_idx", block_idx))
                    if isinstance(meta, dict):
                        block_idx = int(meta.get("block_idx", block_idx))
                block_poly = local_blocks[block_idx] if 0 <= block_idx < len(local_blocks) else None

                def _route_seg(a: Vec2, b: Vec2, corridor_geom, slope_scale: float, river_scale: float):
                    return _route_points_with_cost_mask(
                        a,
                        b,
                        extent_m=extent_m,
                        slope=slope,
                        river_mask=river_mask,
                        road_class="local",
                        corridor_geom=corridor_geom,
                        slope_penalty_scale=slope_scale,
                        river_penalty_scale=river_scale,
                    )

                rerouted_pts, reroute_numeric, reroute_notes = reroute_local_polyline(
                    entry["pts"],
                    route_segment_fn=_route_seg,
                    cfg=lr_cfg,
                    block_poly=block_poly,
                    river_union=river_union,
                    river_setback_m=float(river_setback_m),
                )
                for rn in reroute_notes:
                    if rn.startswith("local_reroute:") and "fallback" in rn:
                        # keep notes compact
                        continue
                if float(reroute_numeric.get("applied", 0.0)) > 0.5 and len(rerouted_pts) >= 2:
                    orig_pts = list(entry.get("pts", []) or [])
                    orig_len = max(float(entry.get("length_m", 0.0) or 0.0), _polyline_length(orig_pts), 1e-6)
                    new_len = _polyline_length(rerouted_pts)
                    end_span = _polyline_endpoint_span(rerouted_pts)
                    sinuosity = new_len / max(end_span, 1e-6)
                    length_gain_ratio = float(reroute_numeric.get("length_gain_ratio", (new_len / orig_len)))
                    meta_connected = False
                    if isinstance(meta, dict):
                        meta_connected = bool(meta.get("connected_to_collector", False))
                    elif meta is not None:
                        meta_connected = bool(getattr(meta, "connected_to_collector", False))
                    soft_len_cap = max(
                        180.0,
                        min(
                            max(float(local_classic_max_trace_len_m) * 1.6, float(local_spacing_m) * 4.0),
                            float(local_spacing_m) * 5.5,
                        ),
                    )
                    gain_cap = 2.75 if meta_connected else 2.35
                    reject_noodle = bool(
                        (new_len > max(soft_len_cap, orig_len * gain_cap))
                        or (new_len > max(float(local_spacing_m) * 3.2, 220.0) and sinuosity > 4.8)
                        or (end_span < 1e-6 and new_len > 40.0)
                    )
                    reject_gain = bool(length_gain_ratio > (3.4 if meta_connected else 2.9))
                    if reject_noodle or reject_gain:
                        reroute_fallback += 1
                        if reject_noodle:
                            reroute_rejected_noodle += 1
                        if reject_gain:
                            reroute_rejected_gain += 1
                        entry_flags = set(entry.get("flags", set()) or set())
                        entry_flags.add("local_reroute_rejected")
                        if reject_noodle:
                            entry_flags.add("local_reroute_rejected_noodle")
                        entry["flags"] = entry_flags
                        continue
                    entry["pts"] = rerouted_pts
                    entry["length_m"] = float(new_len)
                    entry_flags = set(entry.get("flags", set()) or set())
                    entry_flags.add("local_rerouted")
                    entry["flags"] = entry_flags
                    reroute_applied += 1
                    reroute_path_points_sum += float(reroute_numeric.get("path_points", len(rerouted_pts)))
                    reroute_length_gain_sum += float(length_gain_ratio)
                    reroute_length_gain_n += 1
                    if bool(entry.get("is_grid_supplement", False)):
                        reroute_grid_applied += 1
                else:
                    reroute_fallback += 1
            if reroute_candidate_count > 0:
                notes.append(f"local_geometry_reroute:applied:{reroute_applied}/{reroute_candidate_count}")
                notes.append(f"local_reroute_coverage:{str(local_reroute_coverage)}")
                if reroute_rejected_noodle > 0:
                    notes.append(f"local_reroute_rejected_noodle:{reroute_rejected_noodle}")

    numeric["local_reroute_candidate_count"] = float(reroute_candidate_count)
    numeric["local_reroute_applied_count"] = float(reroute_applied)
    numeric["local_reroute_fallback_count"] = float(reroute_fallback)
    numeric["local_reroute_grid_supplement_applied_count"] = float(reroute_grid_applied)
    numeric["local_reroute_avg_path_points"] = float(reroute_path_points_sum / reroute_applied) if reroute_applied > 0 else 0.0
    numeric["local_reroute_avg_length_gain_ratio"] = (
        float(reroute_length_gain_sum / reroute_length_gain_n) if reroute_length_gain_n > 0 else 0.0
    )
    numeric["local_reroute_rejected_noodle_count"] = float(reroute_rejected_noodle)
    numeric["local_reroute_rejected_gain_count"] = float(reroute_rejected_gain)
    numeric["local_grid_supplement_budget"] = float(local_grid_supplement_budget or 0)
    numeric["local_grid_supplement_added_count"] = float(supplement_added)
    numeric["local_grid_supplement_used_ratio"] = (
        float(supplement_added / max(int(local_grid_supplement_budget or 0), 1))
        if local_grid_supplement_budget is not None
        else 0.0
    )

    # Append local edges after optional reroute so intersections see final local geometry.
    for entry in pending_local_entries:
        entry_flags = set(entry.get("flags", set()) or set())
        cul = bool(entry.get("cul", False) or ("culdesac" in entry_flags))
        if cul:
            entry_flags.add("culdesac")
        _append_polyline_edge(
            entry.get("pts", []),
            road_class="local",
            width_m=6.0,
            render_order=2,
            edge_id_suffix="-cul" if cul else "",
            edge_flags=entry_flags or None,
            nodes=nodes,
            edges=edges,
            extent_m=extent_m,
            slope=slope,
            river_mask=river_mask,
            slope_penalty=slope_penalty * 0.8,
            river_cross_penalty=river_cross_penalty * 1.25,
        )
    numeric["local_added_count"] = float(local_added)
    return notes, numeric


def _dedupe_and_snap(
    nodes: List[BuiltRoadNode],
    edges: List[BuiltRoadEdge],
    snap_tol: float = 0.5,
) -> Tuple[List[BuiltRoadNode], List[BuiltRoadEdge], Dict[str, int]]:
    # Snap nodes by grid bucket, preserving first node in each bucket.
    buckets: Dict[Tuple[int, int], str] = {}
    node_alias: Dict[str, str] = {}
    node_map: Dict[str, BuiltRoadNode] = {}
    for node in nodes:
        key = (int(round(node.pos.x / snap_tol)), int(round(node.pos.y / snap_tol)))
        canonical_id = buckets.get(key)
        if canonical_id is None:
            buckets[key] = node.id
            canonical_id = node.id
            node_map[canonical_id] = node
        else:
            canonical = node_map[canonical_id]
            if node.pos.distance_to(canonical.pos) > snap_tol:
                # hash collision; keep separate bucket by exact coordinate fallback
                key2 = (int(round(node.pos.x / (snap_tol * 0.25))), int(round(node.pos.y / (snap_tol * 0.25))))
                if key2 not in buckets:
                    buckets[key2] = node.id
                    canonical_id = node.id
                    node_map[node.id] = node
        node_alias[node.id] = canonical_id

    deduped_edges: List[BuiltRoadEdge] = []
    seen_pairs: Dict[Tuple[str, str, str], int] = {}
    duplicate_count = 0
    zero_count = 0
    for edge in edges:
        u = node_alias.get(edge.u, edge.u)
        v = node_alias.get(edge.v, edge.v)
        if u == v:
            zero_count += 1
            continue
        pair = tuple(sorted((u, v)) + [edge.road_class])
        if pair in seen_pairs:
            duplicate_count += 1
            idx = seen_pairs[pair]
            merged_flags = _edge_flags(deduped_edges[idx]) | _edge_flags(edge)
            deduped_edges[idx] = BuiltRoadEdge(
                id=f"edge-{idx}{_edge_id_suffix_from_flags(merged_flags)}",
                u=deduped_edges[idx].u,
                v=deduped_edges[idx].v,
                road_class=deduped_edges[idx].road_class,
                weight=deduped_edges[idx].weight,
                length_m=deduped_edges[idx].length_m,
                river_crossings=deduped_edges[idx].river_crossings,
                width_m=deduped_edges[idx].width_m,
                render_order=deduped_edges[idx].render_order,
                path_points=deduped_edges[idx].path_points,
                flags=merged_flags,
            )
            continue
        idx = len(deduped_edges)
        seen_pairs[pair] = idx
        flags = _edge_flags(edge)
        deduped_edges.append(
            BuiltRoadEdge(
                id=f"edge-{idx}{_edge_id_suffix_from_flags(flags)}",
                u=u,
                v=v,
                road_class=edge.road_class,
                weight=edge.weight,
                length_m=edge.length_m,
                river_crossings=edge.river_crossings,
                width_m=edge.width_m,
                render_order=edge.render_order,
                path_points=edge.path_points,
                flags=flags,
            )
        )

    used_nodes = set()
    for edge in deduped_edges:
        used_nodes.add(edge.u)
        used_nodes.add(edge.v)
    deduped_nodes = [node_map[nid] for nid in node_map if nid in used_nodes]
    deduped_nodes.sort(key=lambda n: n.id)

    return deduped_nodes, deduped_edges, {
        "duplicate_edge_count": duplicate_count,
        "zero_length_edge_count": zero_count,
    }


def _illegal_intersection_count(nodes: Sequence[BuiltRoadNode], edges: Sequence[BuiltRoadEdge]) -> int:
    node_lookup = {n.id: n for n in nodes}
    spatial = SpatialHashIndex(cell_size=128.0)
    segs: Dict[str, Segment] = {}
    owners: Dict[str, BuiltRoadEdge] = {}
    count = 0
    seen_pairs: Set[Tuple[str, str]] = set()
    for edge in edges:
        a = node_lookup[edge.u].pos
        b = node_lookup[edge.v].pos
        seg = Segment(a, b)
        key = edge.id
        for other_key in spatial.query(seg.bbox()):
            pair = tuple(sorted((key, other_key)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            other_edge = owners[other_key]
            if {edge.u, edge.v} & {other_edge.u, other_edge.v}:
                continue
            hit = segment_intersection(seg, segs[other_key])
            if hit.kind in ("point", "overlap"):
                count += 1
        spatial.insert(key, seg.bbox())
        segs[key] = seg
        owners[key] = edge
    return count


def _metrics(nodes: List[BuiltRoadNode], edges: List[BuiltRoadEdge], extra: Dict[str, int]) -> Dict[str, float]:
    g = nx.Graph()
    for node in nodes:
        g.add_node(node.id)
    total_weight = 0.0
    bridge_count = 0
    for edge in edges:
        g.add_edge(edge.u, edge.v)
        total_weight += float(edge.weight)
        if edge.river_crossings > 0:
            bridge_count += 1

    connected = g.number_of_nodes() == 0 or nx.is_connected(g)
    largest = 0
    if g.number_of_nodes():
        largest = max(len(comp) for comp in nx.connected_components(g))
    connectivity_ratio = (largest / float(g.number_of_nodes())) if g.number_of_nodes() else 1.0
    dead_end_count = sum(1 for node_id in g.nodes if g.degree(node_id) == 1)

    return {
        "connected": float(1.0 if connected else 0.0),
        "connectivity_ratio": float(connectivity_ratio),
        "dead_end_count": float(dead_end_count),
        "duplicate_edge_count": float(extra.get("duplicate_edge_count", 0)),
        "zero_length_edge_count": float(extra.get("zero_length_edge_count", 0)),
        "illegal_intersection_count": float(_illegal_intersection_count(nodes, edges)),
        "bridge_count": float(bridge_count),
        "avg_edge_weight": float(total_weight / len(edges)) if edges else 0.0,
    }


def generate_roads(
    hubs: Sequence[HubPoint],
    extent_m: float,
    height: Optional[np.ndarray],
    slope: np.ndarray,
    river_mask: np.ndarray,
    k_neighbors: int,
    loop_budget: int,
    branch_steps: int,
    slope_penalty: float,
    river_cross_penalty: float,
    seed: int,
    road_style: str = "skeleton",
    collector_spacing_m: float = 420.0,
    local_spacing_m: float = 130.0,
    collector_jitter: float = 0.16,
    local_jitter: float = 0.22,
    local_generator: str = "classic_sprawl",
    local_geometry_mode: str = "classic_sprawl_rerouted",
    local_reroute_coverage: str = "selective",
    local_reroute_min_length_m: float = 70.0,
    local_reroute_waypoint_spacing_m: float = 26.0,
    local_reroute_max_waypoints: int = 16,
    local_reroute_corridor_buffer_m: float = 38.0,
    local_reroute_block_margin_m: float = 2.0,
    local_reroute_slope_penalty_scale: float = 1.15,
    local_reroute_river_penalty_scale: float = 1.35,
    local_reroute_collector_snap_bias_m: float = 22.0,
    local_reroute_smooth_iters: int = 1,
    local_reroute_simplify_tol_m: float = 3.0,
    local_reroute_max_edges_per_city: int = 180,
    local_reroute_apply_to_grid_supplement: bool = True,
    local_classic_probe_step_m: float = 18.0,
    local_classic_seed_spacing_m: float = 110.0,
    local_classic_max_trace_len_m: float = 420.0,
    local_classic_min_trace_len_m: float = 48.0,
    local_classic_turn_limit_deg: float = 54.0,
    local_classic_branch_prob: float = 0.62,
    local_classic_continue_prob: float = 0.70,
    local_classic_culdesac_prob: float = 0.42,
    local_classic_max_segments_per_block: int = 28,
    local_classic_max_road_distance_m: float = 500.0,
    local_classic_depth_decay_power: float = 1.5,
    local_community_seed_count_per_block: int = 3,
    local_community_spine_prob: float = 0.28,
    local_arterial_setback_weight: float = 0.5,
    local_collector_follow_weight: float = 0.9,
    river_setback_m: float = 18.0,
    minor_bridge_budget: int = 4,
    max_local_block_area_m2: float = 180000.0,
    collector_generator: str = "classic_turtle",
    classic_probe_step_m: float = 24.0,
    classic_seed_spacing_m: float = 260.0,
    classic_max_trace_len_m: float = 1800.0,
    classic_min_trace_len_m: float = 120.0,
    classic_turn_limit_deg: float = 38.0,
    classic_branch_prob: float = 0.35,
    classic_continue_prob: float = 0.80,
    classic_culdesac_prob: float = 0.18,
    classic_max_queue_size: int = 2000,
    classic_max_segments: int = 1200,
    classic_max_arterial_distance_m: float = 800.0,
    classic_depth_decay_power: float = 1.5,
    slope_straight_threshold_deg: float = 5.0,
    slope_serpentine_threshold_deg: float = 15.0,
    slope_hard_limit_deg: float = 22.0,
    contour_follow_weight: float = 0.9,
    arterial_align_weight: float = 0.6,
    hub_seek_weight: float = 0.25,
    river_snap_dist_m: float = 28.0,
    river_parallel_bias_weight: float = 1.0,
    river_avoid_weight: float = 1.2,
    tensor_grid_resolution: int = 96,
    tensor_step_m: float = 24.0,
    tensor_seed_spacing_m: float = 260.0,
    tensor_max_trace_len_m: float = 1800.0,
    tensor_min_trace_len_m: float = 120.0,
    tensor_turn_limit_deg: float = 38.0,
    tensor_water_tangent_weight: float = 1.15,
    tensor_contour_tangent_weight: float = 0.95,
    tensor_arterial_align_weight: float = 0.70,
    tensor_hub_attract_weight: float = 0.35,
    tensor_water_influence_m: float = 320.0,
    tensor_arterial_influence_m: float = 380.0,
    intersection_snap_radius_m: float = 12.0,
    intersection_t_junction_radius_m: float = 18.0,
    intersection_split_tolerance_m: float = 1.5,
    min_dangle_length_m: float = 35.0,
    syntax_enable: bool = True,
    syntax_choice_radius_hops: int = 10,
    syntax_prune_low_choice_collectors: bool = True,
    syntax_prune_quantile: float = 0.15,
    river_areas: Optional[Sequence[object]] = None,
    progress_cb: Optional[RoadProgressCallback] = None,
    stream_cb: Optional[RoadStreamCallback] = None,
) -> RoadBuildResult:
    # Deprecated compatibility alias: keep accepting tensor-streamline config names while routing
    # collector generation through the classic turtle growth backend.
    if (collector_generator or "").lower() == "tensor_streamline":
        classic_probe_step_m = float(tensor_step_m)
        classic_seed_spacing_m = float(tensor_seed_spacing_m)
        classic_max_trace_len_m = float(tensor_max_trace_len_m)
        classic_min_trace_len_m = float(tensor_min_trace_len_m)
        classic_turn_limit_deg = float(tensor_turn_limit_deg)
        contour_follow_weight = float(max(contour_follow_weight, tensor_contour_tangent_weight))
        arterial_align_weight = float(max(arterial_align_weight, tensor_arterial_align_weight))
        hub_seek_weight = float(max(hub_seek_weight, tensor_hub_attract_weight))
        river_parallel_bias_weight = float(max(river_parallel_bias_weight, tensor_water_tangent_weight))

    nodes: List[BuiltRoadNode] = [
        BuiltRoadNode(id=h.id, pos=h.pos, kind="hub", source_hub_id=h.id) for h in hubs
    ]
    if len(hubs) <= 1:
        metrics = _metrics(nodes, [], {"duplicate_edge_count": 0, "zero_length_edge_count": 0})
        return RoadBuildResult(nodes=nodes, edges=[], candidate_debug=[], metrics=metrics)

    graph, candidate_debug = _build_candidate_graph(
        hubs=hubs,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        k_neighbors=k_neighbors,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
    )
    _emit_road_progress(progress_cb, "roads.candidate_graph", 0.08, "Built road candidate graph")

    selected_backbone = _generate_backbone_edges(graph, loop_budget=loop_budget)
    _emit_road_progress(progress_cb, "roads.backbone", 0.16, "Selected arterial backbone")
    edges: List[BuiltRoadEdge] = []
    for u, v, data in selected_backbone:
        edges.append(
            BuiltRoadEdge(
                id=f"edge-{len(edges)}",
                u=u,
                v=v,
                road_class="arterial",
                weight=float(data.get("weight", 0.0)),
                length_m=float(data.get("length_m", 0.0)),
                river_crossings=int(data.get("river_crossings", 0)),
                width_m=18.0,
                render_order=0,
            )
        )

    _generate_branches(
        hubs=hubs,
        nodes=nodes,
        edges=edges,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        branch_steps=branch_steps,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
        seed=seed,
        stream_cb=stream_cb,
    )
    _emit_road_progress(progress_cb, "roads.branches", 0.24, "Generated branch roads")

    nodes, edges, extra = _dedupe_and_snap(nodes, edges)
    _emit_road_progress(progress_cb, "roads.snap", 0.30, "Snapped and deduplicated backbone/branches")
    _route_all_edges(
        nodes=nodes,
        edges=edges,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
    )
    _emit_road_progress(progress_cb, "roads.route_initial", 0.40, "Routed arterial and branch geometry")
    hierarchy_notes, hierarchy_numeric = _generate_hierarchy_linework(
        extent_m=extent_m,
        height=height,
        slope=slope,
        river_mask=river_mask,
        nodes=nodes,
        edges=edges,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
        seed=seed,
        road_style=road_style,
        collector_spacing_m=collector_spacing_m,
        local_spacing_m=local_spacing_m,
        collector_jitter=collector_jitter,
        local_jitter=local_jitter,
        local_generator=local_generator,
        local_geometry_mode=local_geometry_mode,
        local_reroute_coverage=local_reroute_coverage,
        local_reroute_min_length_m=local_reroute_min_length_m,
        local_reroute_waypoint_spacing_m=local_reroute_waypoint_spacing_m,
        local_reroute_max_waypoints=local_reroute_max_waypoints,
        local_reroute_corridor_buffer_m=local_reroute_corridor_buffer_m,
        local_reroute_block_margin_m=local_reroute_block_margin_m,
        local_reroute_slope_penalty_scale=local_reroute_slope_penalty_scale,
        local_reroute_river_penalty_scale=local_reroute_river_penalty_scale,
        local_reroute_collector_snap_bias_m=local_reroute_collector_snap_bias_m,
        local_reroute_smooth_iters=local_reroute_smooth_iters,
        local_reroute_simplify_tol_m=local_reroute_simplify_tol_m,
        local_reroute_max_edges_per_city=local_reroute_max_edges_per_city,
        local_reroute_apply_to_grid_supplement=local_reroute_apply_to_grid_supplement,
        local_classic_probe_step_m=local_classic_probe_step_m,
        local_classic_seed_spacing_m=local_classic_seed_spacing_m,
        local_classic_max_trace_len_m=local_classic_max_trace_len_m,
        local_classic_min_trace_len_m=local_classic_min_trace_len_m,
        local_classic_turn_limit_deg=local_classic_turn_limit_deg,
        local_classic_branch_prob=local_classic_branch_prob,
        local_classic_continue_prob=local_classic_continue_prob,
        local_classic_culdesac_prob=local_classic_culdesac_prob,
        local_classic_max_segments_per_block=local_classic_max_segments_per_block,
        local_classic_max_road_distance_m=local_classic_max_road_distance_m,
        local_classic_depth_decay_power=local_classic_depth_decay_power,
        local_community_seed_count_per_block=local_community_seed_count_per_block,
        local_community_spine_prob=local_community_spine_prob,
        local_arterial_setback_weight=local_arterial_setback_weight,
        local_collector_follow_weight=local_collector_follow_weight,
        river_setback_m=river_setback_m,
        minor_bridge_budget=minor_bridge_budget,
        max_local_block_area_m2=max_local_block_area_m2,
        collector_generator=collector_generator,
        classic_probe_step_m=classic_probe_step_m,
        classic_seed_spacing_m=classic_seed_spacing_m,
        classic_max_trace_len_m=classic_max_trace_len_m,
        classic_min_trace_len_m=classic_min_trace_len_m,
        classic_turn_limit_deg=classic_turn_limit_deg,
        classic_branch_prob=classic_branch_prob,
        classic_continue_prob=classic_continue_prob,
        classic_culdesac_prob=classic_culdesac_prob,
        classic_max_queue_size=classic_max_queue_size,
        classic_max_segments=classic_max_segments,
        classic_max_arterial_distance_m=classic_max_arterial_distance_m,
        classic_depth_decay_power=classic_depth_decay_power,
        slope_straight_threshold_deg=slope_straight_threshold_deg,
        slope_serpentine_threshold_deg=slope_serpentine_threshold_deg,
        slope_hard_limit_deg=slope_hard_limit_deg,
        contour_follow_weight=contour_follow_weight,
        arterial_align_weight=arterial_align_weight,
        hub_seek_weight=hub_seek_weight,
        river_snap_dist_m=river_snap_dist_m,
        river_parallel_bias_weight=river_parallel_bias_weight,
        river_avoid_weight=river_avoid_weight,
        tensor_grid_resolution=tensor_grid_resolution,
        tensor_step_m=tensor_step_m,
        tensor_seed_spacing_m=tensor_seed_spacing_m,
        tensor_max_trace_len_m=tensor_max_trace_len_m,
        tensor_min_trace_len_m=tensor_min_trace_len_m,
        tensor_turn_limit_deg=tensor_turn_limit_deg,
        tensor_water_tangent_weight=tensor_water_tangent_weight,
        tensor_contour_tangent_weight=tensor_contour_tangent_weight,
        tensor_arterial_align_weight=tensor_arterial_align_weight,
        tensor_hub_attract_weight=tensor_hub_attract_weight,
        tensor_water_influence_m=tensor_water_influence_m,
        tensor_arterial_influence_m=tensor_arterial_influence_m,
        hubs=hubs,
        river_areas=river_areas,
        stream_cb=stream_cb,
    )
    _emit_road_progress(progress_cb, "roads.hierarchy", 0.72, "Generated collector and local hierarchy")
    try:
        from engine.roads.intersections import apply_intersection_operators  # type: ignore
    except Exception:
        inter_notes: list[str] = ["intersection_ops:degraded_unavailable"]
        inter_numeric: dict[str, float] = {}
    else:
        nodes, edges, inter_notes, inter_numeric = apply_intersection_operators(
            nodes=nodes,
            edges=edges,
            snap_radius_m=float(intersection_snap_radius_m),
            t_junction_radius_m=float(intersection_t_junction_radius_m),
            split_tolerance_m=float(intersection_split_tolerance_m),
            min_dangle_length_m=float(min_dangle_length_m),
        )
    _emit_road_progress(progress_cb, "roads.intersections", 0.82, "Applied intersection operators")
    try:
        from engine.roads.syntax import apply_syntax_postprocess  # type: ignore
    except Exception:
        syntax_notes: list[str] = ["syntax:degraded_unavailable"]
        syntax_numeric: dict[str, float] = {}
    else:
        edges, syntax_notes, syntax_numeric = apply_syntax_postprocess(
            nodes=nodes,
            edges=edges,
            syntax_enable=bool(syntax_enable),
            choice_radius_hops=int(syntax_choice_radius_hops),
            prune_low_choice_collectors=bool(syntax_prune_low_choice_collectors),
            prune_quantile=float(syntax_prune_quantile),
        )
    _emit_road_progress(progress_cb, "roads.syntax", 0.88, "Applied space syntax postprocess")
    nodes, edges, extra2 = _dedupe_and_snap(nodes, edges)
    extra = {
        "duplicate_edge_count": int(extra.get("duplicate_edge_count", 0)) + int(extra2.get("duplicate_edge_count", 0)),
        "zero_length_edge_count": int(extra.get("zero_length_edge_count", 0)) + int(extra2.get("zero_length_edge_count", 0)),
    }
    _route_all_edges(
        nodes=nodes,
        edges=edges,
        extent_m=extent_m,
        slope=slope,
        river_mask=river_mask,
        slope_penalty=slope_penalty,
        river_cross_penalty=river_cross_penalty,
    )
    _emit_road_progress(progress_cb, "roads.route_final", 0.94, "Finalized routed road geometry")

    # Street-run aggregation: aggregate fragmented edges into semantically continuous street segments
    street_run_metrics_data: dict[str, float] = {}
    try:
        from engine.roads.street_run import (
            aggregate_street_runs,
            street_run_metrics as calc_street_run_metrics,
            spine_street_run_metrics as calc_spine_metrics,
            road_class_street_run_metrics as calc_class_metrics,
        )
        street_runs, street_run_diag = aggregate_street_runs(edges=edges, nodes=nodes)
        street_run_metrics_data.update(street_run_diag)
        street_run_metrics_data.update(calc_street_run_metrics(street_runs))
        street_run_metrics_data.update(calc_spine_metrics(street_runs))
        street_run_metrics_data.update(calc_class_metrics(street_runs))
    except Exception:
        street_run_metrics_data["street_run_aggregation_failed"] = 1.0
    _emit_road_progress(progress_cb, "roads.street_runs", 0.97, "Aggregated street runs")

    local_cul_final = sum(1 for e in edges if str(getattr(e, "road_class", "")) == "local" and _has_edge_flag(e, "culdesac"))
    local_edges_final = [e for e in edges if str(getattr(e, "road_class", "")) == "local"]
    local_two_point_count = 0
    local_gt2_count = 0
    for e in local_edges_final:
        pts = list(getattr(e, "path_points", []) or [])
        if len(pts) <= 2:
            local_two_point_count += 1
        else:
            local_gt2_count += 1
    metrics = _metrics(nodes, edges, extra)
    metrics.update({k: float(v) for k, v in hierarchy_numeric.items()})
    metrics.update({k: float(v) for k, v in inter_numeric.items()})
    metrics.update({k: float(v) for k, v in syntax_numeric.items()})
    metrics.update({k: float(v) for k, v in street_run_metrics_data.items()})
    metrics["local_culdesac_edge_count_final"] = float(local_cul_final)
    local_cul_pre = float(metrics.get("local_culdesac_edge_count_pre_topology", 0.0))
    metrics["local_culdesac_preserved_ratio"] = float(local_cul_final / local_cul_pre) if local_cul_pre > 0.0 else 0.0
    metrics["local_two_point_edge_count"] = float(local_two_point_count)
    metrics["local_edges_with_gt2_points_count"] = float(local_gt2_count)
    metrics["local_two_point_edge_ratio"] = float(local_two_point_count / len(local_edges_final)) if local_edges_final else 0.0
    # Encode textual notes in numeric flags/metrics-compatible shape; generator will translate to human notes.
    metrics["collector_generator_classic_turtle"] = float(1.0 if any("collector_generator:classic_turtle" == n for n in hierarchy_notes) else 0.0)
    metrics["collector_generator_tensor_streamline"] = float(1.0 if any("collector_generator:tensor_streamline" == n for n in hierarchy_notes) else 0.0)
    metrics["collector_generator_grid_clip"] = float(1.0 if any("collector_generator:grid_clip" == n for n in hierarchy_notes) else 0.0)
    metrics["collector_generator_degraded"] = float(1.0 if any("collector_generator_degraded" in n for n in hierarchy_notes) else 0.0)
    metrics["local_generator_classic_sprawl"] = float(1.0 if any("local_generator:classic_sprawl" == n for n in hierarchy_notes) else 0.0)
    metrics["local_generator_grid_clip"] = float(1.0 if any("local_generator:grid_clip" == n for n in hierarchy_notes) else 0.0)
    metrics["local_generator_degraded"] = float(1.0 if any("local_generator_degraded" in n for n in hierarchy_notes) else 0.0)
    metrics["syntax_note_count"] = float(len(syntax_notes))
    metrics["intersection_note_count"] = float(len(inter_notes))
    _emit_road_progress(progress_cb, "roads.done", 1.0, "Road generation complete")
    return RoadBuildResult(nodes=nodes, edges=edges, candidate_debug=candidate_debug, metrics=metrics)
