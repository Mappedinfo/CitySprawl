import numpy as np

from engine.terrain.hydrology import compute_flow_accumulation, compute_flow_direction


def test_d8_flow_direction_on_simple_slope_goes_downhill():
    height = np.array(
        [
            [5.0, 4.0, 3.0],
            [6.0, 5.0, 4.0],
            [7.0, 6.0, 5.0],
        ]
    )
    downstream = compute_flow_direction(height)
    # center cell should not be a sink and should move toward smaller elevation
    center = 1 * 3 + 1
    target = int(downstream[center])
    assert target != center
    ty, tx = divmod(target, 3)
    assert height[ty, tx] < height[1, 1]


def test_flow_accumulation_monotonic_with_downstream_receiving_more():
    height = np.array(
        [
            [3.0, 2.0],
            [4.0, 1.0],
        ]
    )
    downstream = compute_flow_direction(height)
    acc = compute_flow_accumulation(height, downstream)
    assert np.all(acc >= 1.0)
    assert acc[1, 1] >= acc[0, 1]
