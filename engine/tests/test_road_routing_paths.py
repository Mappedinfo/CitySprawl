from engine.generator import generate_city
from engine.models import GenerateConfig


def test_routed_roads_emit_polyline_paths_and_not_shorter_than_chord():
    artifact = generate_city(
        GenerateConfig(
            seed=31,
            grid_resolution=96,
            roads={"k_neighbors": 4, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 250.0},
        )
    )
    node_map = {n.id: n for n in artifact.roads.nodes}
    checked = 0
    for edge in artifact.roads.edges:
        if edge.road_class not in ("arterial", "minor_local"):
            continue
        if not edge.path_points or len(edge.path_points) < 2:
            continue
        u = node_map[edge.u]
        v = node_map[edge.v]
        chord = ((u.x - v.x) ** 2 + (u.y - v.y) ** 2) ** 0.5
        poly_len = 0.0
        for i in range(len(edge.path_points) - 1):
            a = edge.path_points[i]
            b = edge.path_points[i + 1]
            poly_len += ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
        assert poly_len + 1e-6 >= chord
        checked += 1
    assert checked > 0


def test_default_extent_is_10km():
    cfg = GenerateConfig()
    assert cfg.extent_m == 10000.0

