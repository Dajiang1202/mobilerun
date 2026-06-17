# 金铲铲之战全自动 AI Bot — 设计规范

**日期**: 2026-05-30
**版本**: 1.0
**项目代号**: golden-spatula-bot

---

## 1. 概述

### 1.1 目标

构建一个全自动的金铲铲之战（TFT手游国服）AI Bot，通过纯视觉感知理解游戏状态，使用规则引擎+本地LLM混合决策，通过HDC控制鸿蒙手机完成操作。支持自动开局、匹配、对战、判断胜负、循环多场。

### 1.2 核心需求

| 需求 | 说明 |
|------|------|
| 纯视觉感知 | 仅通过截图识别游戏状态，不依赖任何游戏内部API |
| 实时性 | 备战阶段30秒内完成所有操作，单帧处理<200ms |
| 全自动循环 | 从启动APP到完成对局再到下一局，无人干预 |
| 内网离线 | 所有模型本地部署，不连接外网 |
| 跨赛季迁移 | 新赛季仅需1-2天配置更新即可适配 |
| 多机型支持 | 分辨率自适应，支持不同鸿蒙手机 |
| 战胜人类 | 至少击败初级人类玩家（黄金/白金段位稳定前4） |

### 1.3 技术栈

| 层次 | 技术选型 |
|------|---------|
| 语言 | Python 3.11+ |
| 设备控制 | HDC (HarmonyOS Device Connector) |
| OCR | PaddleOCR v4 (GPU) |
| 目标检测 | YOLOv8m (自训练) |
| 棋子识别 | 模板匹配 (OpenCV matchTemplate) |
| LLM 推理 | vLLM + Qwen2.5-14B-Instruct-GPTQ-Int4 |
| VLM 兜底 | Qwen2.5-VL-7B-Instruct |
| 异步框架 | asyncio + ThreadPoolExecutor |
| 部署 | Docker + NVIDIA Container Runtime |

### 1.4 硬件环境

| 组件 | 规格 |
|------|------|
| GPU (推荐) | RTX 5090D 32GB 或 Tesla V100 32GB |
| GPU (最低) | RTX 3060 12GB（仅能跑7B模型） |
| CPU | i7-12700K 或同级 |
| RAM | 32GB+ |
| 连接 | USB 3.0 → 鸿蒙手机 (HDC) |

---

## 2. 架构

### 2.1 整体架构

**架构选型**: 流水线 + 状态机 + 本地LLM混合架构

选择流水线架构而非通用游戏AI框架（如Cradle），因为：
- 通用框架每帧调LLM导致延迟过高（2-5秒/次），30秒备战时间不够
- 金铲铲手游有特定UI布局，需要精确定制
- 90%的决策可以用规则引擎在<10ms内完成，LLM仅处理5%的战略决策

```
┌───────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  鸿蒙手机   │────▶│  感知层   │────▶│  决策层   │────▶│  执行层   │
│  HDC 截屏   │     │ OCR+CV   │     │ 规则+LLM │     │ HDC 触摸  │
└───────────┘     └──────────┘     └──────────┘     └──────────┘
      ▲                │                │                 │
      │                ▼                ▼                 │
      │          ┌──────────┐    ┌──────────┐             │
      └──────────│ 游戏状态  │    │  策略库   │             │
                 │ (JSON)   │    │ (配置)   │             │
                 └──────────┘    └──────────┘             │
                                                          │
      ┌───────────┐                                      │
      │ 游戏生命周期 │◀───────────────────────────────────┘
      │  状态机     │
      └───────────┘
```

### 2.2 数据流

```
HDC截图 → 视觉管线(并行处理) → GameState(JSON) → 决策引擎 → Action[] → HDC操作 → 截图验证
```

### 2.3 延迟预算

| 模块 | 延迟目标 | 方法 |
|------|---------|------|
| HDC截屏 | <200ms | hdc uitest screenCap (Phase 1), scrcpy推流 <30ms (Phase 3) |
| 视觉管线 | <200ms | 5个子任务并行执行 |
| 规则决策 | <10ms | 纯Python逻辑 |
| 查表决策 | <100ms | JSON/SQLite查询 |
| LLM决策 | 2-5秒 | vLLM + 前缀缓存，每局仅调用3-5次 |
| 操作注入 | <50ms | HDC uitest 命令 |

---

## 3. 游戏生命周期状态机

### 3.1 设计理念

采用**事件驱动**模式而非固定顺序。回合内的事件（备战/战斗/选秀/海克斯/拾取/PvE）出现顺序和次数不固定，由视觉检测器实时判断当前事件类型。

### 3.2 状态定义

```
IDLE → APP_LAUNCH → LOBBY → QUEUE → LOADING → ROUND_LOOP → RESULT → POST_GAME → LOBBY
```

#### 全局状态

| 状态 | 视觉检测标志 | 动作 | 超时处理 |
|------|-------------|------|---------|
| `IDLE` | 无 | 等待启动指令 | - |
| `APP_LAUNCH` | 游戏主进程 | 等待加载完成 | 60s超时→重启APP |
| `LOBBY` | 大厅UI元素（开始按钮） | 点击"排位赛"→"开始匹配" | 30s无操作→截图诊断 |
| `QUEUE` | 匹配中界面 | 等待，定期检查 | 5min超时→取消重排 |
| `LOADING` | 加载进度条 | 等待，初始化游戏状态 | 120s超时→异常 |
| `RESULT` | 结算排名界面 | 记录排名，点击继续 | 自动点击返回 |
| `POST_GAME` | 返回大厅过渡 | 处理奖励弹窗，回到大厅 | 30s超时→强制导航 |

#### 回合内事件（由事件检测器识别）

| 事件 | 检测条件 | 处理器 | 时间约束 |
|------|---------|--------|---------|
| `planning` | 备战倒计时+商店可见 | PlanningHandler | 倒计时结束前完成 |
| `combat` | 战斗动画中 | CombatHandler | 无（观察+预计算） |
| `carousel` | 选秀转盘界面 | CarouselHandler | 10s内必须操作 |
| `augment` | 三选一强化界面 | AugmentHandler | 15s内必须选 |
| `loot_orb` | 装备球出现 | LootHandler | 5s内拾取 |
| `pve_round` | 野怪回合标识 | PvEHandler | 正常备战 |
| `game_over` | 游戏结束动画 | ResultHandler | - |
| `unknown` | 无法识别 | ErrorRecovery | 3次失败→重启APP |

### 3.3 事件检测器

```python
class RoundEventDetector:
    """
    并行检测所有可能的事件，返回置信度最高的事件
    每个检测 <20ms（模板匹配+颜色特征）
    """
    PHASE_SIGNATURES = {
        "lobby":     {"region": (0.4, 0.8, 0.6, 0.95), "template": "lobby_start_btn"},
        "queue":     {"region": (0.3, 0.4, 0.7, 0.6),  "template": "queue_searching"},
        "carousel":  {"region": (0.0, 0.0, 1.0, 0.15), "template": "carousel_banner"},
        "planning":  {"region": (0.85, 0.0, 1.0, 0.1), "template": "planning_timer"},
        "combat":    {"region": (0.0, 0.0, 0.15, 0.1), "template": "combat_indicator"},
        "augment":   {"region": (0.1, 0.2, 0.9, 0.8),  "template": "augment_frame"},
        "result":    {"region": (0.3, 0.1, 0.7, 0.4),  "template": "result_rank"},
    }
```

### 3.4 异常恢复

任意状态遇到异常时进入 `ERROR_RECOVERY`：
1. 截图分析当前画面
2. 尝试关闭弹窗（检测关闭按钮模板）
3. 尝试断线重连（检测重连按钮模板）
4. 3次恢复失败 → 重启APP → 从LOBBY重新开始

---

## 4. 设备控制层 (HDC)

### 4.1 分辨率自适应坐标系统

所有内部逻辑使用**归一化坐标** `(0.0 ~ 1.0)`，运行时根据实际手机分辨率转换为像素坐标。

```python
class CoordAdapter:
    def to_pixel(self, nx: float, ny: float) -> tuple[int, int]:
        return int(nx * self.w), int(ny * self.h)

    def to_normalized(self, px: int, py: int) -> tuple[float, float]:
        return px / self.w, py / self.h
```

### 4.2 HDC 控制器

```python
class HDCController:
    """通过 HDC 协议与鸿蒙手机通信"""

    # 截屏
    def screenshot(self) -> np.ndarray:
        """
        Phase 1: hdc shell uitest screenCap (~200ms/帧)
        Phase 3: scrcpy协议推流 (~30ms/帧)
        """

    # 触摸注入
    def tap(self, nx, ny):
        """归一化坐标点击，带 ±3px 人类随机偏移"""
        # hdc shell uitest screenClick px py

    def drag(self, nx1, ny1, nx2, ny2, duration_ms=300):
        """拖拽（移动棋子、装备）"""
        # hdc shell uitest screenSwipe x1 y1 x2 y2 duration

    # APP管理
    def launch_app(self, package_name):
        # hdc shell aa start -a EntryAbility -b {package_name}

    def get_foreground_app(self) -> str:
        # 检测当前前台应用
```

### 4.3 操作时序

```python
class ActionQueue:
    TIMING = {
        "tap_after_tap":    (0.05, 0.15),   # 连续点击间隔 50-150ms
        "drag_start_delay": (0.02, 0.08),   # 拖拽前停顿
        "drag_end_delay":   (0.05, 0.10),   # 拖拽后等待
        "shop_buy_delay":   (0.10, 0.25),   # 买人后等UI响应
        "between_actions":  (0.03, 0.10),   # 通用间隔
    }
```

所有操作延迟加入随机抖动（±30%），模拟人类行为模式。

### 4.4 截屏优化路线

| 阶段 | 方案 | 延迟 | 复杂度 |
|------|------|------|--------|
| Phase 1 | `hdc shell uitest screenCap` | ~200ms | 低 |
| Phase 2 | `hdc snapshot_display` + pipe | ~150ms | 低 |
| Phase 3 | scrcpy 协议推流 | ~30ms | 高 |

---

## 5. 视觉管线（感知层）

### 5.1 流水线结构

```
截图 → ① 阶段检测 (<20ms)
     → ② 基础信息 OCR (<50ms)      ┐
     → ③ 商店识别 (<80ms)           ├ 并行执行
     → ④ 棋盘/备战席检测 (<100ms)    │
     → ⑤ 装备识别 (<80ms)           ┘
     → ⑥ 特殊事件识别（偶发）
     → GameState (JSON)
```

**总延迟目标: <200ms/帧**（子任务并行，取最长耗时）

### 5.2 归一化 ROI（感兴趣区域）

所有区域坐标使用 `(left, top, right, bottom)` 归一化到 `0.0~1.0`：

```python
REGIONS = {
    "gold":        (0.02, 0.88, 0.08, 0.93),
    "hp":          (0.02, 0.02, 0.12, 0.06),
    "level":       (0.02, 0.78, 0.08, 0.85),
    "round":       (0.44, 0.01, 0.56, 0.05),
    "shop_row":    (0.08, 0.82, 0.92, 0.94),
    "shop_slot_1": (0.08, 0.82, 0.24, 0.94),
    "shop_slot_2": (0.25, 0.82, 0.41, 0.94),
    "shop_slot_3": (0.42, 0.82, 0.58, 0.94),
    "shop_slot_4": (0.59, 0.82, 0.75, 0.94),
    "shop_slot_5": (0.76, 0.82, 0.92, 0.94),
    "refresh_btn": (0.01, 0.72, 0.08, 0.78),
    "buy_xp_btn":  (0.01, 0.64, 0.08, 0.70),
    "board":       (0.15, 0.15, 0.85, 0.60),
    "bench":       (0.15, 0.62, 0.85, 0.72),
    "augment_area": (0.10, 0.15, 0.90, 0.75),
    "carousel_area": (0.20, 0.20, 0.80, 0.70),
}
```

**注意**: 以上坐标为基于横屏模式的初始估计值，需通过 `roi_calibrator.py` 工具在实际手机上校准。

### 5.3 OCR 引擎

- **模型**: PaddleOCR v4 (GPU)
- **用途**: 读取金币、血量、等级、经验、回合数等固定位置的数字/文字
- **优化**: 4个OCR任务并行执行（ThreadPoolExecutor）
- **降级**: 识别失败时多次截图投票（3次取众数）

### 5.4 棋子识别

- **方法**: 模板匹配（OpenCV `matchTemplate` + `TM_CCOEFF_NORMED`）
- **数据源**: 官方棋子头像图标（从 CommunityDragon 或游戏资源提取）
- **阈值**: >0.7 判定为匹配
- **棋盘检测**: 将棋盘区域划分为网格（金铲铲之战手游棋盘为 3行×7列 的玩家半场，具体需通过 `roi_calibrator.py` 在实际手机上校准确认），逐格检测
- **星级判断**: 通过棋子下方的星标颜色/数量识别 1星/2星/3星
- **跨赛季**: 替换 `assets/champion_icons/` 目录即可

### 5.5 装备识别

- **方法**: 小图标模板匹配
- **区域**: 棋子底部 20px 区域（装备图标显示位置）
- **阈值**: >0.75（装备图标较小，需要更高阈值减少误判）
- **合成表**: 查表引擎提供最优合成路径

### 5.6 降级策略

当视觉识别置信度不足时：
1. 增大裁剪区域，提高OCR识别率
2. 多次截图投票（3次取众数）
3. 本地VLM（Qwen2.5-VL-7B）分析截图
4. 跳过该帧，等待下一帧重新识别

---

## 6. 决策引擎（三层混合架构）

### 6.1 决策路由

```
GameState → Decision Router → L1规则 / L2查表 / L3 LLM → Action[]
```

根据当前事件类型路由到不同决策层：

| 事件 | 主要决策层 | 辅助决策层 |
|------|-----------|-----------|
| `planning` | L1 规则（买/卖/经济） + L2 查表（装备/站位） | L3 LLM（仅前期阵容规划） |
| `augment` | L3 LLM | - |
| `carousel` | L1 规则（抢最贵/最需要的） | - |
| `loot_orb` | L1 规则（拾取） | - |
| `combat` | 无操作（后台预计算） | - |

### 6.2 L1 规则引擎（<10ms）

处理 90% 的高频决策：

#### 商店决策（买/不买）

优先级从高到低：
1. **目标阵容核心棋子** → 必买
2. **能升2星/3星的棋子**（已有2个同名 → 买第3个）→ 必买
3. **强力过渡棋子**（前3回合的1-2费卡）→ 看经济买
4. **其他** → 不买

#### 经济决策

- **血量 > 50**: 保持50金吃利息，多余的钱升人口
- **血量 30-50**: 保持30金，适度消费
- **血量 < 30**: 濒死模式，全花刷新找关键棋子

#### 升级优先级

- 检测所有可升星棋子（3个同名同星 = 升1星）
- 执行拖拽合成

### 6.3 L2 查表引擎（<100ms）

#### 装备分配

- 输入: 当前散件列表 + 目标C位/坦位
- 查表: `item_combinations.json` → 最优成装合成路径
- 输出: 把散件拖到目标棋子上的操作序列

#### 站位模板

- 基础模板: 坦克放前两排，输出放后两排
- 进阶模板: 根据阵容名查预设站位
- 高级（Phase 3）: 根据对手站位动态调整

### 6.4 L3 本地LLM（2-5秒/次）

仅在关键决策点介入，每局调用3-5次：

| 调用时机 | 次数 | 单次延迟 | 说明 |
|---------|------|---------|------|
| 阵容规划 (1-4回合) | 1次 | ~3秒 | 根据来牌决定阵容方向 |
| 海克斯选择 | 最多3次 | ~2秒 | 三选一强化 |
| 转型决策 | 0-2次 | ~3秒 | 血量低+来牌差时触发 |
| 异常兜底 | 0-3次 | ~2秒 | 规则引擎无法处理时 |
| **总计** | **4-9次/局** | | **~15秒/局** |

#### LLM Prompt 设计原则

- System prompt 精简到 500 token 以内（利用前缀缓存）
- 要求返回结构化 JSON（100-200 token response）
- 低温度采样 (temperature=0.3) 保证策略稳定性

### 6.5 预计算优化

战斗阶段（无法操作时），后台异步执行：
1. 预跑 LLM 分析阵容方向（如果需要）
2. 重建装备合成查找表
3. 预评估下一回合可能的商店决策

---

## 7. 模型服务层

### 7.1 模型选型

| 模型 | 用途 | 参数量 | 显存需求 | 推理速度(5090D) |
|------|------|--------|---------|----------------|
| YOLOv8m | 目标检测 | ~26M | ~3GB | <10ms/帧 |
| PaddleOCR v4 | 文字识别 | ~10M | ~1GB | <20ms/帧 |
| Qwen2.5-14B-Instruct-GPTQ-Int4 | 策略决策 | 14B | ~10GB | ~80 tok/s |
| Qwen2.5-VL-7B-Instruct | 视觉兜底 | 7B | ~8GB | ~60 tok/s |

### 7.2 显存分配方案

#### 单卡方案（RTX 5090D 32GB 或 V100 32GB）

```
YOLO ~3GB + OCR ~1GB + LLM ~10GB + VLM ~8GB = ~22GB
剩余 ~10GB 用于 KV Cache + 余量
```

#### 双卡分离方案

```
V100 (32GB):    YOLO + OCR + CLIP = ~5GB（CV推理）
5090D (32GB):   LLM + VLM = ~18GB（语言模型推理）
```

### 7.3 vLLM 配置

```python
AsyncEngineArgs(
    model="Qwen2.5-14B-Instruct-GPTQ-Int4",
    gpu_memory_utilization=0.45,
    max_model_len=4096,
    tensor_parallel_size=1,
    dtype="float16",
    quantization="gptq",
    enable_prefix_caching=True,  # System prompt 缓存
)
```

### 7.4 前缀缓存优化

System prompt 只计算一次（Prefill），后续调用仅计算 user prompt：
- Prefill 从 ~2000ms → ~200ms（节省 90%）
- 单次 LLM 调用总延迟: ~2-4秒（5090D）/ ~4-6秒（V100）

### 7.5 离线模型更新

```
有网环境: 下载模型 → 打包 tar.gz → USB 传输
无网环境: 解压到 models/ → 重启服务
```

---

## 8. 赛季配置系统

### 8.1 设计理念

代码中不硬编码任何赛季内容，所有赛季相关数据外置为 JSON 配置文件。新赛季仅需替换配置文件+图标资源即可。

### 8.2 配置结构

```
config/sets/set{N}/
├── meta.json                  # 赛季元数据
├── champions.json             # 棋子列表（名称/费用/羁绊/定位）
├── traits.json                # 羁绊列表
├── items.json                 # 装备列表
├── item_combinations.json     # 装备合成表（8基础件 → 所有成装）
├── comps.json                 # 阵容库（核心/灵活棋子/装备/站位）
├── ui_regions.json            # UI区域坐标（仅UI变化时修改）
└── assets/
    ├── champion_icons/        # 官方棋子头像
    └── item_icons/            # 官方装备图标
```

### 8.3 赛季迁移工具

```python
class SeasonMigrator:
    """
    半自动化赛季迁移:
    1. 从社区API/wiki爬取新赛季棋子/装备/羁绊数据
    2. 下载官方图标资源
    3. 用LLM生成初始阵容推荐
    4. 人工校验后上线
    """
```

### 8.4 热更新

ConfigManager 监听配置文件变更，运行时自动重载。用于赛季中途调整阵容权重和策略参数。

---

## 9. 项目结构

```
golden-spatula-bot/
├── pyproject.toml
├── docker-compose.yml
├── config/sets/set{N}/          # 赛季配置
├── models/                      # 模型权重（不入git）
├── src/
│   ├── main.py                  # 入口
│   ├── device/                  # HDC控制 + 坐标适配
│   ├── vision/                  # 视觉管线
│   ├── brain/                   # 决策引擎（规则+查表+LLM）
│   ├── game/                    # 游戏生命周期状态机
│   ├── models/                  # 模型服务管理
│   └── utils/                   # 工具函数
├── tools/                       # 辅助工具（截图查看/校准/迁移）
├── tests/                       # 测试 + 测试用截图
└── data/                        # 运行数据（日志/统计）
```

---

## 10. 实施路线

> **实施计划分批**: 第一个实施计划仅覆盖 Phase 0 + Phase 1（基础验证 + 完整游戏循环）。后续 Phase 在前两阶段验证后再制定详细实施计划。

### Phase 0: 基础验证（1周）

- 鸿蒙手机HDC连接+截屏+触摸注入验证
- 基础OCR识别金币/血量
- 确认金铲铲之战的APP包名和UI布局

### Phase 1: 完整游戏循环（2-3周）

- 游戏生命周期状态机（大厅→匹配→对战→结算→下一局）
- 基础商店识别+买人操作
- 最简规则引擎（保持50金+买目标棋子）
- **里程碑: 能自动完成一整局游戏**

### Phase 2: 核心战力（2-3周）

- 完整的视觉管线（棋子/装备/棋盘识别）
- 装备合成系统
- 基础站位
- 阵容规划器
- **里程碑: 能击败初级人类玩家**

### Phase 3: 进阶优化（2-3周）

- 本地LLM接入（海克斯选择/阵容规划/异常兜底）
- scrcpy推流优化截屏延迟
- 对手站位识别+动态调整
- 选秀/战利品球处理

### Phase 4: 工程化（1-2周）

- 赛季迁移工具
- ROI校准工具
- Docker部署
- 对局统计与分析面板

### Phase 5: 持续迭代

- 策略权重调优（基于胜负反馈）
- 阵容池扩展
- 赛季更新适配

---

## 11. 测试、调试与数据回灌系统

由于无法在商用游戏中暂停调试，必须设计完善的独立测试和数据回灌机制。

### 11.1 设计原则

- **每个模块必须可独立测试**，不依赖游戏运行
- **实机测试时自动录制所有关键数据**（截图、状态、决策、操作）
- **录制数据可回灌到任意模块**，离线复现和调试

### 11.2 数据采集管线

实机运行时，后台自动录制以下数据（零侵入）：

```python
class DataRecorder:
    """
    实机测试数据采集器
    每一帧、每一个决策、每一个操作都记录下来
    用于离线调试和回灌测试
    """

    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.frame_id = 0

    def record_frame(self, screenshot, game_state, decisions, actions):
        """
        记录一帧的完整数据
        """
        frame_dir = f"{self.session_dir}/frames/{self.frame_id:06d}"
        os.makedirs(frame_dir, exist_ok=True)

        # 1. 保存原始截图
        cv2.imwrite(f"{frame_dir}/screenshot.png", screenshot)

        # 2. 保存游戏状态 (视觉管线输出)
        save_json(f"{frame_dir}/game_state.json", game_state.to_dict())

        # 3. 保存决策结果
        save_json(f"{frame_dir}/decisions.json", [d.to_dict() for d in decisions])

        # 4. 保存操作序列
        save_json(f"{frame_dir}/actions.json", [a.to_dict() for a in actions])

        # 5. 保存截图裁剪区域（用于调试ROI）
        save_json(f"{frame_dir}/roi_debug.json", self.get_roi_debug_info())

        self.frame_id += 1

    def record_event(self, event_type: str, data: dict):
        """记录离散事件（阶段切换、胜负判定等）"""
        save_json(
            f"{self.session_dir}/events/{self.frame_id:06d}_{event_type}.json",
            data
        )

    def record_game_summary(self, summary: dict):
        """
        每局结束时保存总结
        """
        save_json(f"{self.session_dir}/summary.json", {
            "final_rank": summary["rank"],
            "total_frames": self.frame_id,
            "avg_frame_latency_ms": summary["avg_latency"],
            "decisions_by_layer": summary["l1_count", "l2_count", "l3_count"],
            "errors": summary["errors"],
            "duration_minutes": summary["duration"],
        })
```

#### 采集的数据结构

```
data/replays/
└── 2026-05-30_game_001/
    ├── metadata.json           # 对局元信息（时间/赛季/手机型号/分辨率）
    ├── summary.json            # 对局总结（排名/帧数/延迟/错误）
    ├── events/                 # 离散事件
    │   ├── 000042_phase_change_planning.json
    │   ├── 000156_phase_change_combat.json
    │   └── 001200_game_over.json
    └── frames/                 # 逐帧数据
        ├── 000000/
        │   ├── screenshot.png      # 原始截图
        │   ├── game_state.json     # 视觉管线输出
        │   ├── decisions.json      # 决策引擎输出
        │   ├── actions.json        # 操作序列
        │   └── roi_debug.json      # ROI裁剪调试信息
        ├── 000001/
        │   └── ...
        └── ...
```

### 11.3 模块独立测试接口

每个模块提供独立的 CLI 测试入口，使用录制数据而非实时游戏：

```python
# tools/test_vision.py
"""
视觉管线独立测试

用法:
  # 测试单张截图
  python tools/test_vision.py --image data/replays/xxx/frames/000042/screenshot.png

  # 测试整个录制
  python tools/test_vision.py --replay data/replays/2026-05-30_game_001/

  # 对比视觉输出与人工标注
  python tools/test_vision.py --replay xxx --labels data/labels/game_001.json

输出:
  - 每帧的识别结果（可视化标注在截图上）
  - 识别准确率统计
  - 失败帧列表（用于定位问题）
"""

# tools/test_decisions.py
"""
决策引擎独立测试（回灌模式）

用法:
  # 用录制的 game_state 回灌决策引擎
  python tools/test_decisions.py --replay data/replays/xxx/

  # 对比 AI 决策与实际执行的操作
  python tools/test_decisions.py --replay xxx --compare-human

输出:
  - 每帧 AI 会做什么决策
  - 与实际操作/人类操作的对比
  - 决策层调用统计（L1/L2/L3各多少次）
"""

# tools/test_hdc.py
"""
设备控制层独立测试

用法:
  # 测试HDC连接
  python tools/test_hdc.py --test connect

  # 测试截屏
  python tools/test_hdc.py --test screenshot --output test_screenshot.png

  # 测试点击（在指定坐标点击）
  python tools/test_hdc.py --test tap --x 0.5 --y 0.5

  # 测试拖拽
  python tools/test_hdc.py --test drag --x1 0.3 --y1 0.7 --x2 0.5 --y2 0.4
"""

# tools/test_llm.py
"""
LLM 策略独立测试

用法:
  # 用录制的状态测试 LLM 决策
  python tools/test_llm.py --replay data/replays/xxx/ --phase augment

  # 测试阵容规划
  python tools/test_llm.py --test comp_plan --state state.json
"""
```

### 11.4 数据回灌与模拟

```python
class ReplaySimulator:
    """
    回灌模拟器: 用录制数据驱动任意模块，无需实际游戏

    核心用途:
    1. 调试视觉管线: 用历史截图测试识别准确率
    2. 调试决策引擎: 用历史 game_state 测试决策质量
    3. 回归测试: 修改代码后，用历史数据验证不引入新问题
    4. 策略评估: 在历史数据上评估新策略 vs 旧策略
    """

    def __init__(self, replay_dir: str):
        self.replay = load_replay(replay_dir)
        self.frames = sorted(self.replay.frames, key=lambda f: f.id)

    def replay_vision(self, vision_pipeline):
        """
        回灌视觉管线: 用录制截图测试识别
        """
        results = []
        for frame in self.frames:
            # 用截图跑视觉管线
            predicted_state = vision_pipeline.process_frame(frame.screenshot)

            # 对比录制时的实际状态（如果有的话）
            if frame.game_state:
                diff = compare_states(predicted_state, frame.game_state)
                results.append({
                    "frame_id": frame.id,
                    "match": diff.is_match,
                    "differences": diff.details,
                })

        self.print_vision_report(results)
        return results

    def replay_decisions(self, decision_engine):
        """
        回灌决策引擎: 用录制的 game_state 测试决策
        """
        results = []
        for frame in self.frames:
            if frame.game_state.phase == "planning":
                # 用录制的状态跑决策
                actions = decision_engine.decide(frame.game_state)

                # 对比录制时的实际决策
                if frame.decisions:
                    diff = compare_actions(actions, frame.decisions)
                    results.append({
                        "frame_id": frame.id,
                        "ai_actions": actions,
                        "recorded_actions": frame.decisions,
                        "match": diff.is_match,
                    })

        self.print_decision_report(results)
        return results

    def replay_full_pipeline(self, vision, brain):
        """
        全链路回灌: 截图 → 视觉 → 决策 → 对比
        注意: 不能执行操作（没有实际游戏），只对比决策输出
        """
        for frame in self.frames:
            state = vision.process_frame(frame.screenshot)
            actions = brain.decide(state)
            yield {
                "frame_id": frame.id,
                "state": state,
                "actions": actions,
                "recorded_state": frame.game_state,
                "recorded_actions": frame.decisions,
            }
```

### 11.5 可视化调试工具

```python
# tools/debug_viewer.py
"""
可视化调试器: 在截图上叠加显示识别结果和决策信息

功能:
  - 在截图上绘制 ROI 区域框
  - 标注识别到的棋子名称/星级
  - 标注 OCR 读取的数值
  - 显示决策引擎的操作箭头（拖拽路径）
  - 逐帧回放，支持暂停/跳转

用法:
  python tools/debug_viewer.py --replay data/replays/xxx/
"""

# tools/roi_calibrator.py
"""
ROI 校准工具: 可视化调整归一化坐标

功能:
  - 显示实时截图
  - 鼠标拖拽调整 ROI 区域
  - 实时预览裁剪结果
  - 保存到 ui_regions.json

用法:
  python tools/roi_calibrator.py --device hdc --set 14
"""
```

### 11.6 自动化测试

```python
# tests/test_vision_pipeline.py
"""
视觉管线单元测试（使用 fixtures/ 中的截图）
"""
class TestVisionPipeline:
    def test_ocr_gold(self):
        """测试金币OCR识别"""
        screenshot = load_test_image("fixtures/screenshot_planning.png")
        ocr = GameOCR()
        gold = ocr.read_gold(screenshot)
        assert isinstance(gold, int)
        assert 0 <= gold <= 999

    def test_shop_recognition(self):
        """测试商店棋子识别"""
        screenshot = load_test_image("fixtures/screenshot_planning.png")
        rec = ChampionRecognizer("config/sets/set14/")
        shop = rec.recognize_shop(screenshot)
        assert len(shop) == 5
        for slot in shop:
            assert "name" in slot
            assert "cost" in slot

    def test_phase_detection(self):
        """测试阶段检测"""
        for phase in ["planning", "combat", "augment", "carousel"]:
            screenshot = load_test_image(f"fixtures/screenshot_{phase}.png")
            detector = RoundEventDetector()
            detected = detector.detect(screenshot)
            assert detected == phase

# tests/test_rule_engine.py
"""
规则引擎单元测试
"""
class TestRuleEngine:
    def test_economy_keep_50g(self):
        """测试保持50金利息"""
        state = GameState(gold=52, hp=80, level=6, ...)
        engine = RuleEngine(CompConfig.default())
        actions = engine.decide_economy(state)
        # 52金 > 50金，应该升人口或刷新，不该存着
        assert any(a.type in ("buy_xp", "refresh") for a in actions)

    def test_economy_low_hp_all_in(self):
        """测试低血量全花"""
        state = GameState(gold=35, hp=22, level=7, ...)
        engine = RuleEngine(CompConfig.default())
        actions = engine.decide_economy(state)
        # 22血 < 30血，应该全花
        assert any(a.type == "refresh" for a in actions)

    def test_buy_target_comp_unit(self):
        """测试购买目标阵容棋子"""
        comp = CompConfig(core_units=["薇恩", "盖伦"])
        state = GameState(
            shop=[ShopUnit("薇恩", cost=3, slot=0), ...],
            gold=50, ...
        )
        engine = RuleEngine(comp)
        actions = engine.decide_shop(state)
        assert any(a.type == "buy" and a.slot == 0 for a in actions)
```

### 11.7 实机测试最佳实践

```
每次实机测试的流程:

1. 启动录制
   python src/main.py --record --session-name "test_001"

2. 运行一局游戏（全自动）

3. 查看录制数据
   python tools/debug_viewer.py --replay data/replays/test_001/

4. 定位问题帧
   - 查看 summary.json 找到错误/异常
   - 用 debug_viewer 逐帧回放问题区域

5. 回灌调试
   python tools/test_vision.py --replay data/replays/test_001/
   python tools/test_decisions.py --replay data/replays/test_001/

6. 修复代码 → 回灌验证 → 再次实机测试
```

---

## 12. 日志系统

### 12.1 日志架构

采用分层日志，便于不同场景下快速定位问题：

```
日志级别          用途                              输出位置
─────────────────────────────────────────────────────────────
DEBUG           每帧详细数据（截图hash、ROI裁剪、      console + file
                识别置信度、决策中间步骤）
INFO            关键事件（阶段切换、买人、升星、       console + file
                装备合成、LLM调用）
WARNING         异常但可恢复（识别失败重试、           console + file
                操作超时、状态不确定）
ERROR           严重问题（HDC断连、模型崩溃、          console + file + alert
                无法恢复的异常）
GAME            游戏专用日志（每步决策的人类          data/game_logs/
                可读描述，用于复盘）
```

### 12.2 日志格式

```python
import logging
from rich.logging import RichHandler

def setup_logger(level="INFO", session_id=None):
    """
    配置多层日志
    """
    # 控制台: 彩色简洁输出（开发时看）
    console_handler = RichHandler(
        rich_tracebacks=True,
        markup=True,
        show_path=False,
    )
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(
        "%(message)s", datefmt="[%X]"
    ))

    # 文件: 完整结构化日志（排查问题时看）
    file_handler = logging.FileHandler(
        f"data/logs/{session_id}.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    ))

    # 游戏日志: 人类可读的决策描述（复盘时看）
    game_handler = logging.FileHandler(
        f"data/game_logs/{session_id}_game.log", encoding="utf-8"
    )
    game_handler.setLevel(logging.INFO)
    game_handler.addFilter(GameLogFilter())  # 只保留GAME级别
    game_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(message)s"
    ))

    logger = logging.getLogger("golden_spatula")
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(game_handler)
    return logger
```

### 12.3 游戏可读日志示例

```
# data/game_logs/2026-05-30_game_001_game.log

14:32:01 | 🎮 第1局开始 | 赛季14 | 手机: HUAWEI Mate 60 Pro (1260x2720)
14:32:15 | 📋 进入备战阶段 1-1 | 金币:10 | 血量:100 | 等级:1
14:32:16 | 🛒 商店: [盖伦(1费), 薇恩(3费), 赵信(2费), 卡莎(4费), 莫甘娜(2费)]
14:32:16 | ✅ [L1规则] 购买 盖伦(1费) → 目标阵容核心 | 剩余金币:9
14:32:17 | ✅ [L1规则] 购买 赵信(2费) → 强力过渡 | 剩余金币:7
14:32:18 | 📍 [L2查表] 站位: 盖伦→前排(1,3), 赵信→前排(1,4)
14:32:20 | ⚔️ 战斗开始 1-1 (PvE野怪)
14:32:45 | ✅ 战斗胜利 | 剩余棋子: 盖伦(78%血), 赵信(92%血)
14:32:46 | 🎁 战利品球: 反曲之弓
14:33:01 | 📋 备战 1-2 | 金币:12 | 利息:+1
14:33:02 | 🛒 商店: [盖伦(1费), 盖伦(1费), ...]
14:33:02 | ✅ [L1规则] 购买 盖伦(1费) → 升2星(已有2个) | 剩余金币:11
14:33:03 | ⭐ [L1规则] 合成2星盖伦！
...
14:33:15 | 🧠 [L3 LLM] 阵容规划: 已有3个德玛西亚棋子 → 推荐"德玛西亚战士"阵容
14:33:15 | 🧠 [L3 LLM] 核心: 盖伦/嘉文四世/薇恩 | 目标等级:8
...
15:05:42 | 🏆 游戏结束 | 排名: 第3名 | 击杀: 12 | 存活回合: 32
15:05:42 | 📊 本局统计: L1决策×187 | L2决策×23 | L3决策×5 | 平均延迟:142ms
```

### 12.4 日志过滤与搜索

```bash
# 快速查找问题
grep "ERROR\|WARNING" data/logs/session_001.log

# 查看某回合的所有决策
grep "备战 3-2" data/game_logs/session_001_game.log -A 20

# 统计 LLM 调用次数
grep "L3 LLM" data/game_logs/session_001_game.log | wc -l

# 查看识别失败的情况
grep "识别失败\|confidence.*0\.[0-6]" data/logs/session_001.log
```

---

## 13. 结果报告与统计模块

### 13.1 对局追踪器

```python
class GameTracker:
    """
    追踪每局游戏的关键指标
    """

    def __init__(self):
        self.games = []
        self.current_game = None

    def start_game(self):
        self.current_game = {
            "start_time": time.time(),
            "session_id": generate_id(),
            "set": current_set(),
            "device": device_info(),
            "rounds": [],
            "decisions": {"l1": 0, "l2": 0, "l3": 0},
            "errors": [],
            "vision_latency_ms": [],
            "decision_latency_ms": [],
        }

    def record_round(self, round_id: str, result: dict):
        """记录每回合结果"""
        self.current_game["rounds"].append({
            "round": round_id,
            "hp_change": result.get("hp_change", 0),
            "gold_change": result.get("gold_change", 0),
            "result": result.get("result", "unknown"),  # win/loss/pve
            "board_value": result.get("board_value", 0),
        })

    def end_game(self, rank: int):
        """游戏结束，记录排名和汇总"""
        game = self.current_game
        game["end_time"] = time.time()
        game["rank"] = rank
        game["duration_minutes"] = (game["end_time"] - game["start_time"]) / 60
        game["avg_vision_latency"] = mean(game["vision_latency_ms"])
        game["avg_decision_latency"] = mean(game["decision_latency_ms"])
        self.games.append(game)
        self.current_game = None
```

### 13.2 多局统计报告

```python
class StatsReporter:
    """
    生成多局统计报告
    """

    def generate_report(self, games: list[dict]) -> str:
        """生成人类可读的统计报告"""

        total_games = len(games)
        avg_rank = mean([g["rank"] for g in games])
        top4_rate = sum(1 for g in games if g["rank"] <= 4) / total_games
        avg_duration = mean([g["duration_minutes"] for g in games])

        report = f"""
╔══════════════════════════════════════════╗
║        金铲铲之战 AI Bot 统计报告          ║
╠══════════════════════════════════════════╣
║  对局数:        {total_games:>6}                       ║
║  平均排名:      {avg_rank:>6.1f} / 8                    ║
║  前4率:         {top4_rate:>6.1%}                       ║
║  平均时长:      {avg_duration:>6.1f} 分钟                ║
║                                          ║
║  排名分布:                               ║
║    第1名: {self._count_rank(games, 1):>3} 次 ({self._pct(games, 1):>5.1%})        ║
║    第2名: {self._count_rank(games, 2):>3} 次 ({self._pct(games, 2):>5.1%})        ║
║    第3名: {self._count_rank(games, 3):>3} 次 ({self._pct(games, 3):>5.1%})        ║
║    第4名: {self._count_rank(games, 4):>3} 次 ({self._pct(games, 4):>5.1%})        ║
║    第5-8名: {self._count_rank_range(games, 5, 8):>3} 次 ({self._pct_range(games, 5, 8):>5.1%})      ║
║                                          ║
║  性能指标:                               ║
║    平均视觉延迟: {self._avg_latency(games, 'vision'):>5.0f} ms             ║
║    平均决策延迟: {self._avg_latency(games, 'decision'):>5.0f} ms             ║
║    错误总数:    {self._total_errors(games):>5}                       ║
║    崩溃次数:    {self._total_crashes(games):>5}                       ║
║                                          ║
║  决策统计:                               ║
║    L1规则: {self._total_decisions(games, 'l1'):>6} 次 (平均 {self._avg_decisions(games, 'l1'):>4}/局)   ║
║    L2查表: {self._total_decisions(games, 'l2'):>6} 次 (平均 {self._avg_decisions(games, 'l2'):>4}/局)   ║
║    L3 LLM: {self._total_decisions(games, 'l3'):>6} 次 (平均 {self._avg_decisions(games, 'l3'):>4}/局)   ║
╚══════════════════════════════════════════╝
"""
        return report

    def export_json(self, games: list[dict], path: str):
        """导出机器可读的JSON报告（用于数据分析）"""
        save_json(path, {
            "generated_at": time.time(),
            "total_games": len(games),
            "games": games,
            "summary": {
                "avg_rank": mean([g["rank"] for g in games]),
                "top4_rate": sum(1 for g in games if g["rank"] <= 4) / len(games),
                "avg_duration_min": mean([g["duration_minutes"] for g in games]),
                "total_errors": sum(len(g["errors"]) for g in games),
            }
        })
```

### 13.3 趋势分析

```python
class TrendAnalyzer:
    """
    分析多局数据的趋势变化
    用于判断策略改进是否有效
    """

    def analyze(self, games: list[dict]) -> dict:
        """
        输出:
        - 排名趋势（最近10局 vs 之前）
        - 常见失败原因
        - 经济曲线分析
        - 阵容胜率
        """
        return {
            "rank_trend": self._rank_trend(games),
            "common_failure_reasons": self._failure_analysis(games),
            "economy_curve": self._economy_analysis(games),
            "comp_win_rates": self._comp_analysis(games),
            "recommendations": self._generate_recommendations(games),
        }

    def _failure_analysis(self, games):
        """分析常见失败原因"""
        patterns = {
            "early_elimination": 0,    # 前4回合就被淘汰
            "bad_economy": 0,          # 金币利用率低
            "wrong_items": 0,          # 装备给错人
            "bad_positioning": 0,      # 站位问题
            "vision_errors": 0,        # 视觉识别错误
        }
        for game in games:
            if game["rank"] >= 7:  # 倒数两名
                # 分析该局的回合数据，找出失败模式
                ...
        return patterns
```

### 13.4 报告输出

```bash
# 查看最近10局统计
python tools/report.py --last 10

# 导出完整报告
python tools/report.py --export data/reports/2026-05-30_report.json

# 对比两个版本的策略效果
python tools/report.py --compare session_001 session_002

# 趋势分析 + 改进建议
python tools/report.py --trend --last 50
```

---

## 14. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 棋子识别准确率不足 | 买错/漏买 | 多次截图投票 + VLM兜底 + YOLO训练 |
| 金铲铲手游UI频繁变化 | ROI失效 | 归一化坐标 + ROI校准工具 + 热更新 |
| LLM决策质量不稳定 | 做出错误战略决策 | 低温度采样 + 规则引擎兜底 + 人工校验prompt |
| HDC截屏延迟过高 | 备战阶段操作不完 | Phase 3切换scrcpy推流 |
| 微信登录过期 | 无法自动开局 | 检测登录状态 + 手动重新登录提醒 |
| 反作弊检测 | 封号 | 操作随机延迟+偏移 + 避免高频操作 |
| 赛季大改 | 全部配置失效 | 配置驱动架构 + 赛季迁移工具 |

---

## 15. 成功指标

### 最低目标
- 能自动完成一整局游戏（从开局到结算）
- 平均排名 ≤ 5.0（8人局）
- 单帧处理延迟 < 300ms
- 无操作超时（30秒备战内完成所有操作）

### 进阶目标
- 平均排名 ≤ 4.0（稳定前4，击败初级人类）
- 单帧处理延迟 < 200ms
- 连续运行10局无崩溃
- 新赛季适配 < 2天

### 长期目标
- 平均排名 ≤ 3.0（击败中级人类）
- 支持多手机并行
- 自我策略进化（基于历史对局数据）
