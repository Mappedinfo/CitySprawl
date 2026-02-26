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


def _collect_target_edges_for_scoring(
    edges: Sequence[object],
    *,
    target_classes: Optional[Set[str]] = None,
) -> list[object]:
    target_set = set(str(c).lower() for c in target_classes) if target_classes else {"arterial", "major_local"}
    out: list[object] = []
    for e in edges:
        rc = str(getattr(e, "road_class", "")).lower()
        if rc in target_set:
            out.append(e)
    return out


def _build_syntax_background_graph(nodes: Sequence[object], edges: Sequence[object]):
    """Build routing background graph for syntax using all motorized classes."""
    try:
        import networkx as nx  # type: ignore
    except Exception:
        return None

    g = nx.Graph()
    for n in nodes:
        g.add_node(str(getattr(n, "id")))

    bg_classes = {"arterial", "major_local", "minor_local"}
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
    return g


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
    syntax_edges = _collect_target_edges_for_scoring(edges, target_classes=target_classes)
    if not syntax_edges:
        notes.append("syntax:no_candidate_edges")
        return {}, notes

    g = _build_syntax_background_graph(nodes, edges)
    if g is None:
        notes.append("syntax:degraded_no_networkx")
        return {}, notes

    try:
        import networkx as nx  # type: ignore
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


def apply_width_guidance_postprocess(
    nodes: Sequence[object],
    edges: list[object],
    *,
    syntax_enable: bool,
    choice_radius_hops: int,
    target_classes: Optional[Set[str]] = None,
) -> tuple[list[object], list[str], dict[str, float]]:
    """Apply syntax-derived width guidance without changing topology."""
    notes: list[str] = []
    numeric: dict[str, float] = {
        "syntax_enabled": 0.0,
        "syntax_pruned_count": 0.0,
        "syntax_scored_edge_count": 0.0,
    }
    if not syntax_enable:
        notes.append("syntax:disabled")
        return list(edges), notes, numeric

    if target_classes is not None:
        target_set = set(str(c).lower() for c in target_classes)
        notes.append(f"syntax_target_classes:{','.join(sorted(target_set))}")
    else:
        target_set = {"arterial", "major_local"}

    scores, score_notes = compute_space_syntax_edge_scores(
        nodes, edges, choice_radius_hops=choice_radius_hops, target_classes=target_set,
    )
    notes.extend(score_notes)
    if not scores:
        return list(edges), notes, numeric

    numeric["syntax_enabled"] = 1.0
    numeric["syntax_scored_edge_count"] = float(len(scores))

    raw_scores = np.asarray(list(scores.values()), dtype=np.float64)
    high = float(np.quantile(raw_scores, 0.85))
    low = float(np.quantile(raw_scores, 0.15))
    out = list(edges)
    for i, e in enumerate(out):
        rc = str(getattr(e, "road_class", "")).lower()
        if rc != "major_local":
            continue
        if rc not in target_set:
            continue
        score = float(scores.get(str(getattr(e, "id")), 0.0))
        width = float(getattr(e, "width_m", 11.0))
        if score >= high:
            width = 11.0 + min(2.0, 2.0 * ((score - high) / max(1e-6, 1.0 - high)))
        elif score <= low:
            # Low-choice collectors remain in topology; only de-emphasize width slightly.
            width = max(9.0, min(width, 10.0))
        out[i] = _rebuild_edge_like(e, width_m=float(width))

    notes.append("syntax:width_guidance_only")
    return out, notes, numeric


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
    """Deprecated compatibility wrapper; topology pruning is no longer applied."""
    _ = prune_quantile
    notes = ["syntax:deprecated_apply_syntax_postprocess"]
    if prune_low_choice_collectors:
        notes.append("syntax:prune_deprecated_ignored")
    out, inner_notes, numeric = apply_width_guidance_postprocess(
        nodes=nodes,
        edges=edges,
        syntax_enable=syntax_enable,
        choice_radius_hops=choice_radius_hops,
        target_classes=target_classes,
    )
    return out, notes + inner_notes, numeric
