# DDD架构说明 —— 盲道友好路线规划引擎

## 1. 架构总览

本模块基于**领域驱动设计（DDD）**理念重构，将"盲道友好路线规划"作为核心领域，
清晰分离四层架构：

```
┌─────────────────────────────────────────────────────────┐
│                   Interfaces (接口层)                    │
│  FastAPI路由 / WebSocket / DTO适配 / 外部协议转换         │
├─────────────────────────────────────────────────────────┤
│                  Application (应用层)                     │
│  用例编排: RoutePlanningService / VoiceInteractionService │
├─────────────────────────────────────────────────────────┤
│                 Domain (领域层) 【核心】                   │
│  实体/值对象: Route / Obstacle / TactilePaving            │
│  领域服务: RouteScoringService / ObstacleAggregator       │
│  仓储接口: IRouteRepository / ITactilePavingRepository    │
├─────────────────────────────────────────────────────────┤
│              Infrastructure (基础设施层)                  │
│  高德API / GeoJSON盲道 / 内存障碍物 / Qwen大模型           │
└─────────────────────────────────────────────────────────┘
```

## 2. 目录结构

```
python_worker/
├── domain/                          # 领域层（零外部依赖）
│   ├── model/
│   │   ├── route.py                 # 路线聚合根 + GeoPoint + RoadType
│   │   ├── obstacle.py              # 障碍物实体 + ObstacleHotspot
│   │   ├── tactile_paving.py        # 盲道值对象
│   │   └── navigation_request.py    # 导航请求值对象
│   ├── services/
│   │   ├── route_scoring_service.py # 【核心】路线打分重排序引擎
│   │   └── obstacle_aggregator.py   # 障碍物时空聚合器
│   └── repositories/
│       ├── route_repository.py      # IRouteRepository 接口
│       ├── tactile_paving_repository.py
│       └── obstacle_repository.py
├── application/                     # 应用层（用例编排）
│   ├── route_planning_service.py    # 路线规划编排服务
│   ├── voice_interaction_service.py # 语音交互服务
│   └── dtos/
│       └── route_dtos.py            # 数据传输对象
├── infrastructure/                  # 基础设施层
│   ├── config.py                    # 配置管理
│   ├── amap_client.py               # 高德地图API客户端
│   ├── geojson_tactile_repository.py # GeoJSON盲道仓储
│   ├── in_memory_obstacle_repository.py # 内存障碍物仓储
│   └── qwen_voice_client.py         # 通义千问语音客户端
├── interfaces/                      # 接口层
│   ├── api/
│   │   └── route_endpoints.py       # FastAPI REST端点
│   └── websocket/
│       └── obstacle_report_handler.py # 障碍物WebSocket
├── tests/                           # 测试
│   ├── test_route_scoring.py        # 打分引擎测试
│   ├── test_obstacle_aggregator.py  # 障碍物聚合测试
│   └── test_route_planning_service.py # 集成测试
├── navigation_app.py                # 独立启动入口
└── run_all_tests.py                 # 统一测试运行
```

## 3. 核心业务流程

### 3.1 路线规划主流程

```
用户请求 → NavigationRequest
    ↓
IRouteRepository.fetch_routes()  →  高德API获取多条备选路线
    ↓
ITactilePavingRepository.get_in_bbox()  →  加载静态盲道矢量数据
    ↓
IObstacleRepository.get_hotspots()  →  获取实时障碍物热点
    ↓
RouteScoringService.rank_routes()  →  六维度打分重排序
    ↓
QwenVoiceClient.generate_route_broadcast()  →  大模型生成播报
    ↓
返回 RoutePlanningResult（最优路线 + 所有备选 + 播报文本）
```

### 3.2 路线打分引擎（六维度加权）

| 维度 | 权重 | 说明 |
|------|------|------|
| 距离长度 | 10% | 越短越好 |
| 转弯次数 | 10% | 越少越好 |
| 红绿灯数量 | 10% | 越少越好 |
| 道路类型友好度 | 15% | 盲道>人行道>步行街>斑马线>... |
| **盲道覆盖率** | **30%** | **核心维度，越高越好** |
| **障碍物密度** | **25%** | **核心维度，越低越好（实时）** |

总分 = Σ(weight_i × normalized_score_i) × 100，范围 0~100

### 3.3 障碍物时空聚合

- **空间聚类**: 10米内同类型障碍物合并为热点
- **时间衰减**: 半衰期2分钟，超过5分钟自动清理
- **严重程度加权**: high=2.0, medium=1.0, low=0.5
- **置信度加权**: 检测置信度影响热点权重

## 4. API接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/navigation/plan | 路线规划（核心） |
| POST | /api/navigation/voice | 语音命令处理 |
| POST | /api/obstacle/report | 障碍物上报 |
| GET | /api/obstacle/hotspots | 获取障碍物热点 |
| GET | /api/navigation/status | 服务状态 |
| WS | /ws/obstacle | 障碍物实时上报 |

## 5. 环境变量配置

```bash
# 高德地图API
AMAP_API_KEY=your_amap_key

# 通义千问/DashScope
DASHSCOPE_API_KEY=your_dashscope_key

# 盲道数据
TACTILE_PAVING_GEOJSON=/path/to/blind_paths.geojson

# 服务端口
PYTHON_WORKER_PORT=18082
```

## 6. 运行方式

```bash
# 独立运行路线规划服务
python navigation_app.py

# 运行所有测试
python run_all_tests.py

# 访问API文档
# http://localhost:18082/docs
```

## 7. 与现有系统的集成

- `navigation_app.py` 可独立运行，提供路线规划API
- 现有 `app_main.py` 的视觉导航（盲道跟踪、斑马线、红绿灯）保持不变
- Go后端 `server/` 可通过HTTP调用 `/api/navigation/plan` 获取路线
- ESP32眼镜端通过 `/api/obstacle/report` 或WebSocket上报障碍物
- 大模型语音播报文本可通过现有音频通道下发给眼镜播放
