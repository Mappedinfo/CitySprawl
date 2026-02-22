from __future__ import annotations

STAGE_SPECS = [
    {
        'stage_id': 'terrain',
        'title': 'Terrain Input',
        'title_zh': '地形输入',
        'subtitle': 'Complex terrain and hydrology baseline',
        'subtitle_zh': '复杂地形与水文基底',
        'timestamp_ms': 0,
        'visible_layers': ['terrain', 'rivers'],
    },
    {
        'stage_id': 'analysis',
        'title': 'Habitable Analysis',
        'title_zh': '宜居性分析',
        'subtitle': 'Identifying habitable areas and allocating resources',
        'subtitle_zh': '识别宜居区域并配置资源',
        'timestamp_ms': 3000,
        'visible_layers': ['terrain', 'rivers', 'analysis_heatmaps', 'resources'],
    },
    {
        'stage_id': 'infrastructure',
        'title': 'Infrastructure Planning',
        'title_zh': '基础设施规划',
        'subtitle': 'Road network generation and bridge placement',
        'subtitle_zh': '道路网络生成与桥梁布设',
        'timestamp_ms': 7000,
        'visible_layers': ['terrain', 'rivers', 'roads', 'hubs', 'labels'],
    },
    {
        'stage_id': 'traffic',
        'title': 'Traffic Simulation',
        'title_zh': '交通模拟',
        'subtitle': 'OD flow assignment and congestion preview',
        'subtitle_zh': 'OD流量分配与拥堵预览',
        'timestamp_ms': 11000,
        'visible_layers': ['terrain', 'rivers', 'roads', 'hubs', 'traffic_heat'],
    },
    {
        'stage_id': 'final_preview',
        'title': 'City Preview',
        'title_zh': '城市预览',
        'subtitle': 'Composite city preview with buildings and green zones',
        'subtitle_zh': '带建筑与绿地的城市合成预览',
        'timestamp_ms': 15000,
        'visible_layers': ['terrain', 'rivers', 'roads', 'hubs', 'labels', 'buildings', 'green_zones'],
    },
]
