from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable, Optional, Tuple

EPSILON = 1e-6


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> "Vec2":
        return Vec2(self.x * scalar, self.y * scalar)

    __rmul__ = __mul__

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    def length(self) -> float:
        return hypot(self.x, self.y)

    def normalized(self) -> "Vec2":
        length = self.length()
        if length <= EPSILON:
            return Vec2(0.0, 0.0)
        return Vec2(self.x / length, self.y / length)

    def distance_to(self, other: "Vec2") -> float:
        return (self - other).length()

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class AABB:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @classmethod
    def from_points(cls, points: Iterable[Vec2]) -> "AABB":
        pts = list(points)
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return cls(min(xs), min(ys), max(xs), max(ys))

    @classmethod
    def from_segment(cls, seg: "Segment") -> "AABB":
        return cls.from_points([seg.p0, seg.p1])

    def intersects(self, other: "AABB", eps: float = EPSILON) -> bool:
        if self.max_x < other.min_x - eps or other.max_x < self.min_x - eps:
            return False
        if self.max_y < other.min_y - eps or other.max_y < self.min_y - eps:
            return False
        return True


@dataclass(frozen=True)
class Segment:
    p0: Vec2
    p1: Vec2

    def vector(self) -> Vec2:
        return self.p1 - self.p0

    def length(self) -> float:
        return self.p0.distance_to(self.p1)

    def point_at(self, t: float) -> Vec2:
        return self.p0 + self.vector() * t

    def bbox(self) -> AABB:
        return AABB.from_segment(self)


@dataclass(frozen=True)
class SegmentIntersection:
    kind: str  # none, point, overlap
    point: Optional[Vec2] = None


def _orientation(a: Vec2, b: Vec2, c: Vec2) -> float:
    return (b - a).cross(c - a)


def _on_segment(a: Vec2, b: Vec2, p: Vec2, eps: float = EPSILON) -> bool:
    if abs(_orientation(a, b, p)) > eps:
        return False
    return (
        min(a.x, b.x) - eps <= p.x <= max(a.x, b.x) + eps
        and min(a.y, b.y) - eps <= p.y <= max(a.y, b.y) + eps
    )


def segment_intersection(s1: Segment, s2: Segment, eps: float = EPSILON) -> SegmentIntersection:
    if not s1.bbox().intersects(s2.bbox(), eps=eps):
        return SegmentIntersection("none")

    p = s1.p0
    r = s1.vector()
    q = s2.p0
    s = s2.vector()
    rxs = r.cross(s)
    q_p = q - p
    qpxr = q_p.cross(r)

    if abs(rxs) <= eps and abs(qpxr) <= eps:
        # Collinear. Detect any overlapping endpoint.
        for candidate in (s1.p0, s1.p1, s2.p0, s2.p1):
            if _on_segment(s1.p0, s1.p1, candidate, eps) and _on_segment(s2.p0, s2.p1, candidate, eps):
                return SegmentIntersection("overlap", candidate)
        return SegmentIntersection("none")

    if abs(rxs) <= eps and abs(qpxr) > eps:
        return SegmentIntersection("none")

    t = q_p.cross(s) / rxs
    u = q_p.cross(r) / rxs
    if -eps <= t <= 1.0 + eps and -eps <= u <= 1.0 + eps:
        point = p + r * t
        return SegmentIntersection("point", point)
    return SegmentIntersection("none")


def project_point_to_segment(point: Vec2, seg: Segment) -> Vec2:
    v = seg.vector()
    vv = v.dot(v)
    if vv <= EPSILON:
        return seg.p0
    t = (point - seg.p0).dot(v) / vv
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return seg.point_at(t)


def point_segment_distance(point: Vec2, seg: Segment) -> float:
    return point.distance_to(project_point_to_segment(point, seg))


def snap_point(point: Vec2, anchors: Iterable[Vec2], tolerance: float) -> Vec2:
    best = point
    best_dist = tolerance
    for anchor in anchors:
        dist = point.distance_to(anchor)
        if dist <= best_dist:
            best = anchor
            best_dist = dist
    return best
