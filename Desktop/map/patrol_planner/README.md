# 巡逻路径规划工具

离线为 NovaJoy 楼层地图生成 **UVC 巡视巡逻点**、**TSP 访问顺序** 与 **叠加可视化图**。  
本工具不修改车端代码，输出 JSON + PNG 供人工验收与后续扫楼模块接入。

## 环境

- Python 3.10+
- Windows / Linux PC 均可

```powershell
cd 002-630\Desktop\map\patrol_planner
pip install -r requirements.txt
```

## 简易 UI（推荐）

```powershell
python patrol_ui.py
# 或双击 启动巡逻规划UI.bat
```

### 模式说明

| 生成方式 | 范围 | 行为 |
|----------|------|------|
| **自动规划** | 单图 / 全楼 | 算法自动生成巡逻点（原 CLI 逻辑） |
| **手动转圈点** | 单图 / 全楼 | 左键标「停靠转圈点」，车到该处 **原地 360°**；绿区为实时可见范围预览 |

### 手动转圈点

- **目的**：你只决定「在哪停、在哪转圈」，不要求算法算全覆盖
- **左键**：添加转圈点 `S1/S2/...`（吸附可活动区）
- **右键**：删除最近转圈点
- **绿区预览**：当前锚点 + 已标记点若原地 360° 转圈，能看到的范围（墙体遮挡已算）
- **锚点 A**：路线起终点，**不转圈**；转圈仅发生在 `S*` 点
- JSON 中巡逻点带 `"action": "spin_360"`

### UI 操作

- **全楼 / 单图结果预览**：顶部 `◀` `▶` 翻页，或下拉选择叠加图
- **全楼手动标记**：「加载地图标记」后，用 `标记楼层` 下拉或 `◀` `▶` 切换各层，分别标点
- **左键**：添加巡逻点（吸附可活动区）；**右键**：删除最近点
- **清空本层点**：清除当前层已标记点

界面功能：

- 单图 / 全楼模式切换
- 地图目录、输出目录、switcher 路径浏览选择
- 覆盖模式、未覆盖容差、采样步长
- 一键生成 + 日志输出
- **叠加图预览**（全楼多张可下拉切换）
- 打开输出目录

生成在后台线程执行，界面不会卡死。

## 命令行（CLI）

```powershell
# 全楼模式（读取 switcher_node.py 的 FLOOR_MAPS）
python generate_patrol.py building

# 单图模式（蓝点，可用于未登记楼层或预留地图）
python generate_patrol.py single --map my_map3.yaml

# 走廊优先覆盖（房间深处允许少量未覆盖）
python generate_patrol.py building --coverage-mode corridor_priority

# 自定义输出目录
python generate_patrol.py building --out-dir D:\tmp\patrol_run_01

# 允许最多 5% 栅格未覆盖（减少巡逻点数）
python generate_patrol.py building --max-uncovered-ratio 0.05
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--map-dir` | `ros_ws/install/.../rt_robot_nav2/map` | 地图目录（pgm + yaml） |
| `--out-dir` | `002-630/Desktop/map/patrol_out` | 输出根目录（可任意可写路径） |
| `--switcher` | 自动查找 `ros_ws/.../switcher_node.py` | 楼层与房间配置来源 |
| `--coverage-mode` | `full_free` | `full_free` 全部自由空间；`corridor_priority` 走廊优先 |
| `--max-uncovered-ratio` | `0`（尽量 100%） | 允许未覆盖比例 0~1 |
| `--sample-step` | `0.25` | 候选点采样步长（米） |
| `--inflate` | `0.3` | 障碍膨胀半径（米），停靠点安全边距 |

子命令：

- `building` — 全楼：仅处理 `FLOOR_MAPS` 中登记的楼层；叠加图 **红点**
- `single --map <yaml>` — 单图：任意地图；叠加图 **蓝点**

## 输出结构

```
{out-dir}/
  manifest.json                 # 地图 hash、参数快照、警告
  building/                     # 全楼模式
    patrol_1F.json
    patrol_2F.json
    overlay_my_map3_patrol_red.png
  single/                       # 单图模式
    patrol_my_map3.json
    overlay_my_map3_patrol_blue.png
```

### JSON 字段（节选）

```json
{
  "floor": "1F",
  "map_yaml": "my_map3.yaml",
  "coverage_mode": "full_free",
  "coverage_ratio": 0.95,
  "anchor": { "kind": "anchor", "label": "100", "x": 6.17, "y": -3.17, "yaw": 0.134 },
  "waypoints": [
    { "index": 0, "kind": "anchor", "label": "100", "x": 6.17, "y": -3.17, "yaw": 0.134 },
    { "index": 1, "kind": "patrol", "label": "P1", "x": 1.2, "y": 3.4, "yaw": 1.57 }
  ],
  "route_order": [0, 3, 1, 2, 0],
  "route_length_m": 42.5
}
```

- **锚点**：1F 使用 `ROOM_LOCATIONS["100"]`；其余楼层使用该图 yaml 的 `initial_pose`
- **巡逻点 yaw**：朝向 TSP 下一站点（原地转圈模式下执行时可忽略）

## 可视化图例

| 元素 | 含义 |
|------|------|
| 绿色三角 `A` | 锚点（出入点） |
| 蓝/红圆点 + 序号 | 巡逻点及访问顺序 |
| 灰白连线 | TSP 路径 |
| 半透明绿 | 已覆盖区域 |
| 深红 | 未覆盖（必覆盖区） |
| 浅灰 | 未覆盖（走廊优先模式下的房间区） |

## 配置来源

楼层与锚点规则从 `switcher_node.py` 自动解析（与 `UI/backend/vehicle_rooms.py` 相同思路）：

- `FLOOR_MAPS` — 全楼模式处理哪些层、对应哪张 yaml
- `ROOM_LOCATIONS` — 1F 锚点房间 `100`

可通过环境变量指定 ros_ws：

```powershell
$env:AI_CAR_ROS_WS = "D:\cd\NovaJoy\002-630\Desktop\ros_ws"
python generate_patrol.py building
```

## 注意事项

1. **FLOOR_MAPS 需与真车一致**  
   若 `1F`/`2F` 指向同一张图，工具会 warning 且只规划一次。正式使用前请在 `switcher_node.py` 中改为例如 `1F: my_map3.yaml`、`2F: my_map5.yaml`。

2. **不在 FLOOR_MAPS 的地图**  
   - 全楼模式：忽略  
   - 单图模式：可生成；无楼层登记时用 yaml `initial_pose` 作锚点

3. **覆盖率**  
   复杂户型 + 墙体遮挡下，默认参数可能无法达到 100% 覆盖。可：
   - 放宽 `--max-uncovered-ratio 0.05`（UI 中填 `0.05`）
   - 查看叠加图热力区人工判断
   - 使用 `--coverage-mode corridor_priority` 减少点数

### 实际可活动区域

工具按 **Nav2 三值地图** 语义解析 PGM：

| 像素值 | 含义 | 是否可巡逻 |
|--------|------|------------|
| 254 | 自由空间 | 是（经 0.3m 膨胀后） |
| 0 | 占用 / 墙体 | 否 |
| 205 | 未知 | 否（不再误判为可通行） |

并仅保留 **与锚点连通的封闭主区域**（四连通泛洪），剔除地图边缘 unknown 假自由区与孤立小块。

## 算法概要

1. 读取 PGM/YAML，按 Nav2 三值阈值解析自由空间，0.3m 膨胀  
2. 降采样规划栅格上贪心 set cover（360° 射线遮挡）  
3. 全分辨率验证覆盖，TSP 最近邻排序  
4. 输出 JSON + manifest + 叠加 PNG  

## 目录

```
patrol_planner/
  patrol_ui.py            # 简易 UI 入口（推荐）
  启动巡逻规划UI.bat      # Windows 双击启动
  generate_patrol.py      # CLI 入口
  patrol_runner.py        # CLI / UI 共用执行逻辑
  patrol_core.py          # 规划 / 可视化核心
  switcher_config.py      # 解析 switcher_node.py
  requirements.txt
  README.md
```
