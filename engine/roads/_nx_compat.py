from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple


class NetworkXNoPath(Exception):
    pass


class Graph:
    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, object]] = {}
        self._adj: Dict[str, Dict[str, Dict[str, object]]] = {}

    def add_node(self, node: str, **attrs: object) -> None:
        self._nodes.setdefault(node, {}).update(attrs)
        self._adj.setdefault(node, {})

    def add_edge(self, u: str, v: str, **attrs: object) -> None:
        self.add_node(u)
        self.add_node(v)
        data = dict(attrs)
        self._adj[u][v] = data
        self._adj[v][u] = data

    def has_edge(self, u: str, v: str) -> bool:
        return v in self._adj.get(u, {})

    def number_of_nodes(self) -> int:
        return len(self._nodes)

    def number_of_edges(self) -> int:
        total = sum(len(neigh) for neigh in self._adj.values())
        return total // 2

    @property
    def nodes(self):
        return self._nodes.keys()

    def edges(self, data: bool = False):
        seen: Set[Tuple[str, str]] = set()
        for u, nbrs in self._adj.items():
            for v, attrs in nbrs.items():
                key = tuple(sorted((u, v)))
                if key in seen:
                    continue
                seen.add(key)
                if data:
                    yield (u, v, dict(attrs))
                else:
                    yield (u, v)

    def degree(self, node: str) -> int:
        return len(self._adj.get(node, {}))

    def neighbors(self, node: str) -> Iterator[Tuple[str, Dict[str, object]]]:
        for nbr, data in self._adj.get(node, {}).items():
            yield nbr, data


# Functions mirroring the subset used in roads/network.py

def connected_components(graph: Graph) -> Iterable[Set[str]]:
    seen: Set[str] = set()
    for node in graph.nodes:
        if node in seen:
            continue
        comp: Set[str] = set()
        stack = [node]
        seen.add(node)
        while stack:
            cur = stack.pop()
            comp.add(cur)
            for nbr, _ in graph.neighbors(cur):
                if nbr not in seen:
                    seen.add(nbr)
                    stack.append(nbr)
        yield comp


def is_connected(graph: Graph) -> bool:
    if graph.number_of_nodes() <= 1:
        return True
    comps = list(connected_components(graph))
    return len(comps) == 1


def minimum_spanning_tree(graph: Graph, weight: str = "weight") -> Graph:
    tree = Graph()
    for node in graph.nodes:
        tree.add_node(node)

    parent: Dict[str, str] = {}
    rank: Dict[str, int] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> bool:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1
        return True

    for node in graph.nodes:
        parent[node] = node
        rank[node] = 0

    edges = sorted(graph.edges(data=True), key=lambda e: float(e[2].get(weight, 0.0)))
    for u, v, attrs in edges:
        if union(u, v):
            tree.add_edge(u, v, **attrs)
    return tree


def shortest_path_length(graph: Graph, source: str, target: str, weight: str = "weight") -> float:
    if source == target:
        return 0.0
    pq: List[Tuple[float, str]] = [(0.0, source)]
    dist: Dict[str, float] = {source: 0.0}
    visited: Set[str] = set()
    while pq:
        d, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)
        if node == target:
            return d
        for nbr, attrs in graph.neighbors(node):
            nd = d + float(attrs.get(weight, 0.0))
            if nd < dist.get(nbr, float("inf")):
                dist[nbr] = nd
                heapq.heappush(pq, (nd, nbr))
    raise NetworkXNoPath(f"No path between {source} and {target}")
