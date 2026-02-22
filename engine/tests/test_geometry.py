from engine.core.geometry import AABB, Segment, Vec2, point_segment_distance, segment_intersection


def test_vec2_normalize_zero_safe():
    v = Vec2(0.0, 0.0).normalized()
    assert v.x == 0.0 and v.y == 0.0


def test_segment_intersection_t_junction():
    a = Segment(Vec2(0, 0), Vec2(10, 0))
    b = Segment(Vec2(5, -5), Vec2(5, 0))
    hit = segment_intersection(a, b)
    assert hit.kind == "point"
    assert hit.point is not None
    assert abs(hit.point.x - 5) < 1e-6
    assert abs(hit.point.y - 0) < 1e-6


def test_segment_intersection_parallel_none():
    a = Segment(Vec2(0, 0), Vec2(10, 0))
    b = Segment(Vec2(0, 1), Vec2(10, 1))
    hit = segment_intersection(a, b)
    assert hit.kind == "none"


def test_segment_intersection_collinear_overlap():
    a = Segment(Vec2(0, 0), Vec2(10, 0))
    b = Segment(Vec2(4, 0), Vec2(12, 0))
    hit = segment_intersection(a, b)
    assert hit.kind == "overlap"


def test_aabb_intersection():
    box1 = AABB(0, 0, 1, 1)
    box2 = AABB(0.5, 0.5, 2, 2)
    assert box1.intersects(box2)


def test_point_segment_distance_endpoint_clamp():
    seg = Segment(Vec2(0, 0), Vec2(10, 0))
    d = point_segment_distance(Vec2(12, 3), seg)
    assert abs(d - (13**0.5)) < 1e-6
