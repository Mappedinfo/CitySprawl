from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Dict, Iterable, List, Tuple

from .geometry import AABB, Vec2


class SpatialHashIndex:
    """Minimal spatial hash for AABB culling in MVP-scale geometry checks."""

    def __init__(self, cell_size: float) -> None:
        self.cell_size = max(cell_size, 1e-6)
        self._cells: DefaultDict[Tuple[int, int], List[str]] = defaultdict(list)
        self._boxes: Dict[str, AABB] = {}

    def _keys_for_box(self, box: AABB) -> Iterable[Tuple[int, int]]:
        x0 = int(box.min_x // self.cell_size)
        x1 = int(box.max_x // self.cell_size)
        y0 = int(box.min_y // self.cell_size)
        y1 = int(box.max_y // self.cell_size)
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                yield (ix, iy)

    def insert(self, key: str, box: AABB) -> None:
        self._boxes[key] = box
        for cell in self._keys_for_box(box):
            self._cells[cell].append(key)

    def query(self, box: AABB) -> List[str]:
        hits = set()
        for cell in self._keys_for_box(box):
            for key in self._cells.get(cell, []):
                other = self._boxes[key]
                if other.intersects(box):
                    hits.add(key)
        return list(hits)

    def clear(self) -> None:
        self._cells.clear()
        self._boxes.clear()
