from __future__ import annotations

from typing import Dict, List, Sequence

from engine.hubs.sampling import HubPoint

from .providers import ToponymyFeature, ToponymyProvider


def _hub_degree_map(edges: Sequence[Dict[str, object]]) -> Dict[str, int]:
    degree: Dict[str, int] = {}
    for edge in edges:
        u = str(edge["u"])
        v = str(edge["v"])
        degree[u] = degree.get(u, 0) + 1
        degree[v] = degree.get(v, 0) + 1
    return degree


def _near_river(hub: HubPoint) -> bool:
    return float(hub.attrs.get("river_distance_m", 1e9)) < 180.0


def assign_hub_names(
    hubs: Sequence[HubPoint],
    road_edges: Sequence[Dict[str, object]],
    provider: ToponymyProvider,
    seed: int,
) -> List[str]:
    degree_map = _hub_degree_map(road_edges)
    max_score = max((h.score for h in hubs), default=1.0)
    features: List[ToponymyFeature] = []
    for hub in hubs:
        features.append(
            ToponymyFeature(
                tier=hub.tier,
                near_river=_near_river(hub),
                bridge_count=0,
                degree=degree_map.get(hub.id, 0),
                centrality=float(hub.score / max(max_score, 1e-9)),
            )
        )
    return provider.generate_names(features, seed)
