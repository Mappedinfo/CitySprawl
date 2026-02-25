from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np


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


def _rebuild_edge_like(edge: object, *, width_m: Optional[float] = None) -> object:
    cls = edge.__class__
    flags = _edge_flags(edge)
    kwargs = dict(
        id=getattr(edge, "id"),
        u=getattr(edge, "u"),
        v=getattr(edge, "v"),
        road_class=getattr(edge, "road_class"),
        weight=float(getattr(edge, "weight")),
        length_m=float(getattr(edge, "length_m")),
        river_crossings=int(getattr(edge, "river_crossings")),
        width_m=float(width_m if width_m is not None else getattr(edge, "width_m")),
        render_order=int(getattr(edge, "render_order")),
        path_points=getattr(edge, "path_points"),
    )
    try:
        return cls(flags=flags, **kwargs)
    except TypeError:
        return cls(**kwargs)


def _edge_pairs(edges: Sequence[object]) -> Dict[Tuple[str, str], List[object]]:
    out: Dict[Tuple[str, str], List[object]] = defaultdict(list)
    for e in edges:
        u = str(getattr(e, "u"))
        v = str(getattr(e, "v"))
        out[tuple(sorted((u, v)))].append(e)
    return out


def _largest_component_ratio(edges: Sequence[object], nodes: Sequence[object]) -> float:
    if not nodes:
        return 1.0
    adj: Dict[str, set[str]] = defaultdict(set)
    for e in edges:
        u = str(getattr(e, "u"))
        v = str(getattr(e, "v"))
        adj[u].add(v)
        adj[v].add(u)
    node_ids = [str(getattr(n, "id")) for n in nodes]
    seen: set[str] = set()
    largest = 0
    for nid in node_ids:
        if nid in seen:
            continue
        comp = 0
        stack = [nid]
        seen.add(nid)
        while stack:
            cur = stack.pop()
            comp += 1
            for nb in adj.get(cur, set()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        largest = max(largest, comp)
    return float(largest / max(len(node_ids), 1))


def compute_space_syntax_edge_scores(
    nodes: Sequence[object],
    edges: Sequence[object],
    *,
    choice_radius_hops: int = 10,
    target_classes: Optional[Set[str]] = None,
) -> tuple[Dict[str, float], list[str]]:
    _ = choice_radius_hops  # first-pass uses global choice for simplicity
    notes: list[str] = []
    # Determine which classes to score
    score_set = set(str(c).lower() for c in target_classes) if target_classes else {"arterial", "collector"}
    syntax_edges = [e for e in edges if str(getattr(e, "road_class", "")) in score_set]
    if not syntax_edges:
        notes.append("syntax:no_candidate_edges")
        return {}, notes

    try:
        import networkx as nx  # type: ignore
    except Exception:
        notes.append("syntax:degraded_no_networkx")
        return {}, notes

    # Build graph from ALL motorized edges (arterial + collector + local) as routing background
    g = nx.Graph()
    for n in nodes:
        g.add_node(str(getattr(n, "id")))
    bg_classes = {"arterial", "collector", "local"}
    for e in edges:
        rc = str(getattr(e, "road_class", "")).lower()
        if rc not in bg_classes:
            continue
        u = str(getattr(e, "u"))
        v = str(getattr(e, "v"))
        w = float(getattr(e, "length_m", 1.0) or 1.0)
        if g.has_edge(u, v):
            if w < float(g[u][v].get("weight", w)):
                g[u][v]["weight"] = w
            continue
        g.add_edge(u, v, weight=w)

    try:
        pair_scores = nx.edge_betweenness_centrality(g, normalized=True, weight="weight")
    except Exception:
        notes.append("syntax:degraded_betweenness_failed")
        return {}, notes

    if not pair_scores:
        notes.append("syntax:empty_scores")
        return {}, notes

    raw_vals = np.asarray([float(v) for v in pair_scores.values()], dtype=np.float64)
    vmin = float(np.min(raw_vals))
    vmax = float(np.max(raw_vals))
    span = max(vmax - vmin, 1e-9)

    pair_to_edges = _edge_pairs(syntax_edges)
    out: Dict[str, float] = {}
    for pair, raw in pair_scores.items():
        key = tuple(sorted((str(pair[0]), str(pair[1]))))
        score = float((float(raw) - vmin) / span)
        for e in pair_to_edges.get(key, []):
            out[str(getattr(e, "id"))] = score
    notes.append(f"syntax:computed_edges:{len(out)}")
    return out, notes


def apply_syntax_postprocess(
    nodes: Sequence[object],
    edges: list[object],
    *,
    syntax_enable: bool,
    choice_radius_hops: int,
    prune_low_choice_collectors: bool,
    prune_quantile: float,
    target_classes: Optional[Set[str]] = None,
) -> tuple[list[object], list[str], dict[str, float]]:
    """Apply space syntax postprocessing to road edges.
    
    Args:
        nodes: Sequence of road nodes.
        edges: List of road edges.
        syntax_enable: Whether to enable syntax processing.
        choice_radius_hops: Radius in hops for choice calculation.
        prune_low_choice_collectors: Whether to prune low-choice collectors.
        prune_quantile: Quantile threshold for pruning.
        target_classes: Optional set of road classes to score and prune/adjust.
            The betweenness graph always includes all motorized edges (arterial +
            collector + local) as routing background, but only edges in target_classes
            receive scores and are eligible for pruning/width changes.
    
    Returns:
        Tuple of (edges, notes, numeric).
    """
    notes: list[str] = []
    numeric: dict[str, float] = {
        "syntax_enabled": 0.0,
        "syntax_pruned_count": 0.0,
        "syntax_scored_edge_count": 0.0,
    }
    if not syntax_enable:
        notes.append("syntax:disabled")
        return edges, notes, numeric

    # Determine which classes to process
    if target_classes is not None:
        target_set = set(str(c).lower() for c in target_classes)
        notes.append(f"syntax_target_classes:{','.join(sorted(target_set))}")
    else:
        target_set = {"arterial", "collector"}  # default behavior

    scores, score_notes = compute_space_syntax_edge_scores(
        nodes, edges, choice_radius_hops=choice_radius_hops, target_classes=target_set,
    )
    notes.extend(score_notes)
    if not scores:
        return edges, notes, numeric

    numeric["syntax_enabled"] = 1.0
    numeric["syntax_scored_edge_count"] = float(len(scores))

    # Width emphasis for high-choice collectors.
    if scores:
        high = np.quantile(np.asarray(list(scores.values()), dtype=np.float64), 0.85)
        for i, e in enumerate(edges):
            rc = str(getattr(e, "road_class", "")).lower()
            if rc != "collector":
                continue
            # Only process if collector is in target classes
            if target_classes is not None and rc not in target_set:
                continue
            score = float(scores.get(str(getattr(e, "id")), 0.0))
            if score <= float(high):
                continue
            width = 11.0 + min(2.0, 2.0 * ((score - float(high)) / max(1e-6, 1.0 - float(high))))
            edges[i] = _rebuild_edge_like(e, width_m=float(width))

    if not prune_low_choice_collectors:
        return edges, notes, numeric

    # Only prune collectors if they are in target classes
    if target_classes is not None and "collector" not in target_set:
        notes.append("syntax:collector_not_in_target_classes")
        return edges, notes, numeric

    collector_scores = [float(scores.get(str(getattr(e, "id")), 0.0)) for e in edges if str(getattr(e, "road_class", "")) == "collector"]
    if not collector_scores:
        notes.append("syntax:no_collectors_to_prune")
        return edges, notes, numeric
    thresh = float(np.quantile(np.asarray(collector_scores, dtype=np.float64), max(0.0, min(0.99, prune_quantile))))

    base_ratio = _largest_component_ratio(edges, nodes)
    if base_ratio <= 0.0:
        base_ratio = 1.0

    def degrees(cur_edges: Sequence[object]) -> Dict[str, int]:
        d: Dict[str, int] = Counter()
        for e in cur_edges:
            d[str(getattr(e, "u"))] += 1
            d[str(getattr(e, "v"))] += 1
        return d

    cur = list(edges)
    initial_collector_count = sum(1 for e in cur if str(getattr(e, "road_class", "")) == "collector")
    min_collectors_to_keep = max(1, int(np.ceil(initial_collector_count * 0.15))) if initial_collector_count > 0 else 0
    pruned = 0
    changed = True
    while changed:
        changed = False
        collector_count = sum(1 for e in cur if str(getattr(e, "road_class", "")) == "collector")
        if collector_count <= min_collectors_to_keep:
            break
        deg = degrees(cur)
        cands = []
        for idx, e in enumerate(cur):
            if str(getattr(e, "road_class", "")) != "collector":
                continue
            score = float(scores.get(str(getattr(e, "id")), 0.0))
            if score > thresh:
                continue
            if max(deg.get(str(getattr(e, "u")), 0), deg.get(str(getattr(e, "v")), 0)) > 2:
                continue
            cands.append((score, idx))
        for _, idx in cands:
            collector_count = sum(1 for e in cur if str(getattr(e, "road_class", "")) == "collector")
            if collector_count <= min_collectors_to_keep:
                break
            test_edges = [e for j, e in enumerate(cur) if j != idx]
            ratio = _largest_component_ratio(test_edges, nodes)
            if ratio + 0.02 < base_ratio:
                continue
            cur = test_edges
            pruned += 1
            changed = True
            break

    if pruned > 0:
        notes.append(f"syntax:pruned_collectors:{pruned}")
    numeric["syntax_pruned_count"] = float(pruned)
    return cur, notes, numeric
