from shapely.geometry import Polygon

from engine.terrain.river_area import build_river_area_polygons, select_primary_rivers


def test_primary_river_selection_and_area_polygon_validity():
    rivers = [
        {"id": "r-main", "points": [{"x": 20.0, "y": 40.0}, {"x": 120.0, "y": 60.0}, {"x": 220.0, "y": 120.0}], "flow": 80.0, "length_m": 240.0},
        {"id": "r-a", "points": [{"x": 40.0, "y": 180.0}, {"x": 100.0, "y": 140.0}, {"x": 170.0, "y": 115.0}], "flow": 10.0, "length_m": 120.0},
        {"id": "r-b", "points": [{"x": 230.0, "y": 200.0}, {"x": 210.0, "y": 160.0}, {"x": 180.0, "y": 130.0}], "flow": 8.0, "length_m": 110.0},
        {"id": "r-small", "points": [{"x": 10.0, "y": 200.0}, {"x": 20.0, "y": 210.0}], "flow": 1.0, "length_m": 20.0},
    ]
    selected = select_primary_rivers(rivers, max_branches=2)
    assert selected
    assert selected[0]["is_main_stem"] is True
    assert len(selected) <= 3

    selected2, areas = build_river_area_polygons(rivers, max_branches=2)
    assert selected2
    assert areas
    for area in areas:
        poly = Polygon([(p.x, p.y) for p in area.points])
        assert poly.is_valid
        assert poly.area > 0.0
        assert area.width_mean_m > 0.0
