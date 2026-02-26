# CityGen Major Roads 生成方案说明

本文档说明 Major Roads（Arterial + Collector）的生成架构，特别是 **两阶段生成流水线** 的设计。

---

## 1. 道路层级定义

本项目的道路分为三个层级：

| 层级 | road_class | 说明 |
|------|-----------|------|
| **Arterial** | `arterial` | 主骨架道路，城市级连接 |
| **Collector** | `collector` | 次干/汇集道路，连接主骨架与局部网络 |
| **Local** | `local` | 本地道路，社区内部网络 |

UI 显示分组：
- **Major Roads** = `arterial + collector`
- **Local Roads** = `local`

---

## 2. 两阶段生成流水线

### 2.1 设计背景

传统的单阶段流水线存在 **时序倒置问题**：

```
旧流程：
1. 生成 Arterial
2. 生成 Collector（直接添加到 edges）
3. 生成 Local（基于未处理的 Collector 几何）
4. apply_intersection_operators（全量处理）
5. apply_syntax_postprocess（可能删除 Collector）
```

**问题**：Local roads 在步骤 3 中基于"草稿版" Collector 计算种子点和附着位置，
但 Collector 在步骤 4-5 中可能被修改或删除，导致：
- **几何失真**：Local 与修改后的 Collector 位置不匹配
- **幽灵附着**：Local 附着到被删除的 Collector 节点上

### 2.2 两阶段流水线设计

新流水线通过"冻结机制"确保 Local 基于完全定型的 Major Network 生成：

```
新流程（use_two_phase_generation=True，默认）：

=== Phase 1: Major Network 完全定型 ===
1. 生成 Arterial（骨架）
2. 生成 Collector（generation_phase="collector_only"）
3. apply_intersection_operators(target_classes={"arterial", "collector"})
4. apply_syntax_postprocess(target_classes={"arterial", "collector"})
5. _freeze_major_network() → 创建不可变快照

=== Phase 2: Local Network 基于冻结几何生成 ===
6. 生成 Local（generation_phase="local_only", frozen_major_network=frozen）
7. apply_intersection_operators(target_classes={"local"})

=== Final Processing ===
8. _dedupe_and_snap()
9. _route_all_edges()
```

### 2.3 冻结机制

`FrozenMajorNetwork` 数据类包含：
- `edges`: Major edges 的深拷贝（保留最终 path_points）
- `nodes`: Major nodes 的深拷贝（保留最终位置）
- `local_blocks`: 由冻结几何预计算的 Local blocks
- `river_union`: 缓存的河流联合几何

说明（与 Local 连接策略相关）：
- `local_blocks` 是 **major network 定义的生成域/裁剪域**，不是 local-local 连通的硬边界语义
- 后续 local endpoint bridge（若启用）可跨 `local_blocks` 配对端点，只要地形/河流/A* 代价约束允许
- 也就是说：major 冻结负责给 local 提供稳定 blocks，但不应阻止 local 网络在后处理阶段做跨 block 的连通补救

冻结发生在 Phase 1 完成后，此时：
- Collector 已经过 intersection operators 和 syntax postprocess
- 低度数 Collector 已被删除
- 几何位置已经确定

---

## 3. 关键函数参数

### 3.1 generate_roads()

```python
generate_roads(
    ...,
    use_two_phase_generation: bool = True,  # 是否使用两阶段流水线
)
```

- `True`（默认）：使用两阶段流水线，Local 基于冻结几何生成
- `False`：使用传统单阶段流水线（向后兼容）

### 3.2 _generate_hierarchy_linework()

```python
_generate_hierarchy_linework(
    ...,
    generation_phase: str = "both",  # "both" | "collector_only" | "local_only"
    frozen_major_network: Optional[FrozenMajorNetwork] = None,
)
```

- `"both"`：生成 Collector 和 Local（传统模式）
- `"collector_only"`：仅生成 Collector，然后返回
- `"local_only"`：跳过 Collector 生成，使用 `frozen_major_network` 中的 blocks

### 3.3 apply_intersection_operators()

```python
apply_intersection_operators(
    ...,
    target_classes: Optional[Set[str]] = None,  # 限制处理的道路等级
)
```

- `None`：处理所有道路（传统模式）
- `{"arterial", "collector"}`：仅处理 Major roads
- `{"local"}`：仅处理 Local roads

### 3.4 apply_syntax_postprocess()

```python
apply_syntax_postprocess(
    ...,
    target_classes: Optional[Set[str]] = None,  # 限制处理的道路等级
)
```

---

## 4. Local Roads 种子点计算

Local roads 的种子点从 `arterial + collector` 的 path_points 预计算：

```python
# engine/roads/classic_local_fill.py
for edge in edges:
    if edge.road_class not in {"arterial", "collector"}:
        continue
    pts = _iter_polyline_points(edge, node_lookup)
    # 沿 Major road 每 400-500m 取样一个种子点
    for dist_m in sample_dists:
        p, tan = _sample_polyline_point_and_tangent(pts, dist_m)
        seeds.append((p, tan))
```

在两阶段流水线中，这些种子点基于 **冻结后的 Major 几何** 计算，
确保 Local 不会附着到被删除的 Collector 上。

---

## 5. 向后兼容

- `use_two_phase_generation=False` 时，使用传统单阶段流水线
- 所有新增参数均有默认值，不影响现有调用
- `_generate_hierarchy_linework(generation_phase="both")` 等同于原有行为

---

## 6. 关键文件

| 文件 | 说明 |
|------|------|
| `engine/roads/network.py` | 主流程、冻结机制、两阶段调度 |
| `engine/roads/intersections.py` | 路口处理（支持 target_classes） |
| `engine/roads/syntax.py` | 语法后处理（支持 target_classes） |
| `engine/roads/classic_local_fill.py` | Local 生成（使用冻结几何） |
