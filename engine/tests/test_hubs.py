import math

import numpy as np

from engine.hubs import generate_hubs


def test_poisson_like_hubs_respect_min_distance():
    slope = np.zeros((64, 64), dtype=float)
    result = generate_hubs(
        seed=123,
        extent_m=1000.0,
        slope=slope,
        river_polylines=[],
        t1_count=1,
        t2_count=2,
        t3_count=10,
        min_distance_m=60.0,
    )
    hubs = result.hubs
    for i in range(len(hubs)):
        for j in range(i + 1, len(hubs)):
            d = hubs[i].pos.distance_to(hubs[j].pos)
            assert d >= 59.9, (i, j, d)
