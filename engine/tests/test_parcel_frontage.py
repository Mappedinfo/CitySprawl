from shapely.geometry import Polygon

from engine.blocks.parcelize import FrontageParcelConfig, generate_frontage_parcels


def test_frontage_parcelizer_splits_superblock_and_preserves_positive_area():
    # Simple superblock to exercise recursive frontage-oriented splitting.
    block = Polygon([(0.0, 0.0), (260.0, 0.0), (260.0, 180.0), (0.0, 180.0)])
    result = generate_frontage_parcels(
        [block],
        pedestrian_width_m=3.0,
        config=FrontageParcelConfig(
            residential_target_area_m2=1800.0,
            mixed_target_area_m2=2200.0,
            min_frontage_m=10.0,
            min_depth_m=12.0,
            seed=42,
        ),
    )
    assert result.parcel_polygons_by_block
    parcels = result.parcel_polygons_by_block[0]
    assert len(parcels) > 4
    for poly in parcels:
        assert poly.area > 0.0
        minx, miny, maxx, maxy = poly.bounds
        assert (maxx - minx) > 3.0
        assert (maxy - miny) > 3.0
    # Large block should usually get at least one pedestrian cut in the base pass.
    assert result.pedestrian_paths is not None

