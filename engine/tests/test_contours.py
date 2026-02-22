import numpy as np

from engine.terrain.contours import extract_contour_lines
from engine.terrain.generator import generate_heightmap


def test_extract_contours_returns_lines_within_extent():
    extent = 1000.0
    height = generate_heightmap(resolution=96, octaves=4, seed=5, relief_strength=1.2)
    contours = extract_contour_lines(height, extent_m=extent, max_resolution=64, contour_count=8)

    assert contours
    for contour in contours[:200]:
        assert 0.0 <= contour.elevation_norm <= 1.0
        assert len(contour.points) >= 2
        for p in contour.points:
            assert 0.0 <= p.x <= extent
            assert 0.0 <= p.y <= extent

