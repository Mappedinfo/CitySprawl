from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Sequence

import numpy as np


@dataclass
class ToponymyFeature:
    tier: int
    near_river: bool
    bridge_count: int
    degree: int
    centrality: float


class ToponymyProvider(ABC):
    @abstractmethod
    def generate_names(self, features_batch: Sequence[ToponymyFeature], seed: int) -> List[str]:
        raise NotImplementedError


class MockToponymyProvider(ToponymyProvider):
    _prefix = [
        "Quayside",
        "Foundry",
        "Cedar",
        "Granite",
        "Harbor",
        "Northgate",
        "Rivermark",
        "Lantern",
        "Iron",
        "Market",
        "Willow",
        "Summit",
    ]
    _suffix = [
        "Square",
        "Terminal",
        "Cross",
        "Heights",
        "Ward",
        "Junction",
        "Commons",
        "Reach",
        "Point",
        "Exchange",
    ]
    _river_tokens = ["Quay", "Bridge", "Dock", "Ferry", "Basin"]

    def generate_names(self, features_batch: Sequence[ToponymyFeature], seed: int) -> List[str]:
        rng = np.random.default_rng(seed + 9001)
        names: List[str] = []
        used = set()
        for i, feat in enumerate(features_batch):
            prefix = self._prefix[int(rng.integers(0, len(self._prefix)))]
            suffix = self._suffix[int(rng.integers(0, len(self._suffix)))]
            if feat.near_river or feat.bridge_count > 0:
                river_tok = self._river_tokens[int(rng.integers(0, len(self._river_tokens)))]
                candidate = f"{prefix} {river_tok}"
            elif feat.tier == 1:
                candidate = f"{prefix} Central"
            else:
                candidate = f"{prefix} {suffix}"
            if candidate in used:
                candidate = f"{candidate} {i + 1}"
            used.add(candidate)
            names.append(candidate)
        return names


def get_toponymy_provider(name: str) -> ToponymyProvider:
    normalized = (name or "mock").strip().lower()
    if normalized == "mock":
        return MockToponymyProvider()
    raise ValueError(f"Unsupported naming provider: {name}")
