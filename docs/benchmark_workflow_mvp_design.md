# GeoAI 城市沙盒（Urban Sandbox）
## 竞品工作流拆解与对标型 MVP 设计文档

版本：v0.1
状态：设计稿（用于指导下一阶段实现）
最后更新：2026-02-22

---

## 1. 文档目的

本设计文档用于完成以下目标：

- 将竞品展示视频中的“阶段化生成叙事”拆解为可实现的工程工作流。
- 在现有 CityGen MVP（地形、水文、Hub、路网、Web 可视化）基础上，补齐对标竞品所需的关键能力。
- 明确一套“计算流水线（Compute DAG）”与“演示时间轴（Presentation Timeline）”分离的系统设计，确保既能做研究验证，也能做产品演示。
- 为下一步实现 `generate_staged` 接口、阶段回放 UI、交通热力图和资源/人口分析层提供统一规范。

---

## 2. 背景与对标目标

### 2.1 竞品视频可见能力（基于画面和文字推断）

竞品视频以时间顺序展示如下阶段：

1. 复杂地形输入（山地、丘陵、河流等）
2. 地形分析与宜居性/资源/人口分布建议（叠加图层）
3. 基础设施规划（道路、桥梁、主干道逐步生成）
4. 交通模拟与优化（车流动画、拥堵热力、控制逻辑）
5. 高质量城市可视化合成（逼真鸟瞰图）

视频中字幕或提示语义接近：

- `Complex terrain input`
- `Identifying habitable areas`
- `Resource allocation`
- `Mapping infrastructure`
- `Road network generation`
- `Simulating traffic`
- `Traffic flow optimization`
- `High-quality visualization generated`

### 2.2 对标目标（我们这一阶段的目标）

我们当前目标不是在算法与视觉上完全追平竞品，而是实现“演示等价”的 MVP：

- 能按阶段输出与回放城市生成流程。
- 每个阶段都有清晰的中间图层与说明文本。
- 核心几何与规划逻辑保持确定性、可测试、可复现。
- AI 在 MVP 中主要承担“语义命名、说明与接口预留”，不侵入几何正确性核心。

---

## 3. 关键设计原则（新增）

### 3.1 计算流水线与演示时间轴分离

竞品视频展示顺序不等于计算内部实现顺序。为此系统必须同时维护两条逻辑：

- `Compute DAG`：真实计算依赖关系（地形 -> 水文 -> 适宜性 -> Hub -> 路网 -> 交通 -> 合成）
- `Presentation Timeline`：前端演示回放顺序（可包含淡入/叠加/字幕/过渡）

设计要求：

- 后端生成时输出阶段快照（Stage Artifacts）。
- 前端根据阶段快照和时间轴脚本回放，不直接耦合底层生成步骤。

### 3.2 确定性算法为骨架，AI 为语义增强

沿用项目核心哲学：

- 用确定性算法构建城市骨架（地形、水文、Hub、路网、交通流量分配）
- 用生成式 AI 增强语义表达（命名、说明、后续视觉渲染）

MVP 范围内：

- AI 不参与几何决策。
- AI 仅负责命名与可选的文字解释接口。

### 3.3 中间图层必须可视化且可导出

对标竞品视频的关键不是最终图像，而是“中间推理过程可见”。因此每个阶段都必须输出：

- 可渲染图层（heatmap / points / polylines / polygons）
- 阶段指标（如候选区面积、路网连通率、流量峰值）
- 字幕/说明文本（可模板化）

---

## 4. 对标工作流拆解（竞品 -> 我们的工程映射）

### 4.1 阶段 A：Complex Terrain Input（复杂地形输入）

竞品画面特征：

- 山地、丘陵、河流等复杂地形底图
- 提示“Starting with terrain data”

工程映射（我们）：

- 输入：`seed + GenerateConfig`
- 输出：高度图、坡度图、河道（简化水文）
- 已有实现：
  - `engine/terrain/generator.py`
  - `engine/terrain/hydrology.py`

MVP 展示图层：

- Terrain height preview（伪彩色）
- River polylines
- 可选 slope preview（半透明）

### 4.2 阶段 B：GeoAI Analysis（宜居性/资源/人口分析）

竞品画面特征：

- 宜居性叠加图层
- 水源与资源位置
- 人口分布/居住建议
- 类似“Where people will live”的字幕语义

工程映射（我们，新增层）：

- 输入：高度图、坡度图、河道
- 输出：
  - `suitability_heatmap`（宜居性）
  - `flood_risk_heatmap`（洪水风险）
  - `resource_sites`（资源点）
  - `population_potential_heatmap`（人口潜力）

算法原则（MVP）：

- 完全规则驱动，不依赖 LLM
- 由坡度、水源距离、中心性、洪泛风险组合评分生成
- 结果确定性、可重复

### 4.3 阶段 C：Settlement & Infrastructure（聚落与基础设施规划）

竞品画面特征：

- 关键点逐步连线
- 道路与桥梁出现
- 避开陡坡/洪水区

工程映射（我们）：

- Hub 生成从单纯坡度+水体评分升级为：
  - `suitability + resource + water + centerity`
- 路网生成继续使用：
  - 候选边代价（距离、坡度、河流穿越惩罚）
  - MST 骨架 + 少量环路增强 + 支路扩展
- 桥梁表现：河流穿越边以桥梁标记展示

已有实现（可扩展）：

- `engine/hubs/sampling.py`
- `engine/roads/network.py`

### 4.4 阶段 D：Traffic Simulation & Optimization（交通仿真与优化）

竞品画面特征：

- 车辆流动动画
- 热力图/拥堵预测
- 动态交通逻辑

工程映射（我们，MVP简化版）：

- 不直接上微观车辆 Agent 仿真
- 先实现 `OD-based edge flow assignment`：
  - 基于 Hub tier 构造 OD 需求矩阵
  - 在路网上执行最短路径分配
  - 统计每条边的流量并生成热度图
- 前端用动画脉冲/流动粒子伪装“交通动态”，满足演示目标

后续升级路径：

- A* + 动态边权重 + 迭代反馈
- 微观车辆 Agent 模拟与信号灯逻辑

### 4.5 阶段 E：High-quality Visualization（高质量城市可视化）

竞品画面特征：

- 高质量 3D 鸟瞰城市
- 建筑、绿化、交通系统完整展示

工程映射（我们，MVP展示版）：

- 先做 2D/2.5D 合成城市预览：
  - 建筑 footprint 占位（按道路密度和 Hub tier）
  - 绿地/水体填充
  - 交通热图淡出，结构图层增强
- 文案保持“High-quality city preview generated”
- 扩散模型渲染与 ControlNet 留作后续阶段

---

## 5. 强化后的系统架构（5 层 + 编排器）

在原始 4 层架构基础上，建议调整为：

1. `Geo-Spatial Foundation Layer`
- 空间索引、几何、地形、高度、坡度、水文

2. `GeoAI Suitability & Resource Layer`（新增）
- 宜居性、洪水风险、资源点、人口潜力图

3. `Procedural Infrastructure Layer`
- Hub 播撒、路网生成、桥梁标记、（后续）地块提取

4. `Dynamic Traffic Layer`
- 路网流量分配、热力图、（后续）动态重路由与微观仿真

5. `Presentation & Rendering Layer`
- 2D/2.5D 图层合成、字幕、回放状态机、导出

6. `Pipeline Orchestrator / Timeline Player`（新增编排器）
- 负责生成阶段快照、编排时间轴、输出阶段化结果

---

## 6. 现有系统能力与缺口分析（基于当前代码）

### 6.1 已具备能力（可复用）

- 地形与简化水文：`engine/terrain/*`
- 几何基础库：`engine/core/*`
- Hub 生成与评分：`engine/hubs/sampling.py`
- 路网骨架生成与基础质量指标：`engine/roads/network.py`
- 命名接口与 Mock Provider：`engine/naming/*`
- FastAPI API：`engine/api/app.py`
- Web 可视化（Canvas/SVG 图层渲染、参数面板、导出）：`web/src/*`

### 6.2 关键缺口（为实现对标演示 MVP 必须补齐）

- `Stage Artifacts`（阶段快照）数据结构与接口
- `generate_staged` 后端接口
- 宜居性/资源/人口潜力分析层
- 交通流量分配最小版（非微观仿真）
- 前端时间轴回放与字幕系统
- 最终 2.5D 合成预览图层

---

## 7. MVP 对标方案（演示等价版）

### 7.1 目标

在不引入复杂外部 AI 服务与高成本渲染的前提下，做出“竞品同款叙事结构”的视频级演示 MVP。

### 7.2 交付形式

- 后端输出 `final_artifact + stages[]`
- 前端提供 `Timeline Player`：
  - 自动播放阶段
  - 图层渐进显示
  - 字幕/说明叠加
  - 可暂停/跳转/导出单帧

### 7.3 MVP 阶段与展示脚本（建议默认）

阶段 A（0-3s）Terrain
- 展示地形与河流
- 字幕：`Complex terrain input`
- 字幕：`Starting with terrain data`

阶段 B（3-7s）Analysis
- 依次叠加宜居性、资源点、人口潜力图
- 字幕：`Identifying habitable areas`
- 字幕：`Resource allocation`
- 字幕：`Where people will live`

阶段 C（7-11s）Infrastructure
- 先显示 Hub，再逐步绘制主干路与桥梁，最后支路
- 字幕：`Mapping infrastructure`
- 字幕：`Road network generation`

阶段 D（11-15s）Traffic
- 道路流量热度动画
- 拥堵边高亮/脉冲效果
- 字幕：`Simulating traffic`
- 字幕：`Traffic flow optimization`

阶段 E（15-20s）Final Composite
- 淡入建筑占位、绿化与清爽图层
- 字幕：`High-quality city preview generated`

---

## 8. 数据模型设计（新增）

### 8.1 `StageArtifact`（新增）

用于后端输出阶段快照，前端时间轴播放使用。

建议字段：

- `stage_id: str`（如 `terrain`, `analysis`, `infrastructure`, `traffic`, `final_preview`）
- `title: str`
- `subtitle: str`
- `timestamp_ms: int`（建议时间轴位置）
- `visible_layers: list[str]`（本阶段默认开启图层）
- `annotations: list[StageAnnotation]`（字幕、标注）
- `metrics: dict[str, float|int|str|bool]`
- `layers: StageLayersSnapshot`

### 8.2 `StageLayersSnapshot`（新增）

建议字段（按需可空）：

- `terrain_heights_preview`
- `slope_preview`
- `rivers`
- `suitability_preview`
- `flood_risk_preview`
- `population_potential_preview`
- `resource_sites`
- `hubs`
- `candidate_edges`
- `roads`
- `traffic_edge_flows`
- `building_footprints_preview`
- `green_zones_preview`

### 8.3 `ResourceSite`（新增）

建议字段：

- `id: str`
- `x: float`
- `y: float`
- `kind: str`（`water`, `agri`, `ore`, `forest`, `energy` 等）
- `quality: float`
- `influence_radius_m: float`

### 8.4 `TrafficEdgeFlow`（新增）

建议字段：

- `edge_id: str`
- `flow: float`
- `capacity: float`（MVP 可估算）
- `congestion_ratio: float`
- `class: str`（arterial/local）

---

## 9. 后端接口设计（新增/扩展）

### 9.1 新增接口：`POST /api/v1/generate_staged`

用途：生成最终城市结果，并返回完整阶段快照数组。

请求：
- 复用 `GenerateConfig`

响应：
- `final_artifact: CityArtifact`
- `stages: list[StageArtifact]`
- `timeline: PresentationTimeline`（可选）

### 9.2 保留现有接口

- `GET /api/v1/health`
- `GET /api/v1/presets`
- `GET /api/v1/schema`
- `POST /api/v1/generate`（最终成品直出）

### 9.3 接口设计原则

- `generate` 用于快速调参与测试
- `generate_staged` 用于演示与视频导出
- 两者内部共享同一 `Compute DAG`，避免逻辑分叉

---

## 10. 算法设计增强（面向对标视频）

### 10.1 宜居性热力图（新增）

输入：
- 坡度图
- 河道与距离场
- 高度图（用于高海拔惩罚）
- 中心性偏好（避免边缘全空）

输出：
- `suitability_heatmap ∈ [0,1]`

推荐评分项（MVP）：
- `slope_score`: 坡度越低越好
- `river_access_score`: 离河适中最优（太远不利，太近洪泛风险高）
- `flood_penalty`: 低地且近河加惩罚
- `center_bias`: 中心区轻微奖励

### 10.2 资源点生成（新增）

目标：为竞品视频中的“Resource allocation”提供明确视觉对象。

MVP 方案：规则生成
- `water`: 由河流点或河流汇流节点导出
- `agri`: 低坡、近水、非洪泛区域
- `ore`: 高坡/山地附近点
- `forest`: 中坡、远离核心城区

### 10.3 人口潜力图（新增）

目标：提供“Where people will live”图层。

MVP 方案：
- `population_potential = f(suitability, water_access, resource_access, road_access_proxy)`
- 在路网生成前可先用 `hub attractiveness proxy`
- 在路网生成后可更新为版本 2（受可达性影响）

### 10.4 交通仿真最小版（新增）

MVP 不做微观 Agent，做“边流量分配 + 动画”即可。

步骤：
1. 构造 OD 矩阵
- T1 与 T2/T3 高权重
- T2 之间中权重
- T3 到最近高层 Hub 低到中权重

2. 最短路径分配
- 基于现有路网边权（可复用）
- 统计路径经过次数或加权需求量

3. 生成热度
- 输出 `flow`, `congestion_ratio`
- 前端按热度颜色映射显示（蓝 -> 黄 -> 红）

4. 动画效果（前端）
- 边上做脉冲点或渐变滚动，制造“车辆流动”观感

### 10.5 最终合成预览（新增）

MVP 2.5D 方案：
- 按 Hub tier 和道路密度布置建筑 footprint（矩形/多边形占位）
- 对核心区增加高层密度权重
- 对沿河区域加入绿带/公园涂层
- 不追求真实建筑模型，追求演示叙事完整度

---

## 11. 前端时间轴与回放设计（新增）

### 11.1 `Timeline Player` 核心能力

- 播放/暂停/跳转阶段
- 自动切换图层可见性
- 字幕和阶段标题叠加
- 过渡动画（淡入/淡出/线条生长）
- 指标卡片随阶段刷新

### 11.2 前端状态模型（建议）

建议前端将状态分成三类：

- `artifactState`: `final_artifact`, `stages[]`
- `timelineState`: 当前阶段索引、播放时间、播放状态
- `viewState`: 图层开关、缩放、平移、选中对象

### 11.3 前端渲染策略（与现有代码兼容）

- 保留现有 Canvas + SVG 混合结构
- Canvas：热图、道路线、河流、交通热度
- SVG：Hub、资源点、标签、字幕、选中高亮
- 用 `visible_layers` 驱动渲染开关

---

## 12. 模块结构扩展建议（在现有仓库基础上）

新增/调整模块建议：

- `engine/analysis/`
- `suitability.py`（宜居性、洪水风险、人口潜力）
- `resources.py`（资源点生成）
- `demand.py`（OD 矩阵与需求建模）

- `engine/traffic/`
- `assignment.py`（边流量分配）
- `metrics.py`（拥堵指标）

- `engine/staging/`
- `models.py`（StageArtifact、Timeline）
- `builder.py`（阶段快照构建器）
- `narration.py`（字幕模板）

- `web/src/timeline/`
- `TimelinePlayer.tsx`
- `captions.ts`
- `transitions.ts`

- `web/src/render/`
- 扩展现有 `cityRenderer.ts` 支持阶段化图层与交通热度动画

---

## 13. 对标型 MVP 范围（更新版）

### 13.1 In Scope（新增）

- 阶段化生成产物 `stages[]`
- 宜居性/洪水风险/资源/人口潜力图层
- 交通流量分配热度图（非微观车辆）
- 前端时间轴回放与字幕系统
- 最终 2.5D 合成预览（建筑占位级）

### 13.2 Out of Scope（本阶段仍排除）

- 完整粒子水力侵蚀
- 微观车辆 Agent + 信号控制系统
- 真实 LLM 驱动规划决策
- 扩散模型高保真卫星/城市渲染
- 真实 GIS/DEM 数据导入（后续可加）

---

## 14. 验收标准（对标视频叙事版）

### 14.1 叙事完整性（核心）

必须能完整回放 5 个阶段：
- 地形输入
- 分析叠加
- 基础设施规划
- 交通热度模拟
- 最终城市预览

### 14.2 技术正确性（沿用现有标准）

- 固定 seed 与配置输出稳定
- 路网主干连通率 100%
- 重复边 = 0
- 零长度边 = 0
- 非法自交尽可能为 0（当前默认配置目标为 0）

### 14.3 展示效果（MVP）

- 阶段切换顺畅、无明显卡顿
- 字幕与图层对应关系清晰
- 交通热度动画能体现主干道拥堵差异
- 最终合成画面在视觉上明显区别于中间线稿阶段

---

## 15. 实施顺序建议（下一阶段）

建议按以下顺序推进，以最短路径获得“竞品同款叙事效果”：

1. `engine/analysis`（宜居性/资源/人口潜力）
2. `engine/staging` + `POST /api/v1/generate_staged`
3. 前端 `Timeline Player` + 字幕系统
4. `engine/traffic`（边流量分配）+ 交通热度图层
5. 最终 2.5D 合成预览图层

原因：

- 第 1-3 步即可构成“会讲故事”的演示闭环。
- 交通与合成可逐步增强，不会阻塞阶段回放主框架。

---

## 16. 风险与缓解

### 16.1 风险：阶段化输出导致后端 payload 过大

缓解：
- 所有热图仅输出预览分辨率（如 128x128）
- `stages[]` 默认只保存阶段所需图层，不重复全量复制
- 可选开启 `debug/full` 模式

### 16.2 风险：前端时间轴与计算层耦合过深

缓解：
- 前端只消费 `StageArtifact` 和 `Timeline`，不依赖后端内部过程
- 统一图层命名与渲染契约

### 16.3 风险：交通热度效果“不像仿真”

缓解：
- MVP 明确定位为 `flow assignment simulation preview`
- 用阶段字幕与 UI 文案描述为“simulation preview / optimization preview”
- 后续再升级微观仿真

### 16.4 风险：对标视觉差距过大

缓解：
- 先补“叙事结构一致性”而不是“渲染质感一致性”
- 加强分镜、图层节奏、字幕设计、配色与动画过渡

---

## 17. 与当前实现的关系（说明）

本设计文档是对当前 CityGen MVP 的增强设计，不推翻现有实现。当前实现已覆盖：

- Terrain/Hydrology 基础生成
- Hub + Road Network 骨架生成
- 命名 Mock 接口
- FastAPI + Web 可视化基础框架

本设计的目标是在此基础上增加“阶段化分析与展示能力”，使系统具备更强的研究演示价值与竞品对标能力。

---

## 18. 附录：术语对照

- `Compute DAG`：计算依赖图（真实生成流程）
- `Presentation Timeline`：演示时间轴（回放流程）
- `Stage Artifact`：阶段快照（阶段输出的数据包）
- `Suitability Heatmap`：宜居性热力图
- `Flow Assignment`：边流量分配（交通简化仿真）
- `Final Composite Preview`：最终合成预览（MVP 版高质量可视化）

