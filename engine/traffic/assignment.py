from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Dict, List, Optional, Sequence, Tuple

from engine.models import HubRecord, RoadEdgeRecord, RoadNetwork, TrafficEdgeFlow


@dataclass
class TrafficAssignmentResult:
    edge_flows: List[TrafficEdgeFlow]
    max_flow: float
    max_congestion_ratio: float
    od_pair_count: int


def _capacity_for_edge(edge: RoadEdgeRecord) -> float:
    base = 1200.0 if edge.road_class == 'arterial' else 420.0
    length_factor = max(0.6, min(1.8, edge.length_m / 140.0 if edge.length_m > 0 else 0.6))
    return float(base * length_factor)


def _dijkstra_path(
    adjacency: Dict[str, List[Tuple[str, float]]],
    source: str,
    target: str,
) -> Optional[List[str]]:
    if source == target:
        return [source]
    pq: List[Tuple[float, str]] = [(0.0, source)]
    dist: Dict[str, float] = {source: 0.0}
    prev: Dict[str, str] = {}
    seen = set()
    while pq:
        d, node = heapq.heappop(pq)
        if node in seen:
            continue
        seen.add(node)
        if node == target:
            break
        for nbr, w in adjacency.get(node, []):
            nd = d + float(w)
            if nd < dist.get(nbr, float('inf')):
                dist[nbr] = nd
                prev[nbr] = node
                heapq.heappush(pq, (nd, nbr))
    if target not in dist:
        return None
    path = [target]
    cur = target
    while cur != source:
        cur = prev[cur]
        path.append(cur)
    path.reverse()
    return path


def _road_index(road_network: RoadNetwork) -> Tuple[Dict[Tuple[str, str], RoadEdgeRecord], Dict[str, List[Tuple[str, float]]]]:
    edge_lookup: Dict[Tuple[str, str], RoadEdgeRecord] = {}
    adjacency: Dict[str, List[Tuple[str, float]]] = {}
    for edge in road_network.edges:
        key = tuple(sorted((edge.u, edge.v)))
        edge_lookup[key] = edge
        adjacency.setdefault(edge.u, []).append((edge.v, float(edge.weight)))
        adjacency.setdefault(edge.v, []).append((edge.u, float(edge.weight)))
    return edge_lookup, adjacency


def _nearest_higher_tier(hub: HubRecord, hubs: Sequence[HubRecord]) -> Optional[HubRecord]:
    best = None
    best_dist = float('inf')
    for other in hubs:
        if other.id == hub.id:
            continue
        if other.tier >= hub.tier:
            continue
        dx = other.x - hub.x
        dy = other.y - hub.y
        d = dx * dx + dy * dy
        if d < best_dist:
            best = other
            best_dist = d
    return best


def _od_pairs(hubs: Sequence[HubRecord]) -> List[Tuple[str, str, float]]:
    pairs: List[Tuple[str, str, float]] = []
    for i in range(len(hubs)):
        for j in range(i + 1, len(hubs)):
            a = hubs[i]
            b = hubs[j]
            if a.tier == 1 and b.tier == 2 or a.tier == 2 and b.tier == 1:
                demand = 180.0
            elif a.tier == 1 and b.tier == 3 or a.tier == 3 and b.tier == 1:
                demand = 90.0
            elif a.tier == 2 and b.tier == 2:
                demand = 70.0
            elif {a.tier, b.tier} == {2, 3}:
                demand = 45.0
            elif a.tier == 3 and b.tier == 3:
                demand = 20.0
            else:
                demand = 30.0
            pairs.append((a.id, b.id, demand))
    for hub in hubs:
        if hub.tier != 3:
            continue
        target = _nearest_higher_tier(hub, hubs)
        if target is not None:
            pairs.append((hub.id, target.id, 35.0))
    return pairs


def assign_edge_flows(hubs: Sequence[HubRecord], road_network: RoadNetwork) -> TrafficAssignmentResult:
    edge_lookup, adjacency = _road_index(road_network)
    edge_flow_acc: Dict[str, float] = {edge.id: 0.0 for edge in road_network.edges}
    pairs = _od_pairs(hubs)
    od_pair_count = 0

    for source, target, demand in pairs:
        path = _dijkstra_path(adjacency, source, target)
        if not path or len(path) < 2:
            continue
        od_pair_count += 1
        for i in range(len(path) - 1):
            key = tuple(sorted((path[i], path[i + 1])))
            edge = edge_lookup.get(key)
            if edge is None:
                continue
            edge_flow_acc[edge.id] = edge_flow_acc.get(edge.id, 0.0) + float(demand)

    edge_flows: List[TrafficEdgeFlow] = []
    max_flow = 0.0
    max_congestion = 0.0
    for edge in road_network.edges:
        flow = float(edge_flow_acc.get(edge.id, 0.0))
        capacity = _capacity_for_edge(edge)
        congestion = flow / max(capacity, 1e-6)
        max_flow = max(max_flow, flow)
        max_congestion = max(max_congestion, congestion)
        edge_flows.append(
            TrafficEdgeFlow(
                edge_id=edge.id,
                flow=flow,
                capacity=float(capacity),
                congestion_ratio=float(congestion),
                road_class=edge.road_class,
            )
        )

    return TrafficAssignmentResult(
        edge_flows=edge_flows,
        max_flow=float(max_flow),
        max_congestion_ratio=float(max_congestion),
        od_pair_count=int(od_pair_count),
    )
