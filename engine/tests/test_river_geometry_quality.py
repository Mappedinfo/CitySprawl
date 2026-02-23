from shapely.geometry import Polygon

from engine.terrain.river_area import build_river_area_polygons


def test_river_geometry_smoothing_and_variable_width_are_valid():
    rivers = [
        {
            "id": "main",
            "points": [{"x": 20.0, "y": 50.0}, {"x": 120.0, "y": 70.0}, {"x": 220.0, "y": 140.0}, {"x": 320.0, "y": 180.0}],
            "flow": 120.0,
            "length_m": 360.0,
        },
        {
            "id": "branch",
            "points": [{"x": 40.0, "y": 250.0}, {"x": 130.0, "y": 190.0}, {"x": 205.0, "y": 155.0}],
            "flow": 12.0,
            "length_m": 200.0,
        },
    ]
    selected, areas, meta = build_river_area_polygons(
        rivers,
        max_branches=2,
        clip_extent_m=400.0,
        centerline_smooth_iters=2,
        width_taper_strength=0.35,
        bank_irregularity=0.08,
        return_meta=True,
    )
    assert selected
    assert areas
    assert meta["pre_clip_area_m2"] >= meta["post_clip_area_m2"] >= 0.0

    main_widths = []
    branch_widths = []
    for area in areas:
        poly = Polygon([(p.x, p.y) for p in area.points])
        assert poly.is_valid
        assert poly.area > 0.0
        assert area.width_mean_m > 0.0
        if area.is_main_stem:
            main_widths.append(area.width_mean_m)
        else:
            branch_widths.append(area.width_mean_m)
    assert main_widths
    if branch_widths:
        assert max(main_widths) > min(branch_widths)

