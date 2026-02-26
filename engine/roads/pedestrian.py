from __future__ import annotations

PEDESTRIAN_WIDTH_M = 3.0
LOCAL_WIDTH_M = 8.0
COLLECTOR_WIDTH_M = 12.0
ARTERIAL_WIDTH_M = 18.0

ROAD_RENDER_ORDER = {
    'arterial': 0,
    'major_local': 1,
    'minor_local': 2,
    'pedestrian': 3,
}

ROAD_WIDTH_PRESET = {
    'arterial': ARTERIAL_WIDTH_M,
    'major_local': COLLECTOR_WIDTH_M,
    'minor_local': LOCAL_WIDTH_M,
    'pedestrian': PEDESTRIAN_WIDTH_M,
}
