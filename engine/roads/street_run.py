"""
Street-run aggregation module.

Aggregates fragmented road edges (after intersection split) into semantically
continuous street segments ("street-runs") using a mutual best continuation
(stroke building) algorithm.

Phase 0 implementation: pure post-processing, does not modify generation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import acos, atan2, degrees, pi
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

import numpy as np

from engine.core.geometry import Vec2


@dataclass
class StreetRunConfig:
    """Configuration for street-run aggregation algorithm."""
    max_deflection_deg_spine: float = 12.0  # max angle for spine roads
    max_deflection_deg_local: float = 22.0  # max angle for local roads
    max_deflection_deg_collector: float = 18.0  # max angle for collector roads
    max_deflection_deg_arterial: float = 15.0  # max angle for arterial roads
    width_tolerance_ratio: float = 0.35  # max relative width difference
    spine_flag_bonus: float = 1.5  # score multiplier for spine candidates
    class_mismatch_penalty: float = 0.3  # score multiplier for class mismatch
    min_spine_length_m: float = 500.0  # minimum length to be a spine candidate


@dataclass
class StreetRun:
    """Represents a semantically continuous street segment composed of multiple edges."""
    id: str
    edge_ids: List[str]  # ordered list of edge IDs forming this run
    road_class: str  # dominant road_class (e.g., "minor_local", "major_local")
    flags: FrozenSet[str]  # aggregated flags (e.g., {"local_spine_run"})
    length_m: float  # total length of all edges
    node_ids: List[str]  # ordered list of node IDs (including start/end)
    start_node_id: str
    end_node_id: str
    mean_bearing_deg: float  # average direction of the run
    deflection_sum_deg: float  # cumulative angular deviation
    is_spine_candidate: bool  # whether this run is a spine candidate
    edge_count: int = 0  # number of edges in this run


@dataclass
class _EdgeEndpoint:
    """Internal: represents one endpoint of an edge with its tangent direction."""
    edge_id: str
    node_id: str
    tangent: Vec2  # unit vector pointing away from this endpoint
    road_class: str
    width_m: float
    flags: FrozenSet[str]


def _compute_edge_tangents(
    edges: Sequence[object],
    node_map: Dict[str, object],
) -> Dict[Tuple[str, str], Vec2]:
    """
    Compute tangent vectors for each edge at each of its endpoints.
    
    For edges with path_points, uses the first/last segment direction.
    For edges without path_points, uses the direct u->v direction.
    
    Returns:
        dict[(edge_id, node_id)] -> unit tangent vector pointing away from node
    """
    tangents: Dict[Tuple[str, str], Vec2] = {}
    
    for edge in edges:
        edge_id = str(getattr(edge, "id", ""))
        u_id = str(getattr(edge, "u", ""))
        v_id = str(getattr(edge, "v", ""))
        path_points = getattr(edge, "path_points", None)
        
        if not edge_id or not u_id or not v_id:
            continue
        
        u_node = node_map.get(u_id)
        v_node = node_map.get(v_id)
        
        if u_node is None or v_node is None:
            continue
        
        u_pos = getattr(u_node, "pos", None)
        v_pos = getattr(v_node, "pos", None)
        
        if u_pos is None or v_pos is None:
            continue
        
        # Compute tangent at u endpoint (pointing away from u toward v)
        if path_points and len(path_points) >= 2:
            # Use first segment direction
            p0 = path_points[0]
            p1 = path_points[1]
            tangent_u = (p1 - p0).normalized()
        else:
            tangent_u = (v_pos - u_pos).normalized()
        
        # Compute tangent at v endpoint (pointing away from v toward u)
        if path_points and len(path_points) >= 2:
            # Use last segment direction (reversed)
            p_last = path_points[-1]
            p_prev = path_points[-2]
            tangent_v = (p_prev - p_last).normalized()
        else:
            tangent_v = (u_pos - v_pos).normalized()
        
        tangents[(edge_id, u_id)] = tangent_u
        tangents[(edge_id, v_id)] = tangent_v
    
    return tangents


def _angle_between_vectors(v1: Vec2, v2: Vec2) -> float:
    """
    Compute angle between two vectors in degrees [0, 180].
    
    For continuation scoring, we want the angle between the outgoing tangent
    of one edge and the incoming tangent of another (which should be small
    for good continuation).
    """
    if v1.length() < 1e-9 or v2.length() < 1e-9:
        return 180.0
    
    # Dot product of normalized vectors
    dot = v1.normalized().dot(v2.normalized())
    # Clamp to [-1, 1] to avoid numerical issues with acos
    dot = max(-1.0, min(1.0, dot))
    return degrees(acos(dot))


def _continuation_score(
    tangent_a: Vec2,
    tangent_b: Vec2,
    class_a: str,
    class_b: str,
    width_a: float,
    width_b: float,
    flags_a: FrozenSet[str],
    flags_b: FrozenSet[str],
    config: StreetRunConfig,
) -> Tuple[float, float]:
    """
    Compute continuation score between two edge endpoints at a shared node.
    
    For good continuation, the tangent vectors should point in opposite
    directions (angle close to 180 degrees). We compute the deflection
    as 180 - angle_between.
    
    Returns:
        (score, deflection_deg)
    """
    # For continuation, tangent_a points away from node, tangent_b points away from node
    # Good continuation means they point in opposite directions
    # So we compare tangent_a with -tangent_b
    neg_tangent_b = Vec2(-tangent_b.x, -tangent_b.y)
    angle = _angle_between_vectors(tangent_a, neg_tangent_b)
    deflection_deg = angle  # 0 means perfect alignment, 180 means opposite
    
    # Determine max deflection based on road class
    # Use the more permissive threshold between the two classes
    def _max_deflection_for_class(rc: str) -> float:
        if rc == "arterial":
            return config.max_deflection_deg_arterial
        elif rc == "major_local":
            return config.max_deflection_deg_collector
        elif "local_spine" in flags_a or "local_spine" in flags_b:
            return config.max_deflection_deg_spine
        else:
            return config.max_deflection_deg_local
    
    max_deflection = max(_max_deflection_for_class(class_a), _max_deflection_for_class(class_b))
    
    # Angle score: 1.0 for perfect alignment, 0.0 at max_deflection
    if deflection_deg >= max_deflection:
        return (0.0, deflection_deg)
    
    angle_score = max(0.0, 1.0 - deflection_deg / max_deflection)
    
    # Class match score
    class_match = 1.0 if class_a == class_b else config.class_mismatch_penalty
    
    # Width similarity score
    max_width = max(width_a, width_b)
    if max_width > 0:
        width_diff_ratio = abs(width_a - width_b) / max_width
        width_score = max(0.0, 1.0 - width_diff_ratio / config.width_tolerance_ratio)
    else:
        width_score = 1.0
    
    # Spine bonus: if both edges have local_spine flag
    spine_bonus = 1.0
    if "local_spine" in flags_a and "local_spine" in flags_b:
        spine_bonus = config.spine_flag_bonus
    
    score = angle_score * class_match * width_score * spine_bonus
    return (score, deflection_deg)


def _build_node_to_edges_map(
    edges: Sequence[object],
) -> Dict[str, List[str]]:
    """Build a mapping from node_id to list of incident edge_ids."""
    node_to_edges: Dict[str, List[str]] = {}
    
    for edge in edges:
        edge_id = str(getattr(edge, "id", ""))
        u_id = str(getattr(edge, "u", ""))
        v_id = str(getattr(edge, "v", ""))
        
        if not edge_id or not u_id or not v_id:
            continue
        
        if u_id not in node_to_edges:
            node_to_edges[u_id] = []
        node_to_edges[u_id].append(edge_id)
        
        if v_id not in node_to_edges:
            node_to_edges[v_id] = []
        node_to_edges[v_id].append(edge_id)
    
    return node_to_edges


def _build_continuation_candidates(
    edges: Sequence[object],
    node_map: Dict[str, object],
    edge_map: Dict[str, object],
    tangents: Dict[Tuple[str, str], Vec2],
    node_to_edges: Dict[str, List[str]],
    config: StreetRunConfig,
) -> Tuple[Dict[Tuple[str, str], List[Tuple[str, float]]], Dict[str, int]]:
    """
    Build continuation candidates for each (edge, node) pair.
    
    Returns:
        candidates: dict[(edge_id, node_id)] -> [(candidate_edge_id, score), ...] sorted by score desc
        reject_stats: dict with rejection counts
    """
    candidates: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
    reject_angle_count = 0
    reject_width_count = 0
    
    for node_id, incident_edges in node_to_edges.items():
        if len(incident_edges) < 2:
            continue
        
        # For each pair of edges at this node
        for i, edge_a_id in enumerate(incident_edges):
            edge_a = edge_map.get(edge_a_id)
            if edge_a is None:
                continue
            
            tangent_a = tangents.get((edge_a_id, node_id))
            if tangent_a is None:
                continue
            
            class_a = str(getattr(edge_a, "road_class", "minor_local"))
            width_a = float(getattr(edge_a, "width_m", 8.0))
            flags_a = frozenset(getattr(edge_a, "flags", frozenset()))
            
            cand_list: List[Tuple[str, float]] = []
            
            for j, edge_b_id in enumerate(incident_edges):
                if i == j:
                    continue
                
                edge_b = edge_map.get(edge_b_id)
                if edge_b is None:
                    continue
                
                tangent_b = tangents.get((edge_b_id, node_id))
                if tangent_b is None:
                    continue
                
                class_b = str(getattr(edge_b, "road_class", "minor_local"))
                width_b = float(getattr(edge_b, "width_m", 8.0))
                flags_b = frozenset(getattr(edge_b, "flags", frozenset()))
                
                score, deflection = _continuation_score(
                    tangent_a, tangent_b,
                    class_a, class_b,
                    width_a, width_b,
                    flags_a, flags_b,
                    config,
                )
                
                if score <= 0:
                    # Track rejection reasons
                    max_defl = config.max_deflection_deg_local
                    if deflection >= max_defl:
                        reject_angle_count += 1
                    else:
                        reject_width_count += 1
                    continue
                
                cand_list.append((edge_b_id, score))
            
            # Sort by score descending
            cand_list.sort(key=lambda x: -x[1])
            candidates[(edge_a_id, node_id)] = cand_list
    
    reject_stats = {
        "reject_angle_count": reject_angle_count,
        "reject_width_count": reject_width_count,
    }
    return candidates, reject_stats


def _mutual_best_continuation(
    candidates: Dict[Tuple[str, str], List[Tuple[str, float]]],
    edge_map: Dict[str, object],
) -> Set[Tuple[str, str, str]]:
    """
    Extract mutual best continuation pairs.
    
    Only when edge A picks edge B as best at node N, AND edge B picks edge A
    as best at node N, do we establish a continuation link.
    
    Returns:
        set of (edge_a_id, edge_b_id, shared_node_id) tuples
    """
    mutual_links: Set[Tuple[str, str, str]] = set()
    
    for (edge_a_id, node_id), cand_list in candidates.items():
        if not cand_list:
            continue
        
        # Get best candidate for edge_a at node_id
        best_b_id, best_score = cand_list[0]
        
        # Check if edge_b also picks edge_a as best at this node
        reverse_key = (best_b_id, node_id)
        reverse_cand_list = candidates.get(reverse_key, [])
        
        if not reverse_cand_list:
            continue
        
        reverse_best_id, _ = reverse_cand_list[0]
        
        if reverse_best_id == edge_a_id:
            # Mutual best! Add the link (use sorted order to avoid duplicates)
            if edge_a_id < best_b_id:
                mutual_links.add((edge_a_id, best_b_id, node_id))
            else:
                mutual_links.add((best_b_id, edge_a_id, node_id))
    
    return mutual_links


def _get_other_node(edge: object, node_id: str) -> Optional[str]:
    """Get the other endpoint node_id of an edge."""
    u = str(getattr(edge, "u", ""))
    v = str(getattr(edge, "v", ""))
    if u == node_id:
        return v
    elif v == node_id:
        return u
    return None


def _extract_street_runs(
    mutual_links: Set[Tuple[str, str, str]],
    edge_map: Dict[str, object],
) -> List[List[str]]:
    """
    Extract street-runs as chains of edges from mutual continuation links.
    
    Builds a continuation graph where edges are nodes, and mutual links are edges.
    Then extracts connected components as chains.
    
    Returns:
        List of edge_id sequences, each representing one street-run
    """
    if not mutual_links:
        # Each edge is its own run
        return [[edge_id] for edge_id in edge_map.keys()]
    
    # Build adjacency list for continuation graph
    # Key: edge_id, Value: list of (connected_edge_id, shared_node_id)
    continuation_adj: Dict[str, List[Tuple[str, str]]] = {}
    
    for edge_a_id, edge_b_id, node_id in mutual_links:
        if edge_a_id not in continuation_adj:
            continuation_adj[edge_a_id] = []
        continuation_adj[edge_a_id].append((edge_b_id, node_id))
        
        if edge_b_id not in continuation_adj:
            continuation_adj[edge_b_id] = []
        continuation_adj[edge_b_id].append((edge_a_id, node_id))
    
    # Extract connected components (chains) via DFS
    visited: Set[str] = set()
    runs: List[List[str]] = []
    
    def _dfs_chain(start_edge_id: str) -> List[str]:
        """Extract a chain starting from an edge, preferring linear traversal."""
        chain: List[str] = []
        stack: List[str] = [start_edge_id]
        
        while stack:
            edge_id = stack.pop()
            if edge_id in visited:
                continue
            visited.add(edge_id)
            chain.append(edge_id)
            
            # Add unvisited neighbors
            for neighbor_id, _ in continuation_adj.get(edge_id, []):
                if neighbor_id not in visited:
                    stack.append(neighbor_id)
        
        return chain
    
    # Process all edges
    all_edge_ids = set(edge_map.keys())
    
    # First, process edges that are part of continuation graph
    for edge_id in continuation_adj.keys():
        if edge_id not in visited:
            chain = _dfs_chain(edge_id)
            if chain:
                runs.append(chain)
    
    # Add isolated edges (not in any continuation) as single-edge runs
    for edge_id in all_edge_ids:
        if edge_id not in visited:
            runs.append([edge_id])
            visited.add(edge_id)
    
    return runs


def _compute_bearing(v: Vec2) -> float:
    """Compute bearing angle in degrees [0, 360) from a direction vector."""
    if v.length() < 1e-9:
        return 0.0
    angle = degrees(atan2(v.y, v.x))
    if angle < 0:
        angle += 360.0
    return angle


def _build_street_run(
    edge_ids: List[str],
    edge_map: Dict[str, object],
    node_map: Dict[str, object],
    tangents: Dict[Tuple[str, str], Vec2],
    run_id: str,
    config: StreetRunConfig,
) -> StreetRun:
    """
    Build a StreetRun object from a sequence of edge IDs.
    
    Computes aggregated properties: total length, node sequence, flags, etc.
    """
    total_length = 0.0
    all_flags: Set[str] = set()
    road_classes: Dict[str, float] = {}  # class -> total length for that class
    bearings: List[float] = []
    deflections: List[float] = []
    node_sequence: List[str] = []
    
    # Build ordered node sequence and accumulate properties
    prev_node: Optional[str] = None
    
    for i, edge_id in enumerate(edge_ids):
        edge = edge_map.get(edge_id)
        if edge is None:
            continue
        
        length = float(getattr(edge, "length_m", 0.0))
        total_length += length
        
        road_class = str(getattr(edge, "road_class", "minor_local"))
        road_classes[road_class] = road_classes.get(road_class, 0.0) + length
        
        flags = getattr(edge, "flags", frozenset())
        all_flags.update(flags)
        
        u_id = str(getattr(edge, "u", ""))
        v_id = str(getattr(edge, "v", ""))
        
        # Determine node order for this edge
        if prev_node is None:
            # First edge: start with u
            node_sequence.append(u_id)
            node_sequence.append(v_id)
            prev_node = v_id
        else:
            # Connect to previous node
            if u_id == prev_node:
                node_sequence.append(v_id)
                prev_node = v_id
            elif v_id == prev_node:
                node_sequence.append(u_id)
                prev_node = u_id
            else:
                # Disconnected chain segment - shouldn't happen normally
                node_sequence.append(u_id)
                node_sequence.append(v_id)
                prev_node = v_id
        
        # Compute bearing from tangent
        tangent = tangents.get((edge_id, u_id))
        if tangent is not None:
            bearings.append(_compute_bearing(tangent))
    
    # Compute deflections between consecutive edges
    for i in range(len(edge_ids) - 1):
        edge_a_id = edge_ids[i]
        edge_b_id = edge_ids[i + 1]
        
        edge_a = edge_map.get(edge_a_id)
        edge_b = edge_map.get(edge_b_id)
        if edge_a is None or edge_b is None:
            continue
        
        # Find shared node
        u_a = str(getattr(edge_a, "u", ""))
        v_a = str(getattr(edge_a, "v", ""))
        u_b = str(getattr(edge_b, "u", ""))
        v_b = str(getattr(edge_b, "v", ""))
        
        shared_node = None
        if u_a == u_b or u_a == v_b:
            shared_node = u_a
        elif v_a == u_b or v_a == v_b:
            shared_node = v_a
        
        if shared_node:
            tangent_a = tangents.get((edge_a_id, shared_node))
            tangent_b = tangents.get((edge_b_id, shared_node))
            if tangent_a and tangent_b:
                neg_tangent_b = Vec2(-tangent_b.x, -tangent_b.y)
                defl = _angle_between_vectors(tangent_a, neg_tangent_b)
                deflections.append(defl)
    
    # Dominant road class
    dominant_class = "minor_local"
    if road_classes:
        dominant_class = max(road_classes.keys(), key=lambda k: road_classes[k])
    
    # Mean bearing
    mean_bearing = 0.0
    if bearings:
        # Use circular mean for bearings
        sin_sum = sum(np.sin(np.radians(b)) for b in bearings)
        cos_sum = sum(np.cos(np.radians(b)) for b in bearings)
        mean_bearing = degrees(atan2(sin_sum, cos_sum))
        if mean_bearing < 0:
            mean_bearing += 360.0
    
    # Sum of deflections
    deflection_sum = sum(deflections)
    
    # Determine if spine candidate
    has_spine_flag = "local_spine" in all_flags
    is_spine_candidate = has_spine_flag or total_length >= config.min_spine_length_m
    
    # Add run-level flag if spine candidate
    final_flags = frozenset(all_flags)
    if is_spine_candidate and "local_spine" not in all_flags:
        final_flags = frozenset(all_flags | {"local_spine_run"})
    
    # Start and end nodes
    start_node = node_sequence[0] if node_sequence else ""
    end_node = node_sequence[-1] if node_sequence else ""
    
    return StreetRun(
        id=run_id,
        edge_ids=edge_ids,
        road_class=dominant_class,
        flags=final_flags,
        length_m=total_length,
        node_ids=node_sequence,
        start_node_id=start_node,
        end_node_id=end_node,
        mean_bearing_deg=mean_bearing,
        deflection_sum_deg=deflection_sum,
        is_spine_candidate=is_spine_candidate,
        edge_count=len(edge_ids),
    )


def aggregate_street_runs(
    edges: Sequence[object],
    nodes: Sequence[object],
    config: Optional[StreetRunConfig] = None,
) -> Tuple[List[StreetRun], Dict[str, float]]:
    """
    Aggregate road edges into semantically continuous street-runs.
    
    Uses a mutual best continuation (stroke building) algorithm:
    1. Compute edge endpoint tangents
    2. Score continuation candidates at each node
    3. Extract mutual best pairs
    4. Build street-run chains
    
    Args:
        edges: Sequence of BuiltRoadEdge objects
        nodes: Sequence of BuiltRoadNode objects
        config: Optional configuration parameters
    
    Returns:
        (street_runs, diagnostic_stats)
    """
    if config is None:
        config = StreetRunConfig()
    
    # Build lookup maps
    node_map: Dict[str, object] = {
        str(getattr(n, "id", "")): n for n in nodes if getattr(n, "id", None)
    }
    edge_map: Dict[str, object] = {
        str(getattr(e, "id", "")): e for e in edges if getattr(e, "id", None)
    }
    
    if not edge_map:
        return [], {"street_run_aggregation_edges": 0.0}
    
    # Step 1: Compute tangents
    tangents = _compute_edge_tangents(edges, node_map)
    
    # Step 2: Build node-to-edges mapping
    node_to_edges = _build_node_to_edges_map(edges)
    
    # Step 3: Build continuation candidates
    candidates, reject_stats = _build_continuation_candidates(
        edges, node_map, edge_map, tangents, node_to_edges, config
    )
    
    # Step 4: Extract mutual best continuations
    mutual_links = _mutual_best_continuation(candidates, edge_map)
    
    # Step 5: Extract street-run chains
    edge_chains = _extract_street_runs(mutual_links, edge_map)
    
    # Step 6: Build StreetRun objects
    street_runs: List[StreetRun] = []
    for i, edge_ids in enumerate(edge_chains):
        run = _build_street_run(
            edge_ids, edge_map, node_map, tangents,
            f"run-{i}",
            config,
        )
        street_runs.append(run)
    
    # Diagnostic stats
    diag_stats = {
        "street_run_aggregation_edges": float(len(edge_map)),
        "street_run_mutual_links": float(len(mutual_links)),
        "street_run_merge_reject_angle_count": float(reject_stats["reject_angle_count"]),
        "street_run_merge_reject_width_count": float(reject_stats["reject_width_count"]),
    }
    
    return street_runs, diag_stats


def street_run_metrics(street_runs: List[StreetRun]) -> Dict[str, float]:
    """
    Calculate general street-run metrics.
    
    Returns metrics for all street-runs regardless of type.
    """
    if not street_runs:
        return {
            "street_run_count": 0.0,
            "street_run_len_p50_m": 0.0,
            "street_run_len_p90_m": 0.0,
            "street_run_len_p99_m": 0.0,
            "street_run_avg_edges_per_run": 0.0,
            "street_run_max_deflection_deg_p90": 0.0,
        }
    
    lengths = [run.length_m for run in street_runs]
    edge_counts = [run.edge_count for run in street_runs]
    deflection_sums = [run.deflection_sum_deg for run in street_runs]
    
    return {
        "street_run_count": float(len(street_runs)),
        "street_run_len_p50_m": float(np.percentile(lengths, 50)),
        "street_run_len_p90_m": float(np.percentile(lengths, 90)),
        "street_run_len_p99_m": float(np.percentile(lengths, 99)),
        "street_run_avg_edges_per_run": float(np.mean(edge_counts)),
        "street_run_max_deflection_deg_p90": float(np.percentile(deflection_sums, 90)),
    }


def spine_street_run_metrics(street_runs: List[StreetRun]) -> Dict[str, float]:
    """
    Calculate metrics for spine-candidate street-runs only.
    
    Spine candidates are runs with local_spine flag or length >= min_spine_length_m.
    """
    spine_runs = [r for r in street_runs if r.is_spine_candidate]
    
    if not spine_runs:
        return {
            "spine_street_run_count": 0.0,
            "spine_street_run_len_p50_m": 0.0,
            "spine_street_run_len_p90_m": 0.0,
            "spine_street_run_len_p99_m": 0.0,
            "spine_street_run_target_band_rate_1km_2km": 0.0,
        }
    
    lengths = [run.length_m for run in spine_runs]
    target_band = [l for l in lengths if 1000.0 <= l <= 2000.0]
    
    return {
        "spine_street_run_count": float(len(spine_runs)),
        "spine_street_run_len_p50_m": float(np.percentile(lengths, 50)),
        "spine_street_run_len_p90_m": float(np.percentile(lengths, 90)),
        "spine_street_run_len_p99_m": float(np.percentile(lengths, 99)),
        "spine_street_run_target_band_rate_1km_2km": float(len(target_band) / len(spine_runs)) if spine_runs else 0.0,
    }


def road_class_street_run_metrics(street_runs: List[StreetRun]) -> Dict[str, float]:
    """
    Calculate metrics broken down by road_class.
    """
    metrics: Dict[str, float] = {}
    
    for road_class in ["arterial", "major_local", "minor_local"]:
        class_runs = [r for r in street_runs if r.road_class == road_class]
        
        if not class_runs:
            metrics[f"{road_class}_street_run_count"] = 0.0
            metrics[f"{road_class}_street_run_len_p50_m"] = 0.0
            continue
        
        lengths = [run.length_m for run in class_runs]
        metrics[f"{road_class}_street_run_count"] = float(len(class_runs))
        metrics[f"{road_class}_street_run_len_p50_m"] = float(np.percentile(lengths, 50))
    
    return metrics
