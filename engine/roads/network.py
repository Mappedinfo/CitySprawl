from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, pi, sin
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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


@dataclass
class RoadBuildResult:
    nodes: List[BuiltRoadNode]
    edges: List[BuiltRoadEdge]
    candidate_debug: List[Tuple[str, Vec2, Vec2, float]]
    metrics: Dict[str, float]


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
            edge = BuiltRoadEdge(
                id=f"edge-{len(edges)}",
                u=current_id,
                v=new_id,
                road_class="local",
                weight=float(best[0]),
                length_m=current_pos.distance_to(next_pos),
                river_crossings=river_cross,
            )
            edges.append(edge)
            node_lookup[new_id] = nodes[-1]
            current_id = new_id
            current_pos = next_pos
            base_angle = chosen_angle


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
    seen_pairs: Set[Tuple[str, str, str]] = set()
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
            continue
        seen_pairs.add(pair)
        deduped_edges.append(
            BuiltRoadEdge(
                id=f"edge-{len(deduped_edges)}",
                u=u,
                v=v,
                road_class=edge.road_class,
                weight=edge.weight,
                length_m=edge.length_m,
                river_crossings=edge.river_crossings,
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
    slope: np.ndarray,
    river_mask: np.ndarray,
    k_neighbors: int,
    loop_budget: int,
    branch_steps: int,
    slope_penalty: float,
    river_cross_penalty: float,
    seed: int,
) -> RoadBuildResult:
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

    selected_backbone = _generate_backbone_edges(graph, loop_budget=loop_budget)
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
    )

    nodes, edges, extra = _dedupe_and_snap(nodes, edges)
    metrics = _metrics(nodes, edges, extra)
    return RoadBuildResult(nodes=nodes, edges=edges, candidate_debug=candidate_debug, metrics=metrics)
